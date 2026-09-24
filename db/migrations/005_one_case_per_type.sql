-- One conversation, one case of each type (§13).
--
-- The check in create_handover_record is per process and per conversation
-- object: two concurrent requests for the same conversation both pass it
-- before either has written, and both insert. The guarantee belongs where the
-- rows are. The insert uses ON CONFLICT DO NOTHING against this index and
-- then reads back the reference that won, so the loser returns the same case
-- instead of an error.
--
-- Per record type, not per conversation: a billing dispute followed by "I want
-- to cancel" is two requests for two teams, and one case must not stand in for
-- the other.
--
-- Always re-applied (no sentinel in apply_migrations.py); IF NOT EXISTS makes
-- that a no-op after the first run.
CREATE UNIQUE INDEX IF NOT EXISTS handover_record_one_per_type
    ON crm.handover_record (origin_session_id, record_type);
