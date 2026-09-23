-- 001_crm.sql — the CRM is the system of record for the contract (§6.1).
-- Payment status is deliberately NOT here: it lives in the billing system.

CREATE SCHEMA IF NOT EXISTS crm;

CREATE TYPE crm.contract_status   AS ENUM ('active', 'suspended', 'terminated');
CREATE TYPE crm.handover_type     AS ENUM ('billing_dispute', 'cancellation_request', 'technical');
CREATE TYPE crm.priority          AS ENUM ('low', 'medium', 'high');
CREATE TYPE crm.queue             AS ENUM ('billing_operations', 'retention', 'technical_support');
CREATE TYPE crm.record_status     AS ENUM ('open', 'in_progress', 'closed');

CREATE TABLE crm.customer (
    id          bigserial PRIMARY KEY,
    full_name   text        NOT NULL,
    -- The agent has no SELECT grant on these two columns (§8, §9).
    -- What it cannot read it cannot repeat in a conversation or send to a model.
    email       text        NOT NULL,
    phone       text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE crm.contract (
    contract_number  text PRIMARY KEY
                     CHECK (contract_number ~ '^FP-[0-9]{4}-[0-9]{6}$'),
    customer_id      bigint              NOT NULL REFERENCES crm.customer(id),
    modem_serial     text                NOT NULL,
    plan_name        text                NOT NULL,
    monthly_fee_eur  numeric(6,2)        NOT NULL,
    activation_date  date                NOT NULL,
    lock_in_until    date,                       -- NULL = no lock-in
    service_address  text                NOT NULL,
    status           crm.contract_status NOT NULL DEFAULT 'active'
);

CREATE INDEX contract_customer_idx ON crm.contract (customer_id);

-- Reference numbers the agent cannot choose. It is granted USAGE on the
-- sequence and nothing more, so §12.4's "must not state a reference number it
-- did not receive" is structural rather than instructed.
CREATE SEQUENCE crm.handover_reference_seq START WITH 1042;

CREATE TABLE crm.handover_record (
    id                      bigserial PRIMARY KEY,
    reference               text NOT NULL UNIQUE
                            DEFAULT to_char(nextval('crm.handover_reference_seq'), 'FM00000000'),
    record_type             crm.handover_type NOT NULL,
    priority                crm.priority      NOT NULL,
    queue                   crm.queue         NOT NULL,

    -- Nullable: §13 requires the record to be created even when the contract
    -- cannot be linked. An incomplete record beats no record.
    contract_number         text REFERENCES crm.contract(contract_number),
    claimed_contract_number text,               -- what the customer said, unvalidated
    contract_link_missing   boolean NOT NULL DEFAULT false,
    customer_id             bigint REFERENCES crm.customer(id),

    subject                 text    NOT NULL,
    collected_context       jsonb   NOT NULL,   -- named keys, not prose (§13, §1)
    stated_reason           text,               -- the customer's own words (§12.2)

    -- Omitted from the agent's INSERT grant, so the agent physically cannot
    -- create a record that claims not to be agent-handled (§13).
    handled_by_agent        boolean NOT NULL DEFAULT true,
    -- Never readable by the agent (§8, field-level restriction).
    internal_comments       text,

    status                  crm.record_status NOT NULL DEFAULT 'open',
    origin_session_id       uuid    NOT NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),

    -- §13's table, enforced by the engine rather than by the model.
    CONSTRAINT priority_matches_type CHECK (
        (record_type = 'cancellation_request') = (priority = 'high')
    ),
    CONSTRAINT queue_matches_type CHECK (
        (record_type = 'billing_dispute'      AND queue = 'billing_operations') OR
        (record_type = 'cancellation_request' AND queue = 'retention')          OR
        (record_type = 'technical'            AND queue = 'technical_support')
    ),
    CONSTRAINT link_flag_consistent CHECK (
        contract_link_missing = (contract_number IS NULL)
    )
);

CREATE INDEX handover_queue_idx   ON crm.handover_record (queue, status, created_at DESC);
CREATE INDEX handover_session_idx ON crm.handover_record (origin_session_id);

-- Completeness recomputed in SQL, independent of the code path that wrote the
-- row, so the §1 metric is not measured by the thing it measures.
CREATE VIEW crm.v_handover_completeness AS
SELECT
    r.id,
    r.reference,
    r.record_type,
    r.origin_session_id,
    r.created_at,
    (
        r.subject           IS NOT NULL AND r.subject <> ''
        AND r.collected_context ? 'what_customer_was_told'
        AND r.collected_context ? 'escalation_trigger'
        AND r.collected_context ? 'lookups'
        AND (r.contract_number IS NOT NULL OR r.claimed_contract_number IS NOT NULL)
        AND (r.record_type <> 'cancellation_request'
             OR (r.stated_reason IS NOT NULL
                 AND r.collected_context ? 'request_date'))
        -- `disputed_point` is required only when a lookup actually returned a
        -- figure. §13's table describes a *worked* billing dispute; a billing
        -- escalation where the lookup never succeeded is a different shape, and
        -- there is no amount in existence for the customer to dispute.
        --
        -- This mirrors handover/completeness.py deliberately rather than
        -- sharing code with it: §1 wants completeness recomputed independently
        -- of the path that wrote the row. Independent has to mean *the same
        -- rule, derived separately* — the two disagreeing is a bug, and it was
        -- one: the Python side was fixed first and this was left behind, so
        -- every record the agent opened during an outage read as incomplete.
        AND (r.record_type <> 'billing_dispute'
             OR (r.collected_context ? 'billing_status'
                 AND (r.collected_context ? 'disputed_point'
                      OR NOT EXISTS (
                          SELECT 1
                            FROM jsonb_array_elements(
                                     COALESCE(r.collected_context -> 'lookups', '[]'::jsonb)
                                 ) AS lookup
                           WHERE lookup ->> 'outcome' = 'found'
                      ))))
    ) AS is_complete
FROM crm.handover_record r;
