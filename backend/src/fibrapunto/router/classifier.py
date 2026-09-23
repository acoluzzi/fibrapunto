"""Domain classification (§4).

A separate structured call rather than a framework handoff, for one reason that
decides it: §1 requires **routing accuracy ≥ 90%, including mid-conversation
switches**, to be measurable. With a classifier, "which domain fired" is a field
in the response — a fact the harness compares against expectation. With handoffs
it would have to be inferred from a transcript by a judge model, which means
measuring the measuring instrument.

§4 also asks for exactly this shape: a mid-conversation switch should be "a
routing decision made by the system, not an `if` buried in a prompt".

The classifier sees the domains' **verbatim scope statements** — §4 says the
scope statement is what classification uses, so it is used unmodified rather
than paraphrased into something more classifier-friendly.
"""

from __future__ import annotations

from typing import Literal

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from fibrapunto.domains.registry import DomainKey, classifiable_domains
from fibrapunto.llm.chat import build_chat_model
from fibrapunto.llm.registry import ModelRole

log = structlog.get_logger(__name__)


class RoutingDecision(BaseModel):
    """What the classifier returns. Every field is consumed by code, not prose."""

    domain: Literal["billing", "cancellation", "fallback"] = Field(
        description=(
            "Il dominio a cui appartiene l'ultimo messaggio del cliente. "
            "Usa 'fallback' solo se non rientra chiaramente negli altri."
        )
    )
    confidence: float = Field(description="Quanto sei sicuro, da 0.0 a 1.0.", ge=0.0, le=1.0)
    explicit_human_request: bool = Field(
        description=(
            "True se il cliente ha chiesto esplicitamente di parlare con una "
            "persona ('mi passi un operatore', 'voglio parlare con qualcuno')."
        )
    )
    sentiment: Literal["neutral", "frustrated", "angry"] = Field(
        description="Il tono del cliente nell'ultimo messaggio."
    )
    rationale: str = Field(
        description="Una frase sul perché. Uso interno: non viene mai mostrata al cliente."
    )


SYSTEM = """\
Sei un classificatore. Leggi l'ultimo messaggio di un cliente e decidi a quale \
ambito appartiene.

Non rispondi al cliente e non dai consigli: produci solo la classificazione.

Gli ambiti disponibili sono questi, con la loro descrizione ufficiale:

{scopes}

Regole:
- Classifica l'ULTIMO messaggio del cliente, usando i messaggi precedenti solo \
come contesto.
- Se il cliente cambia argomento a metà conversazione, segui il cambio: è \
proprio questo il caso che devi cogliere.
- 'fallback' è per ciò che non rientra negli altri ambiti, non per i messaggi \
che ti sembrano ambigui. Se è ambiguo ma plausibilmente di un ambito, scegli \
quello con confidence bassa.
"""


def _scopes_block() -> str:
    """Every scope statement the classifier may choose between, verbatim.

    Fallback is included and comes from the registry like every other domain.
    It used to carry a second, hand-written description here; two descriptions
    of one domain is a drift waiting to happen, and §4 is explicit that "a
    domain has a scope statement used for classification" — singular.

    What keeps fallback from becoming a shrug is the rule in SYSTEM, not its
    absence from this list: an ambiguous message goes to its likeliest domain
    with low confidence, and only a message that fits nowhere goes here. §4.4's
    refusal has to be a considered outcome.
    """
    return "\n\n".join(
        f"### {key.value}\n{domain.scope_statement}"
        for key, domain in classifiable_domains().items()
    )


async def classify(message: str, history: list[str], current_domain: DomainKey) -> RoutingDecision:
    """Classify one turn.

    `history` is the last few turns as plain text — enough for the classifier to
    notice a topic change, not so much that the call stops being cheap.
    """
    model = build_chat_model(ModelRole.router).with_structured_output(RoutingDecision)

    context = "\n".join(f"- {t}" for t in history[-4:]) or "(nessuno)"
    prompt = (
        f"Ambito attuale della conversazione: {current_domain.value}\n\n"
        f"Messaggi precedenti:\n{context}\n\n"
        f"Ultimo messaggio del cliente:\n{message}"
    )

    decision: RoutingDecision = await model.ainvoke(
        [SystemMessage(SYSTEM.format(scopes=_scopes_block())), HumanMessage(prompt)]
    )
    log.info(
        "router.classified",
        domain=decision.domain,
        confidence=decision.confidence,
        sentiment=decision.sentiment,
    )
    return decision
