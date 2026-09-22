-- 002_platform.sql — the platform's own records: transcripts, traces, eval runs.
-- The agent has NO grant on this schema at all. It cannot read its own
-- transcripts, for the same reason it cannot read a customer's email (§8).

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TYPE platform.turn_role AS ENUM ('customer', 'agent', 'system');

CREATE TABLE platform.conversation (
    id                uuid PRIMARY KEY,
    created_at        timestamptz NOT NULL DEFAULT now(),
    ended_at          timestamptz,
    channel           text        NOT NULL DEFAULT 'web',

    -- Which failure mode the billing stub is in for this session (§7). Held
    -- per-session so the harness can run cases concurrently without them
    -- stepping on each other.
    test_mode         text,

    -- Everything needed to attribute a result to an exact build (§14).
    agent_model       text        NOT NULL,
    agent_model_served text,                    -- what OpenRouter actually used (§10)
    router_model      text        NOT NULL,
    prompt_sha        text        NOT NULL,
    git_sha           text,

    guardrails_config jsonb       NOT NULL DEFAULT '{}'::jsonb,
    final_status      text,

    run_id            text,                     -- set when driven by the harness
    case_id           text,

    retention_until   timestamptz NOT NULL,     -- §9
    purged_at         timestamptz
);

CREATE INDEX conversation_run_idx       ON platform.conversation (run_id, case_id);
CREATE INDEX conversation_retention_idx ON platform.conversation (retention_until)
    WHERE purged_at IS NULL;

CREATE TABLE platform.turn (
    id              bigserial PRIMARY KEY,
    conversation_id uuid    NOT NULL REFERENCES platform.conversation(id) ON DELETE CASCADE,
    seq             int     NOT NULL,
    role            platform.turn_role NOT NULL,

    -- Redacted text only. The raw text is never written to disk (§9). An
    -- invariant test re-runs the detectors over this column after every run.
    text_redacted   text,
    redaction_flags jsonb   NOT NULL DEFAULT '[]'::jsonb,

    latency_ms      int,
    tokens_in       int,
    tokens_out      int,
    cost_usd        numeric(10,6),

    created_at      timestamptz NOT NULL DEFAULT now(),
    purged_at       timestamptz,                -- retention nulls the words, keeps the row

    UNIQUE (conversation_id, seq)
);

-- Field names chosen to be OpenTelemetry-span-compatible so the migration to a
-- real tracing backend is mechanical rather than a rewrite. See
-- docs/FRICTION-LOG.md for why that migration was not made now.
CREATE TABLE platform.trace_event (
    id              bigserial PRIMARY KEY,
    conversation_id uuid    NOT NULL REFERENCES platform.conversation(id) ON DELETE CASCADE,
    parent_event_id bigint  REFERENCES platform.trace_event(id),
    turn_seq        int,

    name            text    NOT NULL,           -- the event type
    start_ts        timestamptz NOT NULL DEFAULT now(),
    monotonic_ns    bigint,
    duration_ms     int,
    attributes      jsonb   NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX trace_conversation_idx ON platform.trace_event (conversation_id, id);
CREATE INDEX trace_name_idx         ON platform.trace_event (name);

CREATE TABLE platform.eval_run (
    run_id        text PRIMARY KEY,
    created_at    timestamptz NOT NULL DEFAULT now(),
    git_sha       text,
    agent_model   text NOT NULL,
    judge_model   text NOT NULL,
    dataset_sha   text NOT NULL,
    prompt_sha    text NOT NULL,
    metrics       jsonb,
    CONSTRAINT judge_is_not_subject CHECK (
        split_part(judge_model, '/', 1) <> split_part(agent_model, '/', 1)
    )
);
