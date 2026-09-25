-- Messages to the customer outside the chat, queued but not sent (demo).
--
-- When a colleague decides on an offer the customer accepted, the customer is
-- told in their conversation — and, in a real deployment, by email too. The
-- CRM holds the address; this table is the outbox a mail sender would drain.
-- Nothing drains it here: `sent_at` stays NULL, and the operator view shows
-- each message as "queued, not sent", so the demo never pretends to email
-- anyone.
--
-- Re-applied on every `make migrate`; safe to repeat.

CREATE TABLE IF NOT EXISTS platform.notification_outbox (
    id               bigserial   PRIMARY KEY,
    reference        text        NOT NULL,           -- the case it is about
    conversation_id  uuid,
    channel          text        NOT NULL CHECK (channel IN ('email')),
    recipient        text        NOT NULL,
    subject          text        NOT NULL,
    body             text        NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    sent_at          timestamptz                      -- never set by the demo
);

CREATE INDEX IF NOT EXISTS notification_outbox_reference_idx
    ON platform.notification_outbox (reference, created_at);

GRANT ALL ON platform.notification_outbox TO fibrapunto_platform;
GRANT ALL ON SEQUENCE platform.notification_outbox_id_seq TO fibrapunto_platform;
