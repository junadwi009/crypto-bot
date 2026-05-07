-- ============================================================
-- Down: 0008_orchestrator_decisions_down.sql
-- WARNING: dropping this table destroys the Phase-3 enforcement-promotion
-- decision substrate. Only run if a follow-up migration immediately
-- recreates equivalent or stricter audit storage.
-- ============================================================

DROP POLICY IF EXISTS orchestrator_decisions_no_update ON orchestrator_decisions;
DROP POLICY IF EXISTS orchestrator_decisions_no_delete ON orchestrator_decisions;
DROP POLICY IF EXISTS orchestrator_decisions_select   ON orchestrator_decisions;
DROP POLICY IF EXISTS orchestrator_decisions_insert   ON orchestrator_decisions;

DROP INDEX IF EXISTS idx_orch_vetoes_present;
DROP INDEX IF EXISTS idx_orch_observe_only;
DROP INDEX IF EXISTS idx_orch_decision_time;
DROP INDEX IF EXISTS idx_orch_pair_time;

DROP TABLE IF EXISTS orchestrator_decisions;
