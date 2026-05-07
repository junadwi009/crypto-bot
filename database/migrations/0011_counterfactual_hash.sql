-- ============================================================
-- Migration: 0011_counterfactual_hash.sql
-- Phase 2.5 R3 — Counterfactual determinism hash.
--
-- Adds counterfactual_hash column to orchestrator_decisions. The hash is
-- computed by governance/orchestrator.py compute_counterfactual_hash()
-- as SHA-256 of canonical JSON of {action, size_scale, mode, vetoes}.
--
-- Two-step migration so existing rows (if any) are not violated by the
-- NOT NULL constraint:
--   1. ADD COLUMN nullable
--   2. backfill any pre-existing rows with a sentinel
--   3. SET NOT NULL
--
-- Existing RLS append-only policies are unchanged. No new policies needed.
-- ============================================================

-- 1. Add column nullable, idempotent
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'orchestrator_decisions'
           AND column_name = 'counterfactual_hash'
    ) THEN
        ALTER TABLE orchestrator_decisions
            ADD COLUMN counterfactual_hash text;
        RAISE NOTICE 'Added: orchestrator_decisions.counterfactual_hash (nullable)';
    ELSE
        RAISE NOTICE 'Skipped (exists): orchestrator_decisions.counterfactual_hash';
    END IF;
END $$;

-- 2. Backfill pre-R3 rows with a sentinel so NOT NULL can be applied.
--    Operator note: 'pre_r3_no_hash' denotes that the hash was not
--    computed at write time; review queries should treat these as
--    unverifiable for replay.
UPDATE orchestrator_decisions
   SET counterfactual_hash = 'pre_r3_no_hash'
 WHERE counterfactual_hash IS NULL;

-- 3. Promote to NOT NULL once all rows have a value.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'orchestrator_decisions'
           AND column_name = 'counterfactual_hash'
           AND is_nullable = 'YES'
    ) THEN
        ALTER TABLE orchestrator_decisions
            ALTER COLUMN counterfactual_hash SET NOT NULL;
        RAISE NOTICE 'Set NOT NULL: orchestrator_decisions.counterfactual_hash';
    ELSE
        RAISE NOTICE 'Skipped (already NOT NULL): orchestrator_decisions.counterfactual_hash';
    END IF;
END $$;

-- Index for replay-equivalence queries: cluster decisions with identical
-- enforcement outcome. Useful in Phase-3 review.
CREATE INDEX IF NOT EXISTS idx_orch_counterfactual_hash
    ON orchestrator_decisions(counterfactual_hash);
