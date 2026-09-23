"""Load and version the domain instructions.

The instructions live in `.md` files, not in Python. That is the whole point:
**the operator changes a procedure by editing a file, without an engineer and
without a redeploy.** §19 lists "domains as versioned code, defined in reviewable
source" as future work; this is that, arriving early, and it is also tier 1 of
the retrieval decision in docs/DECISIONS.md #12.

Two properties fall out, and both matter downstream:

  * **Attribution.** Every rendered prompt is hashed, and the hash goes into
    every trace record. §14 wants a behaviour change attributable to the change
    that caused it; without this, a metric moving between two eval runs could be
    a prompt edit or a model drift and nobody could tell which.

  * **Cacheability.** The prompt is assembled in two parts. The *stable* part —
    identity plus domain instructions plus tool schemas — is byte-identical
    across every turn of every conversation, so it can be cached at the
    provider and read back at ~0.1x. The *volatile* part — collected state,
    turn constraints — is appended after it and never cached. Getting that
    order wrong makes the cache useless, which is why the split is structural
    here rather than a caller's responsibility.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass(frozen=True, slots=True)
class PromptFile:
    name: str
    version: int
    body: str
    sha: str

    @property
    def short_sha(self) -> str:
        return self.sha[:12]


def _strip(raw: str) -> tuple[dict[str, str], str]:
    """Split frontmatter from body, and drop the HTML comments.

    The comments hold the provenance of each instruction — which section of the
    spec it comes from, and why any expansion was made. They are for the
    reviewer, not the model: sending them would be several hundred tokens of
    commentary the agent has no use for, on every cached prompt.
    """
    meta: dict[str, str] = {}
    match = _FRONTMATTER.match(raw)
    if match:
        for line in match.group(1).splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()
        raw = raw[match.end() :]

    body = _HTML_COMMENT.sub("", raw)
    # Collapse the blank runs the comments leave behind.
    return meta, re.sub(r"\n{3,}", "\n\n", body).strip()


@lru_cache
def load(name: str) -> PromptFile:
    """Load one prompt file by stem, e.g. `domain_billing`."""
    path = PROMPTS_DIR / f"{name}.en.md"
    if not path.exists():
        raise FileNotFoundError(f"No prompt file at {path}")

    meta, body = _strip(path.read_text(encoding="utf-8"))
    return PromptFile(
        name=name,
        version=int(meta.get("version", 0)),
        body=body,
        sha=hashlib.sha256(body.encode("utf-8")).hexdigest(),
    )


def reload_all() -> None:
    """Drop the cache so an edited file takes effect.

    Called by the admin reset endpoint, so an operator can change a procedure
    and see the effect in the next conversation without restarting the service.
    """
    load.cache_clear()
    stable_prompt.cache_clear()


@lru_cache
def stable_prompt(domain_key: str) -> PromptFile:
    """Identity + domain instructions, as one cacheable block.

    This is the part that must not vary between turns. Anything that changes
    per turn — what has been collected, what the escalation state is — is
    appended by the caller *after* this block, never interpolated into it.
    A timestamp or a contract number in here would invalidate the provider
    cache on every single request and quietly triple the input cost.
    """
    identity = load("base_identity")
    domain = load(f"domain_{domain_key}")

    body = f"{identity.body}\n\n---\n\n{domain.body}"
    return PromptFile(
        name=f"stable:{domain_key}",
        version=max(identity.version, domain.version),
        body=body,
        sha=hashlib.sha256(body.encode("utf-8")).hexdigest(),
    )
