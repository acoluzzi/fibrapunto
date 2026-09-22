"""The domains (§4, §17.1).

    "The agent is not one instruction set. It is a router plus a small number of
    bounded domains, each with its own description, its own reasoning
    instructions and its own explicitly enumerated set of permitted
    capabilities."

    "A design where every instruction applies to every conversation does not
    satisfy this section."

The `capabilities` tuple is the enforcement point, not documentation. The graph
binds exactly these tools to the node for this domain, and
`test_domain_capabilities.py` asserts that no domain can reach a capability it
was not granted. That is how §1's "cancellations handled autonomously — must be
zero" is a structural zero: the cancellation domain is handed three tools, none
of which can execute one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from fibrapunto.prompts.loader import PromptFile, stable_prompt


class DomainKey(StrEnum):
    billing = "billing"
    cancellation = "cancellation"
    modem = "modem"
    fallback = "fallback"


class CapabilityName(StrEnum):
    billing_lookup = "billing_lookup"
    create_handover_record = "create_handover_record"
    transfer_to_human = "transfer_to_human"


@dataclass(frozen=True, slots=True)
class EscalationDefaults:
    """What a handover from this domain looks like (§13).

    Derived from the domain, never chosen by the model. DB CHECK constraints
    enforce the same pairing, so getting here with the wrong combination is
    impossible rather than merely unlikely.
    """

    record_type: str
    priority: str
    queue: str


@dataclass(frozen=True, slots=True)
class Domain:
    key: DomainKey
    enabled: bool
    scope_statement: str
    capabilities: tuple[CapabilityName, ...]
    escalation: EscalationDefaults | None
    always_escalates: bool = False

    @property
    def prompt(self) -> PromptFile:
        return stable_prompt(self.key.value)

    def may_call(self, capability: str) -> bool:
        return capability in {c.value for c in self.capabilities}


# The scope statements are what the router classifies against. §4 is explicit
# that "a domain has a scope statement used for classification" — so these are
# the spec's own words, translated, not a paraphrase written for the classifier.
DOMAINS: dict[DomainKey, Domain] = {
    DomainKey.billing: Domain(
        key=DomainKey.billing,
        enabled=True,
        scope_statement=(
            "Domande su fatture, addebiti mensili, addebiti diretti respinti o "
            "falliti, stato dei pagamenti, e come rimediare a un pagamento non "
            "andato a buon fine. Include le contestazioni di importo e le "
            "richieste di rimborso, che però non vengono risolte qui ma passate "
            "a un collega."
        ),
        capabilities=(
            CapabilityName.billing_lookup,
            CapabilityName.create_handover_record,
            CapabilityName.transfer_to_human,
        ),
        escalation=EscalationDefaults(
            record_type="billing_dispute",
            priority="medium",
            queue="billing_operations",
        ),
    ),
    DomainKey.cancellation: Domain(
        key=DomainKey.cancellation,
        enabled=True,
        scope_statement=(
            "Il cliente vuole disdire, chiudere, cessare o non rinnovare il "
            "contratto, oppure chiede come si fa, oppure chiede se ci sono "
            "penali o vincoli di durata."
        ),
        capabilities=(
            CapabilityName.billing_lookup,
            CapabilityName.create_handover_record,
            CapabilityName.transfer_to_human,
        ),
        escalation=EscalationDefaults(
            record_type="cancellation_request",
            priority="high",
            queue="retention",
        ),
        # §12.1 categorical: "Cancellation. [...] These are not judgement calls
        # and the agent is not permitted to weigh them." Every conversation
        # reaching this domain ends in a handover, decided by code.
        always_escalates=True,
    ),
    DomainKey.fallback: Domain(
        key=DomainKey.fallback,
        enabled=True,
        scope_statement=(
            "Tutto ciò che non rientra nella fatturazione né nelle disdette: "
            "domande di cultura generale, problemi tecnici di linea o modem, "
            "vendite, richieste sui dati di un'altra persona."
        ),
        # One capability. There is nothing in scope to look up (§4.4).
        capabilities=(CapabilityName.transfer_to_human,),
        escalation=None,
    ),
    # §4.3: designed, not built.
    #
    # "Guided troubleshooting requires multi-turn conversational state —
    # remembering which step the customer has already performed, and what
    # happened — and in the time available that state would have cost the two
    # working domains their quality."
    #
    # It is here, disabled, because §16 argues the honest version is worth more
    # than a third half-built domain: enabling it is a flag plus an instruction
    # file, not a redesign.
    DomainKey.modem: Domain(
        key=DomainKey.modem,
        enabled=False,
        scope_statement=(
            "Problemi di linea o di modem: connessione assente o lenta, spie "
            "del modem, guasti di zona."
        ),
        capabilities=(
            CapabilityName.billing_lookup,
            CapabilityName.create_handover_record,
            CapabilityName.transfer_to_human,
        ),
        escalation=EscalationDefaults(
            record_type="technical",
            priority="medium",
            queue="technical_support",
        ),
    ),
}


def enabled_domains() -> dict[DomainKey, Domain]:
    return {key: d for key, d in DOMAINS.items() if d.enabled}


def routable_domains() -> dict[DomainKey, Domain]:
    """Domains the router may choose between.

    Fallback is excluded: it is where a turn lands when nothing else fits, not
    something classified *into*. Including it would let the classifier pick
    "fallback" for a billing question it found confusing, which is the wrong
    failure — §4.4's refusal should be a considered outcome, not a shrug.
    """
    return {k: d for k, d in enabled_domains().items() if k is not DomainKey.fallback}


def get(key: DomainKey) -> Domain:
    return DOMAINS[key]
