-- ============================================================
-- scripts/phase2_rls_verification.sql
-- Operator-runnable verification that the RLS policies introduced in
-- 0008_orchestrator_decisions.sql and 0009_audit_and_capital_ledger.sql
-- correctly forbid UPDATE and DELETE on append-only tables.
--
-- Run against a non-production DB (or a snapshot). Each block must
-- produce the EXPECTED result; deviation indicates RLS is misconfigured
-- and the immutability guarantee is broken.
--
-- Capture the output and include in the Phase-2 submission as
-- evidence_rls_immutability.txt.
-- ============================================================

-- 1. Insert a test row (allowed)
INSERT INTO orchestrator_decisions
    (pair, decision_time, requested_action, final_action,
     size_usd, observe_only_passthrough,
     counterfactual_action, counterfactual_size_usd, counterfactual_mode,
     layer_vetoes, layer_inputs, governance_mode, explanation)
VALUES
    ('TEST/USDT', now(), 'buy', 'buy', 10.0, true,
     'buy', 10.0, 'normal',
     ARRAY[]::text[], '{"schema_version":1,"test":"rls_check"}'::jsonb,
     'normal', 'rls_test_row');

-- EXPECTED: INSERT 0 1

-- 2. Try to UPDATE the row — must fail
\echo '\n--- ATTEMPTING UPDATE ON orchestrator_decisions (must be blocked by RLS) ---'
UPDATE orchestrator_decisions
   SET explanation = 'tampered'
 WHERE explanation = 'rls_test_row';
-- EXPECTED: UPDATE 0   (RLS USING(false) returns no rows to update)
--          (with stricter checks: ERROR: new row violates row-level security policy)

-- Confirm row unchanged
SELECT decision_id, explanation
  FROM orchestrator_decisions
 WHERE explanation = 'rls_test_row';
-- EXPECTED: row still shows explanation='rls_test_row'

-- 3. Try to DELETE the row — must fail
\echo '\n--- ATTEMPTING DELETE ON orchestrator_decisions (must be blocked by RLS) ---'
DELETE FROM orchestrator_decisions WHERE explanation = 'rls_test_row';
-- EXPECTED: DELETE 0  (RLS USING(false) blocks)

-- Confirm row still present
SELECT count(*) AS still_present
  FROM orchestrator_decisions
 WHERE explanation = 'rls_test_row';
-- EXPECTED: 1

-- 4. Same for audit_trail
\echo '\n--- audit_trail RLS verification ---'
INSERT INTO audit_trail (event_time, actor_type, actor_id, action, payload)
VALUES (now(), 'system', 'rls_test', 'rls_check', '{"test": true}'::jsonb);

UPDATE audit_trail SET action = 'tampered' WHERE actor_id = 'rls_test';
-- EXPECTED: UPDATE 0
DELETE FROM audit_trail WHERE actor_id = 'rls_test';
-- EXPECTED: DELETE 0

SELECT action FROM audit_trail WHERE actor_id = 'rls_test';
-- EXPECTED: 'rls_check'

-- 5. capital_ledger RLS + balance trigger
\echo '\n--- capital_ledger RLS + balance trigger verification ---'
INSERT INTO capital_ledger
    (txn_type, amount_usd, balance_after, reason, proposed_by, approved_by)
VALUES
    ('genesis', 213.0, 213.0, 'rls_test_genesis', 'test', 'test');

-- Try injection without genesis predecessor (after a clean test DB) — would fail
-- Try with consistent balance:
INSERT INTO capital_ledger
    (txn_type, amount_usd, balance_after, reason, proposed_by, approved_by)
VALUES
    ('inject', 50.0, 263.0, 'rls_test_inject', 'test', 'test');
-- EXPECTED: INSERT succeeds because 213 + 50 = 263

-- Try INSERT with WRONG balance — trigger must reject
\echo '\n--- attempting INSERT with inconsistent balance (must be rejected by trigger) ---'
INSERT INTO capital_ledger
    (txn_type, amount_usd, balance_after, reason, proposed_by, approved_by)
VALUES
    ('inject', 50.0, 999.0, 'rls_test_bad_balance', 'test', 'test');
-- EXPECTED: ERROR: capital_ledger: balance_after 999 != expected 313

-- UPDATE/DELETE forbidden as before
UPDATE capital_ledger SET amount_usd = 9999 WHERE reason LIKE 'rls_test_%';
-- EXPECTED: UPDATE 0
DELETE FROM capital_ledger WHERE reason LIKE 'rls_test_%';
-- EXPECTED: DELETE 0

-- ============================================================
-- VERIFICATION CHECKLIST
-- ============================================================
-- [ ] orchestrator_decisions: UPDATE returned 0 rows
-- [ ] orchestrator_decisions: DELETE returned 0 rows
-- [ ] audit_trail:           UPDATE returned 0 rows
-- [ ] audit_trail:           DELETE returned 0 rows
-- [ ] capital_ledger:        UPDATE returned 0 rows
-- [ ] capital_ledger:        DELETE returned 0 rows
-- [ ] capital_ledger:        bad-balance INSERT rejected with ERROR
--
-- All must check before Phase-2 acceptance.
