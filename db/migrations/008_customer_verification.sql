-- Verifying the customer before anything about their account is shared or done.
--
-- A contract number is printed on every invoice: knowing it does not make you
-- the customer. Before billing figures, cases, offers or case history, the
-- customer answers a personal security question ("the name of your first
-- pet"). The answers are stored here, bcrypt-hashed, in a table no application
-- role can read. The agent may only EXECUTE two functions: one says which
-- question to ask for a contract, the other says whether an answer matches —
-- yes or no. The agent never holds an answer, so it cannot repeat one or send
-- it to a model (§8, §9).
--
-- Re-applied on every `make migrate`; safe to repeat.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS crm.security_answer (
    customer_id   bigint NOT NULL REFERENCES crm.customer(id) ON DELETE CASCADE,
    question_key  text   NOT NULL,
    question      text   NOT NULL,
    answer_hash   text   NOT NULL,          -- crypt(normalised answer, bf salt)
    PRIMARY KEY (customer_id, question_key)
);

-- "Birba", " birba ", "BIRBA" are the same answer; the comparison is on this.
CREATE OR REPLACE FUNCTION crm.normalise_answer(answer text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT lower(regexp_replace(btrim(coalesce(answer, '')), '\s+', ' ', 'g'))
$$;

-- Which question to ask for this contract: the first of the customer's, by
-- key, so the same customer is always asked the same thing. No row: the CRM
-- does not know the contract, so there is nothing of anyone's to protect.
CREATE OR REPLACE FUNCTION crm.security_question(contract text)
RETURNS TABLE (question_key text, question text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = crm, pg_temp AS $$
    SELECT a.question_key, a.question
      FROM crm.contract k
      JOIN crm.security_answer a ON a.customer_id = k.customer_id
     WHERE k.contract_number = contract
     ORDER BY a.question_key
     LIMIT 1
$$;

-- Does this answer match? Yes or no — nothing else leaves the function.
CREATE OR REPLACE FUNCTION crm.verify_security_answer(contract text, key text, answer text)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = crm, public, pg_temp AS $$
    SELECT coalesce(bool_or(a.answer_hash = crypt(crm.normalise_answer(answer), a.answer_hash)), false)
      FROM crm.contract k
      JOIN crm.security_answer a ON a.customer_id = k.customer_id
     WHERE k.contract_number = contract AND a.question_key = key
$$;

REVOKE ALL ON crm.security_answer FROM PUBLIC;
REVOKE ALL ON FUNCTION crm.security_question(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION crm.verify_security_answer(text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION crm.security_question(text) TO fibrapunto_agent;
GRANT EXECUTE ON FUNCTION crm.verify_security_answer(text, text, text) TO fibrapunto_agent;
