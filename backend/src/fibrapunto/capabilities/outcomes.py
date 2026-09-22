"""Typed capability outcomes (§5, §5.1, §17.4, §17.6).

    "No capability may raise an untyped error into the reasoning layer. Every
    failure must arrive as a named outcome the instructions can refer to. This
    is the single most important implementation rule in the document, and it is
    not a style preference."

Two design choices worth defending out loud:

1. **Every failure outcome carries its own `guidance`.** §5.2 gives the verbatim
   description for `notFound` — "tell the customer plainly that you cannot find
   the contract, ask them to check it, and offer a human. Do not invent contract
   data." That instruction is delivered here, in the payload, rather than in a
   system prompt the model read three thousand tokens earlier. §5.1's argument is
   that a named outcome makes the relevant instruction apply "literally and
   unambiguously"; putting the instruction *next to* the failure makes it apply
   adjacently too.

2. **`rejected` is a deliberate addition to §5's enumeration.** §5.2 requires the
   contract number to "come from the customer verbatim". That has to be enforced
   in code, and enforcement must return something typed — folding it into
   `notFound` would be the agent reporting what was supposed to happen rather
   than what did, which is the §12.4 principle inverted. Recorded in
   docs/DEVIATIONS.md rather than hidden.

Guidance text is Italian because the whole reasoning layer runs in Italian
(§10 criterion 3): a prompt written in English with "reply in Italian" appended
drifts into English technical vocabulary, and these strings are prompt.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from fibrapunto.capabilities.base import CustomerVisible, InternalOnly


class ContractMatch(BaseModel):
    """One candidate contract when the legacy system returns namesakes."""

    contract_number: str = CustomerVisible("Numero del contratto trovato.")
    service_address: str = CustomerVisible(
        "Indirizzo di attivazione. È l'unico criterio ammesso per far scegliere "
        "al cliente fra più contratti: mai il nome, mai il cognome."
    )


# --- billing lookup ----------------------------------------------------------


class BillingFound(BaseModel):
    """The lookup succeeded. These are the only billing figures that may ever
    appear in a reply — the grounding guard rejects any other."""

    outcome: Literal["found"] = "found"

    status: Literal["paid", "failed", "pending"] = CustomerVisible(
        "Stato dell'addebito del mese in corso."
    )
    failure_reason: str | None = CustomerVisible(
        "Motivo del rifiuto dell'addebito, già in italiano piano. "
        "Riferirlo al cliente senza gergo bancario e senza codici."
    )
    amount: Decimal | None = CustomerVisible(
        "Importo dell'addebito in euro. Scriverlo all'italiana: 29,90 €."
    )
    invoice_month: str | None = CustomerVisible("Mese di fatturazione, formato YYYY-MM.")
    next_retry_date: date | None = CustomerVisible(
        "Data del prossimo tentativo di addebito. Comunicarla quando un addebito è stato respinto."
    )
    payment_link: str | None = CustomerVisible(
        "Link sicuro per pagare subito con carta. È l'alternativa da offrire "
        "al cliente: non chiedere mai i dati della carta in chat."
    )
    lock_in_until: date | None = CustomerVisible(
        "Fine del vincolo contrattuale. Se è nullo NON tacere: dire "
        "esplicitamente che non risultano vincoli."
    )
    remaining_instalments: int | None = CustomerVisible(
        "Rate del modem ancora da pagare. Zero significa nessuna rata residua."
    )

    source_system: str = InternalOnly("Sistema di provenienza del dato. Mai citarlo al cliente.")
    fetched_at: str = InternalOnly("Quando il dato è stato letto. Uso interno.")


class BillingNotFound(BaseModel):
    """No contract with this number exists in billing.

    Reached both by a mistyped number and by the contract that exists in the CRM
    and not in billing (§6.1) — the agent cannot tell the two apart, which is
    exactly why it must not guess.
    """

    outcome: Literal["notFound"] = "notFound"
    guidance: str = InternalOnly(
        "Istruzione operativa per il livello di ragionamento. Mai mostrarla al cliente.",
        default=(
            "Nessun contratto risulta con questo numero. Dillo chiaramente al "
            "cliente, chiedigli di verificare il numero, e offri l'apertura di "
            "una pratica con un operatore. Non inventare NESSUN dato di "
            "contratto: né importi, né date, né stato."
        ),
    )


class BillingServiceUnavailable(BaseModel):
    """The billing system did not answer, or answered something that does not
    match its own published contract.

    §15: "If the capability leaks an exception instead of a typed outcome, the
    model fills the gap. This is why §5.1 exists."
    """

    outcome: Literal["serviceUnavailable"] = "serviceUnavailable"
    guidance: str = InternalOnly(
        "Istruzione operativa per il livello di ragionamento. Mai mostrarla al cliente.",
        default=(
            "Il sistema di fatturazione non risponde. Dillo chiaramente al "
            "cliente, NON fornire nessun importo e nessuna data, e offri di "
            "aprire una pratica. Non promettere tempi che non dipendono da te."
        ),
    )
    incident_id: str = InternalOnly(
        "Identificativo per correlare con il log tecnico. Mai mostrarlo al cliente."
    )


class BillingMultipleMatches(BaseModel):
    """More than one contract matched. §4.1 instruction 3: never pick the first."""

    outcome: Literal["multipleMatches"] = "multipleMatches"
    matches: list[ContractMatch] = CustomerVisible(
        "Contratti trovati. Chiedi al cliente quale sia il suo indicando "
        "l'indirizzo di attivazione. Non scegliere mai tu, e non usare il nome."
    )
    guidance: str = InternalOnly(
        "Istruzione operativa per il livello di ragionamento. Mai mostrarla al cliente.",
        default=(
            "Se dopo una domanda di chiarimento il cliente non identifica il "
            "contratto, non insistere: offri il passaggio a un operatore."
        ),
    )


class BillingRejected(BaseModel):
    """The call was refused before it left the process — a precondition failed.

    Not in §5's enumeration; see the module docstring and docs/DEVIATIONS.md.
    """

    outcome: Literal["rejected"] = "rejected"
    reason: Literal["contract_number_not_from_customer", "format_invalid"] = InternalOnly(
        "Perché la chiamata è stata rifiutata. Uso interno."
    )
    guidance: str = InternalOnly(
        "Istruzione operativa per il livello di ragionamento. Mai mostrarla al cliente.",
        default=(
            "Non hai un numero di contratto valido fornito dal cliente. "
            "Chiedilo, nel formato FP-YYYY-NNNNNN. Se il formato è sbagliato "
            "una seconda volta, offri un operatore invece di insistere."
        ),
    )


BillingLookupResult = Annotated[
    BillingFound
    | BillingNotFound
    | BillingServiceUnavailable
    | BillingMultipleMatches
    | BillingRejected,
    Field(discriminator="outcome"),
]

# Every outcome literal across every capability. guardrails/output_visibility.py
# builds its blocklist from this, so a new outcome is covered the moment it is
# declared rather than when someone remembers to update a list.
BILLING_OUTCOMES: frozenset[str] = frozenset(
    {"found", "notFound", "serviceUnavailable", "multipleMatches", "rejected"}
)


# --- create handover record (§5, §13) ----------------------------------------


class HandoverCreated(BaseModel):
    """The record exists and carries a reference the customer can be given."""

    outcome: Literal["created"] = "created"
    reference: str = CustomerVisible(
        "Numero della pratica, assegnato dal sistema. Comunicalo al cliente esattamente così com'è."
    )
    queue_label: str = CustomerVisible(
        "Il team che la prende in carico, in parole comprensibili al cliente."
    )
    response_window: str = CustomerVisible(
        "Entro quando il cliente verrà ricontattato. Non promettere tempi diversi."
    )


class HandoverCreatedWithoutContractLink(BaseModel):
    """§13: "When the contract cannot be linked. The record is created anyway,
    without the link, carrying an explicit note that the contract was not found.
    An incomplete record beats no record: the customer has still asked for
    something, and the company has still been told."
    """

    outcome: Literal["createdWithoutContractLink"] = "createdWithoutContractLink"
    reference: str = CustomerVisible("Numero della pratica.")
    queue_label: str = CustomerVisible("Il team che la prende in carico.")
    response_window: str = CustomerVisible("Entro quando verrà ricontattato.")
    guidance: str = InternalOnly(
        "Istruzione operativa per il livello di ragionamento.",
        default=(
            "La pratica è stata aperta ma non è stato possibile collegarla a un "
            "contratto. Dillo al cliente: gli serve sapere che un collega gli "
            "chiederà di verificare il numero. Non fingere che sia tutto a posto."
        ),
    )


class HandoverFailed(BaseModel):
    """§12.4: "The agent must not state a reference number it did not receive.

    It says plainly that it could not register the request, gives the
    alternative channel, and does not claim that anything has been recorded."

    There is no `reference` field on this model. Not empty, not None — absent,
    so there is nothing for the reasoning layer to mistake for one.
    """

    outcome: Literal["failed"] = "failed"
    missing_fields: list[str] = InternalOnly(
        "Campi obbligatori non raccolti, se è questo il motivo del fallimento.",
        default_factory=list,
    )
    guidance: str = InternalOnly(
        "Istruzione operativa per il livello di ragionamento.",
        default=(
            "NON è stato possibile registrare la richiesta. Dillo chiaramente al "
            "cliente. NON dare nessun numero di pratica: non ne esiste uno. "
            "Indica il canale alternativo (il numero verde o l'area clienti) e "
            "non dire che qualcosa è stato preso in carico."
        ),
    )
    incident_id: str = InternalOnly(
        "Identificativo per il log tecnico. Mai mostrarlo al cliente.",
        default="",
    )


HandoverResult = Annotated[
    HandoverCreated | HandoverCreatedWithoutContractLink | HandoverFailed,
    Field(discriminator="outcome"),
]

HANDOVER_OUTCOMES: frozenset[str] = frozenset({"created", "createdWithoutContractLink", "failed"})
