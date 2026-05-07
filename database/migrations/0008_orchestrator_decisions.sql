-- ============================================================
-- Migration: 0008_orchestrator_decisions.sql
-- Phase 2 — Orchestrator audit trail (observe-only and beyond)
--
-- Creates orchestrator_decisions: append-only record of EVERY pipeline
-- invocation, including holds, rejected signals, frozen-state skips, and
-- counterfactual veto computations during observe-only mode.
--
-- Council mandate: this dataset is the Phase-3 enforcement-promotion
-- decision substrate. Missing rows during observe-only invalidates the
-- 14-day review. Append-only is enforced via RLS, not application
-- convention.
--
-- AUDIT before apply: none required (new table).
-- ============================================================

CREATE TABLE IF NOT EXISTS orchestrator_decisions (
    decision_id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Decision-time clock — captured ONCE inside orchestrator.evaluate()
    -- and propagated. Distinct from created_at (DB write time).
    decision_time            timestamptz NOT NULL,

    pair                     text NOT NULL,
    requested_action         text NOT NULL,

    -- Actual outcome (in observe-only mode, == requested_action)
    final_action             text NOT NULL,
    size_usd                 numeric(12,4) NOT NULL,
    size_scale               numeric(4,3)  NOT NULL DEFAULT 1.0,

    -- Counterfactual: what enforcement mode WOULD have done.
    -- Always populated, even when observe_only_passthrough = true.
    observe_only_passthrough boolean NOT NULL DEFAULT true,
    counterfactual_action    text NOT NULL,
    counterfactual_size_usd  numeric(12,4) NOT NULL,
    counterfactual_size_scale numeric(4,3) NOT NULL DEFAULT 1.0,
    counterfactual_mode      text NOT NULL,
    layer_vetoes             text[] NOT NULL DEFAULT '{}',

    -- Forensic snapshot. Schema-versioned per Council constraint 13.
    -- See governance/orchestrator.py LAYER_INPUTS_SCHEMA_VERSION HISTORY.
    layer_inputs             jsonb NOT NULL,

    -- Active governance mode at decision time (read-only consumer)
    governance_mode          text  NOT NULL,
    explanation              text  NOT NULL,

    -- DB write time (for latency analysis vs decision_time)
    created_at               timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_orch_pair_time
    ON orchestrator_decisions(pair, decision_time DESC);
CREATE INDEX IF NOT EXISTS idx_orch_decision_time
    ON orchestrator_decisions(decision_time DESC);
CREATE INDEX IF NOT EXISTS idx_orch_observe_only
    ON orchestrator_decisions(observe_only_passthrough);
CREATE INDEX IF NOT EXISTS idx_orch_vetoes_present
    ON orchestrator_decisions((array_length(layer_vetoes, 1)));

-- ── Append-only enforcement via RLS ──
-- Council mandate constraint 7: immutable via DB-level policies, not
-- application convention. UPDATE and DELETE are forbidden. INSERT is
-- the only permitted mutation.
ALTER TABLE orchestrator_decisions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS orchestrator_decisions_no_update ON orchestrator_decisions;
CREATE POLICY orchestrator_decisions_no_update
    ON orchestrator_decisions
    FOR UPDATE
    USING (false);

DROP POLICY IF EXISTS orchestrator_decisions_no_delete ON orchestrator_decisions;
CREATE POLICY orchestrator_decisions_no_delete
    ON orchestrator_decisions
    FOR DELETE
    USING (false);

DROP POLICY IF EXISTS orchestrator_decisions_select ON orchestrator_decisions;
CREATE POLICY orchestrator_decisions_select
    ON orchestrator_decisions
    FOR SELECT
    USING (true);

DROP POLICY IF EXISTS orchestrator_decisions_insert ON orchestrator_decisions;
CREATE POLICY orchestrator_decisions_insert
    ON orchestrator_decisions
    FOR INSERT
    WITH CHECK (true);
