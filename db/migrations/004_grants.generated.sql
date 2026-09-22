-- 004_grants.generated.sql
--
-- GENERATED FILE — DO NOT EDIT.
-- Source: contracts/perimeter-policy.yaml
-- Regenerate: uv run fibrapunto-render-grants
--
-- This is §8's table as the database actually enforces it. Every column the
-- agent can read is listed here; every column that is not listed is denied by
-- the engine, not by an instruction (§17.2, §17.3).


-- --------------------------------------------------------------------------
-- svc_agent  (db role: fibrapunto_agent)
-- The conversational agent. Runs under its own identity, never with the permissions of the person who configured it (§8).
-- --------------------------------------------------------------------------

REVOKE ALL ON ALL TABLES IN SCHEMA crm, platform FROM fibrapunto_agent;
REVOKE ALL ON SCHEMA crm FROM fibrapunto_agent;
REVOKE ALL ON SCHEMA platform FROM fibrapunto_agent;

GRANT USAGE ON SCHEMA crm TO fibrapunto_agent;
-- schema platform: no access, deliberately

-- customer   -- denied: email, phone
GRANT SELECT (id, full_name) ON crm.customer TO fibrapunto_agent;

-- contract
GRANT SELECT ON crm.contract TO fibrapunto_agent;

-- handover_record   -- denied: internal_comments
GRANT SELECT (id, reference, record_type, priority, queue, contract_number, claimed_contract_number, contract_link_missing, customer_id, subject, collected_context, stated_reason, handled_by_agent, status, origin_session_id, created_at) ON crm.handover_record TO fibrapunto_agent;
GRANT INSERT (record_type, priority, queue, contract_number, claimed_contract_number, contract_link_missing, customer_id, subject, collected_context, stated_reason, origin_session_id) ON crm.handover_record TO fibrapunto_agent;

GRANT USAGE ON SEQUENCE crm.handover_reference_seq TO fibrapunto_agent;
GRANT USAGE ON SEQUENCE crm.handover_record_id_seq TO fibrapunto_agent;


-- --------------------------------------------------------------------------
-- platform_admin  (db role: fibrapunto_platform)
-- The platform identity. Runs migrations, the reset script, the eval harness and the operator view. Never used to serve a conversation.
-- --------------------------------------------------------------------------

REVOKE ALL ON ALL TABLES IN SCHEMA crm, platform FROM fibrapunto_platform;
REVOKE ALL ON SCHEMA crm FROM fibrapunto_platform;
REVOKE ALL ON SCHEMA platform FROM fibrapunto_platform;

GRANT USAGE ON SCHEMA crm TO fibrapunto_platform;
GRANT USAGE, CREATE ON SCHEMA platform TO fibrapunto_platform;

GRANT ALL ON ALL TABLES    IN SCHEMA platform TO fibrapunto_platform;
GRANT ALL ON ALL SEQUENCES IN SCHEMA platform TO fibrapunto_platform;

-- customer
GRANT SELECT ON crm.customer TO fibrapunto_platform;
GRANT INSERT ON crm.customer TO fibrapunto_platform;
GRANT UPDATE ON crm.customer TO fibrapunto_platform;
GRANT DELETE ON crm.customer TO fibrapunto_platform;

-- contract
GRANT SELECT ON crm.contract TO fibrapunto_platform;
GRANT INSERT ON crm.contract TO fibrapunto_platform;
GRANT UPDATE ON crm.contract TO fibrapunto_platform;
GRANT DELETE ON crm.contract TO fibrapunto_platform;

-- handover_record
GRANT SELECT ON crm.handover_record TO fibrapunto_platform;
GRANT INSERT ON crm.handover_record TO fibrapunto_platform;
GRANT UPDATE ON crm.handover_record TO fibrapunto_platform;
GRANT DELETE ON crm.handover_record TO fibrapunto_platform;

-- handover_completeness
GRANT SELECT ON crm.v_handover_completeness TO fibrapunto_platform;

-- Everything not granted above is denied. In particular the agent has no
-- grant on schema platform, so it cannot read its own transcripts.
