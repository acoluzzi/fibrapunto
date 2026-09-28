"""Record a run where the dashboard can read it.

The harness writes each run to evals/runs/ — the versioned, committed artefact
§14 asks for. The dashboard cannot read that directory: the backend runs in a
container, and the harness is an external program on the host by design
(§14.2). So the last thing a run does is hand its §1 table to the backend over
the same operator API the completeness metric was read through, and the
backend keeps it in `platform.eval_run`, next to the judge-vendor constraint
that was waiting for a writer (docs/DECISIONS.md #11).

Best effort, deliberately: the run directory is the record of truth, and a
dashboard that could not be reached must not fail a run that cost money. The
run can be published later with `make eval-publish RUN=<id>`.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


def dataset_sha(paths: list[Path]) -> str:
    """One hash over the whole test set, so two runs can be told apart by
    what they were run against, not only by when."""
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:12]


# evals/src/evals/publish.py → the repository root, where .env lives.
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def _from_env_file(name: str) -> str:
    """One value from the repository's .env, for a shell that did not export it.

    The backend reads .env through its settings; the harness is an external
    program and read only the process environment, so `make eval-publish`
    from a fresh terminal was refused at the operator gate while `make eval`
    from a terminal that had sourced .env went through. Same file, same
    answer, whichever terminal.
    """
    try:
        lines = _ENV_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() == name:
            return value.strip().strip("'\"")
    return ""


def operator_auth() -> tuple[str, str]:
    """The operator routes read and write as platform_admin, so they sit
    behind the operator password (backend api/auth.py)."""
    password = os.environ.get("OPERATOR_PASSWORD") or _from_env_file("OPERATOR_PASSWORD")
    return ("operator", password)


async def fetch_prompt_sha(base_url: str) -> tuple[str | None, dict[str, str]]:
    """The instruction files the agent ran with, as the backend reports them.

    Every reply already carries the prompt sha of its domain; this is the same
    attribution at run level (prompts/loader.py): one hash over every enabled
    domain's, so a metric that moved between two runs can be traced to a
    prompt edit — or cleared of one.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{base_url}/api/domains")
            response.raise_for_status()
            domains = response.json()
    except (httpx.HTTPError, ValueError):
        return None, {}
    shas = {d["key"]: d["prompt_sha"] for d in domains if d.get("prompt_sha")}
    if not shas:
        return None, {}
    joined = ",".join(f"{key}={shas[key]}" for key in sorted(shas))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12], shas


def payload_for(run_dir: Path) -> dict[str, Any]:
    """What the backend records, read back from the run directory.

    Built from the files rather than from the in-memory run so that `publish`
    after the fact sends exactly what `make eval` would have sent.
    """
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    started = meta.get("started_at") or run_dir.name[:20]
    try:
        started_at = datetime.strptime(started, "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=UTC)
    except ValueError:
        started_at = None
    return {
        "run_id": meta.get("run_id") or run_dir.name,
        "git_sha": meta.get("git_sha"),
        "agent_model": meta.get("agent_model") or "unknown",
        "judge_model": meta.get("judge_model"),
        # Absent from runs made before the harness recorded them: left null,
        # not computed today over files that may have changed since.
        "dataset_sha": meta.get("dataset_sha"),
        "prompt_sha": meta.get("prompt_sha"),
        "case_count": meta.get("case_count"),
        "started_at": started_at.isoformat() if started_at else None,
        "metrics": [
            {
                "name": m["name"],
                "value": m.get("value"),
                "target": m.get("target", ""),
                "passed": m.get("passed"),
            }
            for m in metrics
        ],
    }


async def publish(base_url: str, run_dir: Path) -> str | None:
    """Record one run. Returns None on success, or the reason it could not."""
    payload = payload_for(run_dir)
    try:
        async with httpx.AsyncClient(timeout=15.0, auth=operator_auth()) as client:
            response = await client.post(f"{base_url}/api/operator/eval-runs", json=payload)
    except httpx.HTTPError as exc:
        return f"backend not reachable at {base_url}: {exc}"
    if response.status_code == 401:
        return "operator login refused: is OPERATOR_PASSWORD set in .env?"
    if response.is_error:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text
        return f"backend answered {response.status_code}: {detail}"
    return None
