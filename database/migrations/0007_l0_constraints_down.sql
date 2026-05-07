-- ============================================================
-- Down migration: 0007_l0_constraints_down.sql
-- Reverses 0007_l0_constraints.sql.
--
-- USE WITH CAUTION. Removing L0 constraints removes the DB-level
-- defense for invariants enforced in governance/safety_kernel.
-- Application code will continue to enforce them, but a malformed
-- direct SQL write or a future contributor who removes the kernel
-- check will no longer be caught.
--
-- Run only if a follow-up migration explicitly replaces these
-- with stricter or differently-shaped constraints.
-- ============================================================

ALTER TABLE strategy_params
    DROP CONSTRAINT IF EXISTS chk_strategy_params_position_multiplier_l0;

ALTER TABLE claude_usage
    DROP CONSTRAINT IF EXISTS chk_claude_usage_cost_nonneg_l0;

ALTER TABLE trades
    DROP CONSTRAINT IF EXISTS chk_trades_pnl_sanity_l0;

ALTER TABLE trades
    DROP CONSTRAINT IF EXISTS chk_trades_amount_positive_l0;

ALTER TABLE trades
    DROP CONSTRAINT IF EXISTS chk_trades_entry_price_positive_l0;

ALTER TABLE trades
    DROP CONSTRAINT IF EXISTS chk_trades_side_l0;

ALTER TABLE portfolio_state
    DROP CONSTRAINT IF EXISTS chk_portfolio_state_capital_nonneg_l0;
