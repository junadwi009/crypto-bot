-- ============================================================
-- Down: 0009_audit_and_capital_ledger_down.sql
-- WARNING: dropping these tables destroys the immutable audit trail
-- and capital movements ledger. Only run if a follow-up migration
-- recreates equivalent immutable storage.
-- ============================================================

DROP TRIGGER IF EXISTS trg_capital_ledger_balance_check ON capital_ledger;
DROP FUNCTION IF EXISTS capital_ledger_balance_check();

DROP POLICY IF EXISTS capital_ledger_no_update ON capital_ledger;
DROP POLICY IF EXISTS capital_ledger_no_delete ON capital_ledger;
DROP POLICY IF EXISTS capital_ledger_select    ON capital_ledger;
DROP POLICY IF EXISTS capital_ledger_insert    ON capital_ledger;
DROP INDEX IF EXISTS idx_ledger_txn_type;
DROP INDEX IF EXISTS idx_ledger_approved_at;
DROP TABLE IF EXISTS capital_ledger;

DROP POLICY IF EXISTS audit_trail_no_update ON audit_trail;
DROP POLICY IF EXISTS audit_trail_no_delete ON audit_trail;
DROP POLICY IF EXISTS audit_trail_select    ON audit_trail;
DROP POLICY IF EXISTS audit_trail_insert    ON audit_trail;
DROP INDEX IF EXISTS idx_audit_action;
DROP INDEX IF EXISTS idx_audit_actor;
DROP INDEX IF EXISTS idx_audit_event_time;
DROP TABLE IF EXISTS audit_trail;
