"""Model-provider smoke test (§10). Run this before trusting anything downstream.

Four things are verified, in order of how expensive they are to discover late:

  1. A tool call round-trips. If this fails, no capability works and the typed
     outcome contract (§5.1) has nothing to attach to.
  2. Structured output round-trips. The router (§4) depends on it.
  3. **The model that answered is the model that was asked for.** OpenRouter
     routes across providers; without `allow_fallbacks: false` it may serve a
     different one under load, and a "pinned" evaluation run that silently
     changed model is worse than no pin at all (§10, §14).
  4. The judge is not the subject (§14.2, §10).

Run: make smoke
"""

from __future__ import annotations

import sys

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from fibrapunto.llm.chat import build_chat_model, served_model
from fibrapunto.llm.registry import ModelRole, get_registry
from fibrapunto.settings import get_settings

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def ok(message: str) -> None:
    print(f"  {GREEN}✓{RESET} {message}")


def fail(message: str) -> None:
    print(f"  {RED}✗{RESET} {message}")


def note(message: str) -> None:
    print(f"    {DIM}{message}{RESET}")


# --- 1. tool calling ---------------------------------------------------------


@tool
def look_up_contract(contract_number: str) -> str:
    """Look up the billing status of a contract.

    Args:
        contract_number: The contract number, format FP-YYYY-NNNNNN.
    """
    return "status: refused"


def check_tool_call(role: ModelRole) -> bool:
    spec = get_registry()[role]
    print(f"\n{YELLOW}1. Tool calling{RESET}  {DIM}({spec.id}){RESET}")
    print(f"    {DIM}max_tokens={spec.max_tokens}{RESET}")
    try:
        response = (
            build_chat_model(role)
            .bind_tools([look_up_contract])
            .invoke([HumanMessage("Check contract FP-2023-048812. Use the tool available to you.")])
        )
    except Exception as exc:
        fail(f"request failed: {type(exc).__name__}: {exc}")
        return False

    calls = getattr(response, "tool_calls", []) or []
    if not calls:
        fail("the model returned no tool call")
        note(f"content was: {response.content!r}")
        return False

    ok(f"tool call returned: {calls[0]['name']}({calls[0]['args']})")
    if calls[0]["args"].get("contract_number") != "FP-2023-048812":
        fail("the contract number was not extracted verbatim")
        return False
    ok("argument extracted verbatim from the customer's text (§5.2)")
    return True


# --- 2. structured output ----------------------------------------------------


class RoutingDecision(BaseModel):
    """Stands in for the real router's schema (§4)."""

    dominio: str = Field(description="billing | cancellation | fallback")
    confidenza: float = Field(description="0.0 - 1.0")


def check_structured_output(role: ModelRole) -> bool:
    spec = get_registry()[role]
    print(f"\n{YELLOW}2. Structured output{RESET}  {DIM}({spec.id}){RESET}")
    print(f"    {DIM}max_tokens={spec.max_tokens}{RESET}")
    try:
        result = (
            build_chat_model(role)
            .with_structured_output(RoutingDecision)
            .invoke(
                [
                    HumanMessage(
                        "Classify: 'I want to cancel my contract, I have moved house'. "
                        "Answer with the domain and a confidence."
                    )
                ]
            )
        )
    except Exception as exc:
        fail(f"request failed: {type(exc).__name__}: {exc}")
        return False

    ok(f"parsed: dominio={result.dominio!r} confidenza={result.confidenza}")
    if "cancel" not in result.dominio.lower():
        note(f"{YELLOW}routed to {result.dominio!r}; expected cancellation{RESET}")
    return True


# --- 3. the pin (§10) --------------------------------------------------------


def check_model_pin(role: ModelRole) -> bool:
    spec = get_registry()[role]
    print(f"\n{YELLOW}3. Version pinning{RESET}  {DIM}({spec.id}){RESET}")
    print(f"    {DIM}max_tokens={spec.max_tokens}{RESET}")
    try:
        response = build_chat_model(role).invoke([HumanMessage("Reply with: OK")])
    except Exception as exc:
        fail(f"request failed: {type(exc).__name__}: {exc}")
        return False

    actual = served_model(response)
    if actual is None:
        fail("OpenRouter did not report which model served the request")
        note("the pin cannot be verified — treat every eval run as unpinned")
        return False

    if actual != spec.id:
        fail(f"asked for {spec.id}, was served {actual}")
        note("allow_fallbacks is false, so this should not happen — investigate")
        return False

    ok(f"served by the model that was requested: {actual}")
    return True


# --- 4. judge is not the subject (§14.2) -------------------------------------


def check_judge_separation() -> bool:
    print(f"\n{YELLOW}4. Separation of judge and subject{RESET}")
    settings = get_settings()
    agent, judge = settings.model_agent, settings.model_judge

    if agent == judge:
        fail(f"judge and agent are the same model ({agent})")
        note("§10: grading one's own output is not measurement")
        return False

    agent_vendor, judge_vendor = agent.split("/")[0], judge.split("/")[0]
    if agent_vendor == judge_vendor:
        fail(f"judge and agent share a vendor ({agent_vendor})")
        note("two checkpoints of one family share failure modes")
        return False

    ok(f"agent {agent_vendor}/… judged by {judge_vendor}/… — different vendors")
    return True


def main() -> int:
    settings = get_settings()

    if not settings.openrouter_api_key.get_secret_value():
        print(f"\n{RED}OPENROUTER_API_KEY is not set.{RESET}")
        print("  Add it to .env — see .env.example. Get one at https://openrouter.ai/keys\n")
        return 2

    print(f"\n{DIM}Smoke-testing the model provider before trusting anything downstream.{RESET}")

    results = [
        check_judge_separation(),
        check_tool_call(ModelRole.agent),
        check_structured_output(ModelRole.router),
        check_model_pin(ModelRole.agent),
    ]

    print()
    if all(results):
        print(f"{GREEN}All checks passed.{RESET} The reasoning layer can be built on this.\n")
        return 0

    print(f"{RED}{results.count(False)} of {len(results)} checks failed.{RESET}")
    print(f"{DIM}Do not build on top of this until they pass — see the plan's day-2 gate.{RESET}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
