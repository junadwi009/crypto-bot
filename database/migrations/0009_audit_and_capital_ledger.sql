-- ============================================================
-- Migration: 0009_audit_and_capital_ledger.sql
-- Phase 2 — Append-only audit trail + capital movements ledger.
--
-- audit_trail:
--   Every authority-bearing action (CB reset, pause/resume, capital
--   inject approval, supervisor unhealthy clear, etc.) writes a row.
--   Append-only via RLS. Includes actor identity, action, payload.
--
-- capital_ledger:
--   Every change to total_capital. Inject/withdraw/pnl_settle/correction.
--   Source of truth for "what is current_capital?" — replaces the
--   portfolio_state.total_capital read path during Phase 3.
--   Append-only via RLS; balance_after computed on insert via trigger.
-- ============================================================

CREATE TABLE IF NOT EXISTS audit_trail (
    id           bigserial PRIMARY KEY,
    -- Single propagated decision_time per Council constraint 12; the
    -- caller supplies it, we don't re-read the wall clock here.
    event_time   timestamptz NOT NULL,
    actor_type   text NOT NULL CHECK (actor_type IN
                  ('telegram','dashboard','system','orchestrator',
                   'l0_supervisor','reconciliation')),
    actor_id     text NOT NULL,
    action       text NOT NULL,
    payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_event_time ON audit_trail(event_time DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor      ON audit_trail(actor_type, actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_action     ON audit_trail(action);

ALTER TABLE audit_trail ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS audit_trail_no_update ON audit_trail;
CREATE POLICY audit_trail_no_update ON audit_trail FOR UPDATE USING (false);
DROP POLICY IF EXISTS audit_trail_no_delete ON audit_trail;
CREATE POLICY audit_trail_no_delete ON audit_trail FOR DELETE USING (false);
DROP POLICY IF EXISTS audit_trail_select ON audit_trail;
CREATE POLICY audit_trail_select ON audit_trail FOR SELECT USING (true);
DROP POLICY IF EXISTS audit_trail_insert ON audit_trail;
CREATE POLICY audit_trail_insert ON audit_trail FOR INSERT WITH CHECK (true);


-- ── capital_ledger ──
CREATE TABLE IF NOT EXISTS capital_ledger (
    id              bigserial PRIMARY KEY,
    txn_type        text NOT NULL CHECK (txn_type IN
                     ('inject','withdraw','pnl_settle','correction','genesis')),
    amount_usd      numeric(12,4) NOT NULL,
    balance_after   numeric(12,4) NOT NULL,
    reason          text NOT NULL,
    proposed_by     text NOT NULL,
    approved_by     text NOT NULL,
    -- Approver-supplied; propagated decision_time from approval flow
    approved_at     timestamptz NOT NULL DEFAULT now(),
    -- Optional link to an orchestrator decision that triggered the change
    related_decision_id uuid REFERENCES orchestrator_decisions(decision_id),
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ledger_approved_at ON capital_ledger(approved_at DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_txn_type    ON capital_ledger(txn_type);

ALTER TABLE capital_ledger ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS capital_ledger_no_update ON capital_ledger;
CREATE POLICY capital_ledger_no_update ON capital_ledger FOR UPDATE USING (false);
DROP POLICY IF EXISTS capital_ledger_no_delete ON capital_ledger;
CREATE POLICY capital_ledger_no_delete ON capital_ledger FOR DELETE USING (false);
DROP POLICY IF EXISTS capital_ledger_select ON capital_ledger;
CREATE POLICY capital_ledger_select ON capital_ledger FOR SELECT USING (true);
DROP POLICY IF EXISTS capital_ledger_insert ON capital_ledger;
CREATE POLICY capital_ledger_insert ON capital_ledger FOR INSERT WITH CHECK (true);


-- ── balance_after sanity (defense-in-depth) ──
-- Trigger asserts that balance_after equals previous_balance + amount × sign.
-- This is a CHECK at write time; mistakes in the application layer cannot
-- silently corrupt the ledger.
CREATE OR REPLACE FUNCTION capital_ledger_balance_check()
RETURNS TRIGGER AS $$
DECLARE
    prev_balance numeric(12,4);
    expected     numeric(12,4);
    sign_factor  numeric;
BEGIN
    IF NEW.txn_type = 'genesis' THEN
        -- First row; no predecessor to validate against.
        RETURN NEW;
    END IF;

    SELECT balance_after INTO prev_balance
    FROM capital_ledger
    ORDER BY id DESC
    LIMIT 1;

    IF prev_balance IS NULL THEN
        -- First non-genesis row without a genesis predecessor — refuse
        RAISE EXCEPTION 'capital_ledger: non-genesis insert without predecessor';
    END IF;

    sign_factor := CASE NEW.txn_type
        WHEN 'inject'     THEN  1
        WHEN 'pnl_settle' THEN  1
        WHEN 'withdraw'   THEN -1
        WHEN 'correction' THEN  1   -- corrections may be positive or negative
        ELSE 0
    END;

    expected := prev_balance + NEW.amount_usd * sign_factor;

    IF NEW.txn_type = 'correction' THEN
        -- Corrections trust the operator-supplied balance_after; only
        -- check that the magnitude is plausibly small.
        IF abs(NEW.balance_after - prev_balance) > 100000 THEN
            RAISE EXCEPTION 'capital_ledger: correction balance change too large';
        END IF;
    ELSE
        IF abs(NEW.balance_after - expected) > 0.01 THEN
            RAISE EXCEPTION
                'capital_ledger: balance_after % != expected % (prev=% amount=% type=%)',
                NEW.balance_after, expected, prev_balance, NEW.amount_usd, NEW.txn_type;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_capital_ledger_balance_check ON capital_ledger;
CREATE TRIGGER trg_capital_ledger_balance_check
    BEFORE INSERT ON capital_ledger
    FOR EACH ROW EXECUTE FUNCTION capital_ledger_balance_check();
