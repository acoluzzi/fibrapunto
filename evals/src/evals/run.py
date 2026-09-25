"""The evaluation harness (§14).

    "Nothing in this document is believed because it was designed. Each claim
    has a test."

    "Results are versioned with the code. The interesting artefact is not the
    last run; it is the progression across runs, sitting next to the changes
    that caused it."

So every run writes a directory named for its timestamp and git SHA, containing
the metrics, the per-case verdicts, the transcripts, the judge's disagreements
and a rendered report — all committed.

    make eval                    # everything
    make eval --cases 001,005    # a subset, while iterating
    make eval --no-judge         # deterministic assertions only, free
    make eval-diff               # per-case regressions, previous → latest
    make eval-audit              # where the judge and the code disagreed
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from evals import spend
from evals.assertions import CaseVerdict, evaluate
from evals.driver import Driver
from evals.judge import Judge, JudgeMisconfigured
from evals.metrics import compute, disagreements
from evals.report import render_report, render_review_sample

# evals/src/evals/run.py → evals/
ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = ROOT / "cases"
RUNS_DIR = ROOT / "runs"

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[1m",
    "\033[0m",
)


def load_cases(only: set[str] | None = None) -> list[dict[str, Any]]:
    cases = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        case = yaml.safe_load(path.read_text(encoding="utf-8"))
        if only is None or case["id"] in only:
            cases.append(case)
    return cases


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


async def fetch_completeness(base_url: str) -> dict[str, Any] | None:
    """§1's completeness metric, from the SQL view rather than from our own
    Python — so the number is not computed by the code path that wrote the
    rows."""
    # The operator routes read as platform_admin, so they sit behind the
    # operator password (backend api/auth.py).
    auth = ("operator", os.environ.get("OPERATOR_PASSWORD", ""))
    try:
        async with httpx.AsyncClient(timeout=15.0, auth=auth) as client:
            response = await client.get(f"{base_url}/api/operator/records")
            response.raise_for_status()
            records = response.json()
    except httpx.HTTPError:
        return None

    # A record with no contract link — a customer who asked for a person
    # before giving a number, or a number nobody could find — is created on
    # purpose (§13: "an incomplete record beats no record") and can never
    # carry the fields the view requires of a linked one. Counting it makes
    # the 100% target unreachable by design rather than by a defect, so the
    # ratio is over linked records and the unlinked ones are reported beside
    # it, not hidden.
    linked = [r for r in records if not r.get("contract_link_missing")]
    return {
        "total": len(linked),
        "complete": sum(1 for r in linked if r.get("is_complete")),
        "unlinked": sum(1 for r in records if r.get("contract_link_missing")),
    }


async def main_async(args: argparse.Namespace) -> int:
    only = set(args.cases.split(",")) if args.cases else None
    cases = load_cases(only)
    if not cases:
        print(f"{RED}No cases matched.{RESET}", file=sys.stderr)
        return 1

    # docs/DEVIATIONS.md #3 — stated on every run rather than buried in a file
    # nobody opens. A test set that looks more independent than it is would be
    # a worse failure than one that admits the gap.
    shortfall = [c for c in cases if c["source"] == "author_as_nontechnical"]
    if shortfall:
        print(
            f"{YELLOW}⚠ {len(shortfall)} cases are marked author_as_nontechnical: "
            f"written by me in a non-technical register, not by a non-technical "
            f"person.{RESET}\n  {DIM}§14.1 asks for the second. See "
            f"docs/DEVIATIONS.md #3.{RESET}\n"
        )

    judge = None
    if not args.no_judge:
        try:
            judge = Judge()
            print(f"{DIM}judge: {judge.model}{RESET}")
        except JudgeMisconfigured as exc:
            print(f"{RED}{exc}{RESET}", file=sys.stderr)
            return 2

    print(f"{BOLD}{len(cases)} cases{RESET}  {DIM}concurrency {args.concurrency}{RESET}\n")

    # §16 has an argument about cost and the build had no number behind it.
    # Read from outside, before anything is spent.
    balance_before = await spend.balance()

    driver = Driver(args.base_url)
    transcripts = await driver.run_all(cases, concurrency=args.concurrency)
    by_id = {t.case_id: t for t in transcripts}

    verdicts: list[CaseVerdict] = []
    judged: dict[str, dict] = {}

    for case in cases:
        transcript = by_id[case["id"]]
        if transcript.error:
            verdict = CaseVerdict(case["id"], case["title"], case["source"], passed=False)
            verdict.fail(transcript.error)
            verdicts.append(verdict)
            print(f"  {RED}✗{RESET} {case['id']} {case['title']}\n      {transcript.error}")
            continue

        verdict = evaluate(case, transcript.replies, transcript.trace)
        verdicts.append(verdict)

        mark = f"{GREEN}✓{RESET}" if verdict.passed else f"{RED}✗{RESET}"
        print(f"  {mark} {case['id']} {case['title']}")
        for failure in verdict.failures:
            print(f"      {DIM}{failure}{RESET}")

        if judge:
            try:
                judged[case["id"]] = await judge.grade(case, transcript.replies)
            except Exception as exc:  # noqa: BLE001
                judged[case["id"]] = {"error": str(exc)}

    spend_report = spend.report(balance_before, await spend.balance(), len(cases))

    completeness = await fetch_completeness(args.base_url)
    metrics = compute(verdicts, completeness)
    audit = disagreements(verdicts, judged)

    # --- the §1 table --------------------------------------------------------
    print(f"\n{BOLD}§1 metrics{RESET}")
    for metric in metrics:
        if metric.passed is None:
            mark, colour = "—", DIM
        elif metric.passed:
            mark, colour = "✓", GREEN
        else:
            mark, colour = "✗", RED
        print(
            f"  {colour}{mark}{RESET} {metric.name:<28} {metric.display:>6}  "
            f"{DIM}(target {metric.target}){RESET}"
        )
        if metric.detail:
            print(f"      {DIM}{metric.detail}{RESET}")

    print(f"\n{DIM}{spend.format_report(spend_report, len(cases))}{RESET}")

    if audit:
        print(
            f"\n{YELLOW}{len(audit)} disagreements between assertions and judge{RESET} "
            f"{DIM}— make eval-audit{RESET}"
        )

    # --- write the run -------------------------------------------------------
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    run_id = f"{stamp}-{git_sha()}"
    run_dir = RUNS_DIR / run_id
    (run_dir / "transcripts").mkdir(parents=True, exist_ok=True)

    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "git_sha": git_sha(),
                "agent_model": os.environ.get("MODEL_AGENT"),
                "judge_model": judge.model if judge else None,
                "case_count": len(cases),
                "started_at": stamp,
                # §16's cost argument, with a number under it at last.
                "spend": spend_report,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "results.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "case_id": v.case_id,
                    "title": v.title,
                    "source": v.source,
                    "passed": v.passed,
                    "failures": v.failures,
                    "domain_correct": v.domain_correct,
                    "invented_data": v.invented_data,
                    "claimed_cancellation": v.claimed_cancellation,
                    "handover_reference": v.handover_reference,
                    "judge": judged.get(v.case_id),
                },
                ensure_ascii=False,
            )
            for v in verdicts
        ),
        encoding="utf-8",
    )
    (run_dir / "metrics.json").write_text(
        json.dumps(
            [
                {"name": m.name, "value": m.value, "target": m.target, "passed": m.passed}
                for m in metrics
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "audit.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "report.md").write_text(
        render_report(run_id, metrics, verdicts, audit), encoding="utf-8"
    )
    (run_dir / "review_sample.md").write_text(
        render_review_sample(verdicts, by_id), encoding="utf-8"
    )

    for transcript in transcripts:
        (run_dir / "transcripts" / f"{transcript.case_id}.json").write_text(
            json.dumps(
                {"replies": transcript.replies, "trace": transcript.trace},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    _append_history(metrics, run_id)

    print(f"\n{DIM}written to evals/runs/{run_id}/{RESET}")
    failed = sum(1 for v in verdicts if not v.passed)
    print(f"{BOLD}{len(verdicts) - failed}/{len(verdicts)} cases passed{RESET}\n")
    return 1 if failed else 0


def _append_history(metrics, run_id: str) -> None:
    """One row per run. §14's "progression across runs" needs a shape a chart
    can read, and this is it."""
    history = RUNS_DIR / "history.csv"
    names = [m.name for m in metrics]
    if not history.exists():
        history.write_text("run_id," + ",".join(names) + "\n", encoding="utf-8")
    values = ",".join("" if m.value is None else str(m.value) for m in metrics)
    with history.open("a", encoding="utf-8") as handle:
        handle.write(f"{run_id},{values}\n")


def _resolve_run(name: str) -> Path:
    """Accept a full run id, a unique prefix, or `latest`/`previous`."""
    runs = sorted(p for p in RUNS_DIR.iterdir() if p.is_dir()) if RUNS_DIR.exists() else []
    if not runs:
        raise SystemExit("No runs yet.")
    if name in ("latest", "last"):
        return runs[-1]
    if name in ("previous", "prev"):
        if len(runs) < 2:
            raise SystemExit("Only one run exists; nothing to compare against.")
        return runs[-2]
    matches = [p for p in runs if p.name.startswith(name)]
    if not matches:
        raise SystemExit(f"No run matching {name!r}. Have: {[p.name for p in runs]}")
    if len(matches) > 1:
        raise SystemExit(f"{name!r} is ambiguous: {[p.name for p in matches]}")
    return matches[0]


def _read_results(run: Path) -> dict[str, dict]:
    text = (run / "results.jsonl").read_text(encoding="utf-8").strip()
    return {row["case_id"]: row for row in (json.loads(line) for line in text.splitlines() if line)}


def diff_command(a: str, b: str) -> int:
    """Per-case regressions between two runs (§14).

        "The interesting artefact is not the last run; it is the progression
        across runs, sitting next to the changes that caused it."

    A regression is reported **per case**, not as a delta in the headline
    number. Two cases breaking while two others start passing leaves every §1
    metric identical and is not a neutral event — it is four changes in
    behaviour, and a summary that hides them is the reason prompt tuning goes
    round in circles.
    """
    run_a, run_b = _resolve_run(a), _resolve_run(b)
    results_a, results_b = _read_results(run_a), _read_results(run_b)

    print(f"{DIM}a = {run_a.name}{RESET}")
    print(f"{DIM}b = {run_b.name}{RESET}\n")

    regressed, fixed, still_failing = [], [], []
    for case_id in sorted(results_a.keys() | results_b.keys()):
        before, after = results_a.get(case_id), results_b.get(case_id)
        if before is None:
            print(f"  {DIM}+ {case_id} {after['title']} — new in b{RESET}")
            continue
        if after is None:
            print(f"  {DIM}- {case_id} {before['title']} — absent from b{RESET}")
            continue
        if before["passed"] and not after["passed"]:
            regressed.append(after)
        elif not before["passed"] and after["passed"]:
            fixed.append(after)
        elif not after["passed"]:
            still_failing.append(after)

    for row in regressed:
        print(f"  {RED}✗ REGRESSION{RESET} {row['case_id']} {row['title']}")
        for failure in row["failures"]:
            print(f"      {DIM}{failure}{RESET}")
    for row in fixed:
        print(f"  {GREEN}✓ FIXED{RESET}       {row['case_id']} {row['title']}")
    if still_failing:
        print(f"\n  {DIM}{len(still_failing)} cases already failing in both{RESET}")

    # --- the §1 table, side by side -----------------------------------------
    metrics_a = {m["name"]: m for m in json.loads((run_a / "metrics.json").read_text("utf-8"))}
    metrics_b = {m["name"]: m for m in json.loads((run_b / "metrics.json").read_text("utf-8"))}
    print(f"\n{BOLD}§1 metrics{RESET}")
    for name, after in metrics_b.items():
        before = metrics_a.get(name, {})
        va, vb = before.get("value"), after.get("value")
        if va is None or vb is None:
            arrow, colour = "—", DIM
        elif vb > va:
            arrow, colour = "↑", GREEN
        elif vb < va:
            arrow, colour = "↓", RED
        else:
            arrow, colour = "=", DIM
        fa = "—" if va is None else f"{va:g}"
        fb = "—" if vb is None else f"{vb:g}"
        print(f"  {colour}{arrow}{RESET} {name:<28} {fa:>6} → {fb:>6}")

    if not regressed and not fixed:
        print(f"\n{DIM}No per-case changes.{RESET}")
    else:
        print(f"\n{BOLD}{len(regressed)} regressions, {len(fixed)} fixed{RESET}")

    # A regression fails the command even when the headline numbers improved.
    return 1 if regressed else 0


def audit_command() -> int:
    """Show where the judge and the deterministic assertions disagreed."""
    runs = sorted(p for p in RUNS_DIR.iterdir() if p.is_dir()) if RUNS_DIR.exists() else []
    if not runs:
        print("No runs yet.", file=sys.stderr)
        return 1

    audit = json.loads((runs[-1] / "audit.json").read_text(encoding="utf-8"))
    if not audit:
        print(f"{GREEN}No disagreements in {runs[-1].name}.{RESET}")
        return 0

    print(f"{BOLD}{len(audit)} disagreements in {runs[-1].name}{RESET}")
    print(f"{DIM}§14.2: the sample read by hand is the audit of the judge.{RESET}\n")
    for row in audit:
        print(f"  {row['case_id']}  {row['field']}")
        print(f"      code={row['deterministic']}  judge={row['judge']}")
        print(f"      {DIM}{row['judge_rationale']}{RESET}\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="run", choices=["run", "audit", "diff"])
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--cases", help="comma-separated case ids")
    parser.add_argument("--a", default="previous", help="diff: baseline run id or prefix")
    parser.add_argument("--b", default="latest", help="diff: run id, prefix, or latest")
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="deterministic assertions only — free, and enough while iterating",
    )
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()

    if args.command == "audit":
        return audit_command()
    if args.command == "diff":
        return diff_command(args.a, args.b)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
