-- Conversations that survive a restart, owned by an anonymous browser.
--
-- The agent still has no memory across conversations (§3). What changes is the
-- platform around it: it now remembers which conversations belong to one
-- browser installation, so they can be listed and resumed after a restart.
-- A browser principal is not a person and not an authentication of the
-- customer — the contract number stays data, never a credential.
--
-- Re-applied on every `make migrate` (no sentinel in apply_migrations.py), so
-- every statement is written to be safe to repeat.

CREATE TABLE IF NOT EXISTS platform.browser_principal (
    id            uuid PRIMARY KEY,
    -- HMAC-SHA-256 of the cookie token. The token itself is never stored: a
    -- leaked table cannot be replayed as a cookie.
    token_hash    bytea       NOT NULL UNIQUE,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    revoked_at    timestamptz
);

ALTER TABLE platform.conversation
    ADD COLUMN IF NOT EXISTS browser_principal_id uuid
        REFERENCES platform.browser_principal(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS last_activity_at timestamptz NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS conversation_principal_idx
    ON platform.conversation (browser_principal_id, last_activity_at DESC, id DESC);

-- The runtime state needed to resume, and nothing else. What it holds is fixed
-- by RuntimeCheckpointV1 (state/checkpoint.py), not by whatever
-- ConversationState happens to contain: no transcript (platform.turn has it,
-- redacted), no capability log (the trace has it), no raw sensitive values.
CREATE TABLE IF NOT EXISTS platform.conversation_checkpoint (
    conversation_id       uuid PRIMARY KEY
        REFERENCES platform.conversation(id) ON DELETE CASCADE,
    -- Optimistic concurrency: a write names the version it read.
    version               bigint      NOT NULL DEFAULT 1,
    state_schema_version  int         NOT NULL,
    state_json            jsonb       NOT NULL,
    last_turn_seq         int         NOT NULL,
    -- A short lease: one turn at a time per conversation, across processes.
    lease_token           uuid,
    lease_until           timestamptz,
    updated_at            timestamptz NOT NULL DEFAULT now()
);

-- Idempotent turns. A retry with the same client_turn_id gets the stored reply
-- instead of a second model call and a second set of side effects.
CREATE TABLE IF NOT EXISTS platform.turn_request (
    conversation_id  uuid        NOT NULL
        REFERENCES platform.conversation(id) ON DELETE CASCADE,
    client_turn_id   uuid        NOT NULL,
    request_hash     bytea       NOT NULL,
    status           text        NOT NULL CHECK (status IN ('processing', 'done')),
    response_json    jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, client_turn_id)
);

-- 004 grants "all tables in schema platform" as they exist when it runs, and it
-- runs before this file: without these, a fresh database leaves the new tables
-- unreachable to the platform identity until the next migrate.
GRANT ALL ON platform.browser_principal       TO fibrapunto_platform;
GRANT ALL ON platform.conversation_checkpoint TO fibrapunto_platform;
GRANT ALL ON platform.turn_request            TO fibrapunto_platform;
