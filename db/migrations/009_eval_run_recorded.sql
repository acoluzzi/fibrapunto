-- 009_eval_run_recorded.sql — platform.eval_run finally has a writer.
--
-- The table was created in 002 and, as the friction log records, nothing ever
-- wrote to it: the harness kept its runs in evals/runs/ on the host, where the
-- backend container cannot see them, and the dashboard's §1 table stayed a
-- placeholder. The harness now records every run through the operator API,
-- and the dashboard reads them back from here.
--
-- Three columns loosen so that what was actually run can be recorded honestly:
--
--   * judge_model: `make eval-quick` runs no judge. NULL says so; a placeholder
--     vendor would satisfy the judge_is_not_subject CHECK by lying. The CHECK
--     passes on NULL (its comparison is unknown), which is the right reading —
--     no judge is not a judge that shares the subject's vendor.
--   * dataset_sha, prompt_sha: runs made before the harness recorded them can
--     be published from their run directory, and their shas were never taken.
--     NULL, not a sha computed today over a test set that may since have
--     changed.
--
-- Re-applied on every `make migrate`; safe to repeat.

ALTER TABLE platform.eval_run ALTER COLUMN judge_model DROP NOT NULL;
ALTER TABLE platform.eval_run ALTER COLUMN dataset_sha DROP NOT NULL;
ALTER TABLE platform.eval_run ALTER COLUMN prompt_sha  DROP NOT NULL;

-- How many conversations the metrics are over: a 100% over one case and a 100%
-- over thirty are different claims, and the dashboard must say which.
ALTER TABLE platform.eval_run ADD COLUMN IF NOT EXISTS case_count integer;
-- When the harness started the run, as its run id states it. created_at is
-- when the row was written, which for a backfilled run is much later.
ALTER TABLE platform.eval_run ADD COLUMN IF NOT EXISTS started_at timestamptz;

CREATE INDEX IF NOT EXISTS eval_run_started_idx ON platform.eval_run (started_at DESC NULLS LAST);
