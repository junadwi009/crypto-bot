"""
governance/consecutive_losses.py
Phase 3 Step 1 — Per-pair consecutive-loss streak tracker.

Stateless lookup. Reads closed trades from the database, sorted by
closed_at descending, counts consecutive losses from the most recent
trade backwards. The streak ends at the first non-loss (win or
break-even).

Consumed by orchestrator._compute_counterfactual: when streak >=
CONSECUTIVE_LOSS_THRESHOLD, a veto is added to the counterfactual.

Phase 3 Step 1 contract:
  - No Redis writes; no new ACL entry required.
  - Reads only from the existing `trades` table.
  - LayerZeroViolation propagates uncaught.
  - Defaults to 0 on any non-L0 error (safe direction: do not invent
    a streak that didn't exist; let the orchestrator proceed with a
    fresh counterfactual based on whatever is verifiable).

Threshold and lookback are module-level constants. Phase-3 Step 2+
work may extend with cooldown semantics; Step 1 stays minimal.
"""

from __future__ import annotations
import logging

from database.client import db
from governance.exceptions import LayerZeroViolation

log = logging.getLogger("consecutive_losses")

# Phase 3 plan: "Consecutive losses >= 3 on a pair -> that pair restricted".
CONSECUTIVE_LOSS_THRESHOLD: int = 3

# Lookback window for the trade scan. Bounded so the read cost is small
# and so very-old losses don't dominate the streak after a long pause.
DEFAULT_LOOKBACK_DAYS: int = 7


async def get_consecutive_loss_count(
    pair: str,
    max_lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> int:
    """Count consecutive losing trades for `pair` from most recent backwards.

    A "loss" is a closed trade with pnl_usd < 0. A win or break-even
    closed trade ends the streak. Returns 0 when no closed trades exist
    for the pair within the lookback window.

    Raises propagation contract:
        LayerZeroViolation propagates uncaught.
        All other exceptions are logged and treated as "no streak data
        available" (returns 0).
    """
    try:
        all_trades = await db.get_trades_for_period(days=max_lookback_days)
    except LayerZeroViolation:
        raise
    except Exception as e:
        log.error("consecutive_losses: db read failed for %s: %s", pair, e)
        return 0

    pair_closed = [
        t for t in (all_trades or [])
        if t.get("pair") == pair and t.get("status") == "closed"
    ]
    if not pair_closed:
        return 0

    # Sort by closed_at descending — most recent first.
    pair_closed.sort(
        key=lambda t: t.get("closed_at") or "",
        reverse=True,
    )

    streak = 0
    for trade in pair_closed:
        try:
            pnl = float(trade.get("pnl_usd") or 0)
        except (TypeError, ValueError):
            # Unparseable pnl ends the streak (treat as not-a-loss);
            # we cannot confirm a loss without numeric data.
            break
        if pnl < 0:
            streak += 1
        else:
            break
    return streak
