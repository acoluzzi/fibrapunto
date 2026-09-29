"""What the harness hands the backend for the dashboard (§14).

Built from the run directory, not from the run in memory, so `make
eval-publish` after the fact sends exactly what `make eval` would have. No
network here: the payload is the contract, and the backend's side of it is
tested in backend/tests/test_eval_runs.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from evals import publish


def _write_run(root: Path, name: str, meta: dict, metrics: list[dict]) -> Path:
    run_dir = root / name
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    return run_dir


METRICS = [
    {"name": "Autonomous resolution", "value": 1.0, "target": "≥ 70%", "passed": True},
    {"name": "No invention", "value": 0, "target": "= 0", "passed": True},
    {"name": "Escalation precision", "value": None, "target": "≥ 90%", "passed": None},
]


def test_payload_is_the_run_directory_verbatim(tmp_path):
    run_dir = _write_run(
        tmp_path,
        "2026-09-25T15-03-48Z-bafb1b6",
        {
            "run_id": "2026-09-25T15-03-48Z-bafb1b6",
            "git_sha": "bafb1b6",
            "agent_model": "anthropic/claude-sonnet-5",
            "judge_model": "google/gemini-2.5-pro",
            "case_count": 30,
            "started_at": "2026-09-25T15-03-48Z",
            "dataset_sha": "d" * 12,
            "prompt_sha": "p" * 12,
        },
        METRICS,
    )
    payload = publish.payload_for(run_dir)
    assert payload["run_id"] == "2026-09-25T15-03-48Z-bafb1b6"
    assert payload["agent_model"] == "anthropic/claude-sonnet-5"
    assert payload["judge_model"] == "google/gemini-2.5-pro"
    assert payload["case_count"] == 30
    assert payload["dataset_sha"] == "d" * 12
    # The run's own timestamp, made unambiguous (UTC) for the database.
    assert payload["started_at"] == "2026-09-25T15:03:48+00:00"
    assert payload["metrics"] == METRICS


def test_a_run_made_before_recording_existed_publishes_with_what_it_has(tmp_path):
    """The runs already in evals/runs/ have no dataset or prompt sha, and one
    of them was made with no judge. They are published as they are: null is
    the truthful value, not a sha computed today over a test set that may
    have changed since."""
    run_dir = _write_run(
        tmp_path,
        "2026-09-25T13-09-08Z-89ff5a1",
        {
            "run_id": "2026-09-25T13-09-08Z-89ff5a1",
            "git_sha": "89ff5a1",
            "agent_model": "anthropic/claude-sonnet-5",
            "judge_model": None,
            "case_count": 32,
            "started_at": "2026-09-25T13-09-08Z",
        },
        METRICS,
    )
    payload = publish.payload_for(run_dir)
    assert payload["judge_model"] is None
    assert payload["dataset_sha"] is None
    assert payload["prompt_sha"] is None
    assert payload["started_at"] == "2026-09-25T13:09:08+00:00"


def test_dataset_sha_depends_on_content_and_names_not_on_order(tmp_path):
    a = tmp_path / "001-a.yaml"
    b = tmp_path / "002-b.yaml"
    a.write_text("id: '001'\n", encoding="utf-8")
    b.write_text("id: '002'\n", encoding="utf-8")
    sha = publish.dataset_sha([a, b])
    assert sha == publish.dataset_sha([b, a])
    assert len(sha) == 12

    b.write_text("id: '002'\ntitle: edited\n", encoding="utf-8")
    assert publish.dataset_sha([a, b]) != sha


def test_a_reviewed_run_publishes_its_escalation_precision(tmp_path):
    """§1's escalation precision is a person's verdict, not the harness's.
    metrics.json leaves it empty; reviewed.yaml, once written, fills it."""
    run_dir = _write_run(
        tmp_path,
        "2026-09-25T15-57-02Z-f1794c5",
        {"run_id": "2026-09-25T15-57-02Z-f1794c5", "agent_model": "anthropic/claude-sonnet-5"},
        METRICS,
    )
    (run_dir / "reviewed.yaml").write_text(
        "sample_size: 10\nwarranted: 10\nprecision: 1.0\ncases: []\n", encoding="utf-8"
    )
    rows = {m["name"]: m for m in publish.payload_for(run_dir)["metrics"]}
    assert rows["Escalation precision"]["value"] == 1.0
    assert rows["Escalation precision"]["passed"] is True
    # The other rows are untouched.
    assert rows["Autonomous resolution"] == METRICS[0]


def test_an_unreviewed_run_leaves_escalation_precision_empty(tmp_path):
    run_dir = _write_run(
        tmp_path,
        "2026-09-25T16-19-30Z-a6a8e3a",
        {"run_id": "2026-09-25T16-19-30Z-a6a8e3a", "agent_model": "anthropic/claude-sonnet-5"},
        METRICS,
    )
    rows = {m["name"]: m for m in publish.payload_for(run_dir)["metrics"]}
    assert rows["Escalation precision"] == METRICS[2]
