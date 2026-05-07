-- ============================================================
-- Migration: 0007_l0_constraints.sql
-- Phase 1 — Layer 0 Hard Safety Kernel — DB-level enforcement
--
-- Adds CHECK constraints that mirror governance/safety_kernel constants.
-- These are the LAST line of defense if application-layer validation is
-- bypassed (direct SQL, future contributor error, Opus malformed update).
--
-- Idempotent — safe to re-apply. Uses DO blocks to check pg_constraint
-- before adding (Postgres < 15 does not support CONSTRAINT IF NOT EXISTS).
--
-- AUDIT before apply (run manually, expect zero rows from each):
--
--   SELECT pair, position_multiplier FROM strategy_params
--    WHERE position_multiplier < 0.3 OR position_multiplier > 1.5;
--
--   SELECT id, cost_usd FROM claude_usage WHERE cost_usd < 0;
--
--   SELECT id, pair, pnl_usd, amount_usd FROM trades
--    WHERE pnl_usd IS NOT NULL
--      AND amount_usd IS NOT NULL
--      AND pnl_usd < -amount_usd * 1.5;
--
-- If any row returns, generate a data-cleanup migration BEFORE applying
-- this one. Do NOT relax the constraint to accommodate bad data.
-- ============================================================

-- ── strategy_params.position_multiplier in L0 bounds [0.3, 1.5] ──
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_strategy_params_position_multiplier_l0'
    ) THEN
        ALTER TABLE strategy_params
            ADD CONSTRAINT chk_strategy_params_position_multiplier_l0
            CHECK (position_multiplier >= 0.3 AND position_multiplier <= 1.5);
        RAISE NOTICE 'Added: chk_strategy_params_position_multiplier_l0';
    ELSE
        RAISE NOTICE 'Skipped (exists): chk_strategy_params_position_multiplier_l0';
    END IF;
END $$;

-- ── claude_usage.cost_usd cannot be negative ──
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_claude_usage_cost_nonneg_l0'
    ) THEN
        ALTER TABLE claude_usage
            ADD CONSTRAINT chk_claude_usage_cost_nonneg_l0
            CHECK (cost_usd >= 0);
        RAISE NOTICE 'Added: chk_claude_usage_cost_nonneg_l0';
    ELSE
        RAISE NOTICE 'Skipped (exists): chk_claude_usage_cost_nonneg_l0';
    END IF;
END $$;

-- ── trades.pnl_usd sanity: cannot lose more than 1.5× position size ──
-- Catches obvious accounting errors (e.g., qty miscalc, side flip).
-- 1.5× allows for fee + slippage on full SL hit.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_trades_pnl_sanity_l0'
    ) THEN
        ALTER TABLE trades
            ADD CONSTRAINT chk_trades_pnl_sanity_l0
            CHECK (
                pnl_usd IS NULL
                OR amount_usd IS NULL
                OR pnl_usd >= -amount_usd * 1.5
            );
        RAISE NOTICE 'Added: chk_trades_pnl_sanity_l0';
    ELSE
        RAISE NOTICE 'Skipped (exists): chk_trades_pnl_sanity_l0';
    END IF;
END $$;

-- ── trades.amount_usd must be positive ──
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_trades_amount_positive_l0'
    ) THEN
        ALTER TABLE trades
            ADD CONSTRAINT chk_trades_amount_positive_l0
            CHECK (amount_usd > 0);
        RAISE NOTICE 'Added: chk_trades_amount_positive_l0';
    ELSE
        RAISE NOTICE 'Skipped (exists): chk_trades_amount_positive_l0';
    END IF;
END $$;

-- ── trades.entry_price must be positive ──
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_trades_entry_price_positive_l0'
    ) THEN
        ALTER TABLE trades
            ADD CONSTRAINT chk_trades_entry_price_positive_l0
            CHECK (entry_price > 0);
        RAISE NOTICE 'Added: chk_trades_entry_price_positive_l0';
    ELSE
        RAISE NOTICE 'Skipped (exists): chk_trades_entry_price_positive_l0';
    END IF;
END $$;

-- ── trades.side restricted to known values (defensive on enum drift) ──
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_trades_side_l0'
    ) THEN
        ALTER TABLE trades
            ADD CONSTRAINT chk_trades_side_l0
            CHECK (side IN ('buy', 'sell'));
        RAISE NOTICE 'Added: chk_trades_side_l0';
    ELSE
        RAISE NOTICE 'Skipped (exists): chk_trades_side_l0';
    END IF;
END $$;

-- ── portfolio_state.total_capital must be non-negative ──
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_portfolio_state_capital_nonneg_l0'
    ) THEN
        ALTER TABLE portfolio_state
            ADD CONSTRAINT chk_portfolio_state_capital_nonneg_l0
            CHECK (total_capital >= 0);
        RAISE NOTICE 'Added: chk_portfolio_state_capital_nonneg_l0';
    ELSE
        RAISE NOTICE 'Skipped (exists): chk_portfolio_state_capital_nonneg_l0';
    END IF;
END $$;
