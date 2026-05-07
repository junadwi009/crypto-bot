-- ============================================================
-- Down: 0011_counterfactual_hash_down.sql
-- WARNING: dropping counterfactual_hash removes the replay-determinism
-- identity field used by Phase-3 enforcement-promotion review queries.
-- Only run if a follow-up migration replaces it.
-- ============================================================

DROP INDEX IF EXISTS idx_orch_counterfactual_hash;

ALTER TABLE orchestrator_decisions
    DROP COLUMN IF EXISTS counterfactual_hash;
