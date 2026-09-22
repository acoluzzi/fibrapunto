-- 003_roles.sql — §8: "The agent runs under its own service identity, with its
-- own authorizations. It never acts with the permissions of the person who
-- configured it."
--
-- Two principals. The agent connects as the first and only ever as the first.
-- Migrations, the reset script and the harness connect as the owner or the
-- platform role. The actual grants are NOT here — they are generated from
-- contracts/perimeter-policy.yaml into 004_grants.generated.sql, so that §8's
-- table has exactly one source of truth.
--
-- Passwords are substituted by scripts/apply_migrations.py from the environment;
-- :'agent_password' and :'platform_password' are psql variables.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fibrapunto_agent') THEN
        CREATE ROLE fibrapunto_agent LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fibrapunto_platform') THEN
        CREATE ROLE fibrapunto_platform LOGIN;
    END IF;
END
$$;

ALTER ROLE fibrapunto_agent    WITH PASSWORD :'agent_password';
ALTER ROLE fibrapunto_platform WITH PASSWORD :'platform_password';

-- Start from nothing. Every grant either role has is an explicit decision
-- recorded in the perimeter policy; nothing is inherited by accident.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE :"db_name" FROM PUBLIC;

-- Connecting is all either role gets from this file. Every object grant —
-- for both principals, not just the agent — is generated from
-- contracts/perimeter-policy.yaml into 004_grants.generated.sql, so there is
-- exactly one place to read to know what an identity can do.
GRANT CONNECT ON DATABASE :"db_name" TO fibrapunto_agent, fibrapunto_platform;
