"""
governance/consecutive_losses.py
Phase 3 Step 1 — Per-pair consecutive-loss streak tracker.
Phase 3 Step 2 — Purely-derived cooldown predicate.

Stateless lookup. Reads closed trades from the database; both the
streak count (Step 1) and the cooldown predicate (Step 2) are pure
functions of the trades table — no Redis writes, no ACL additions,
no DB migrations, no persisted state of their own.

Consumed by orchestrator._compute_counterfactual:
  - Step 1: when streak >= CONSECUTIVE_LOSS_THRESHOLD, a streak-loss
    veto is added to the counterfactual.
  - Step 2: when is_in_cooldown(pair) is True, a cooldown veto is
    added (additive — both can fire on the same row).

Phase 3 Step 1 contract:
  - No Redis writes; no new ACL entry required.
  - Reads only from the existing `trades` table.
  - LayerZeroViolation propagates uncaught.
  - Defaults to 0 on any non-L0 error (safe direction: do not invent
    a streak that didn't exist; let the orchestrator proceed with a
    fresh counterfactual based on whatever is verifiable).

Phase 3 Step 2 contract (Council-locked):
  - Architecture: purely derived, read-only. No new write surface.
  - Duration: COOLDOWN_DURATION_MINUTES = 60, fixed.
  - End condition: duration-only. Winning trades after the threshold
    crossing do NOT clear cooldown early.
  - Defaults to False on any non-L0 error (safe direction: do not
    invent a cooldown that doesn't exist).
"""

from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone

from database.client import db
from governance.exceptions import LayerZeroViolation

log = logging.getLogger("consecutive_losses")

# Phase 3 plan: "Consecutive losses >= 3 on a pair -> that pair restricted".
CONSECUTIVE_LOSS_THRESHOLD: int = 3

# Lookback window for the trade scan. Bounded so the read cost is small
# and so very-old losses don't dominate the streak after a long pause.
DEFAULT_LOOKBACK_DAYS: int = 7

# Phase 3 Step 2 — Council-locked: cooldown duration in minutes, fixed.
# Cooldown remains active until:
#   now - last_loss_in_threshold_streak >= COOLDOWN_DURATION_MINUTES
# Winning trades during the window do NOT clear cooldown early.
COOLDOWN_DURATION_MINUTES: int = 60


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


async def is_in_cooldown(
    pair: str,
    max_lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> bool:
    """True iff some contiguous loss-run on `pair` reached
    CONSECUTIVE_LOSS_THRESHOLD AND its most-recent loss closed within
    COOLDOWN_DURATION_MINUTES of now.

    Pure derivation from the `trades` table. No persisted state. Walks
    forward in time tracking the current loss streak; whenever the
    streak is at or above threshold, the trade's closed_at is recorded
    as a candidate `last_loss_in_threshold_streak`. The most recent
    such candidate is compared against `now - duration`.

    Spec (Council, locked): end condition is duration-only — winning
    trades after the threshold-crossing loss do NOT clear cooldown
    early. A win merely resets the streak counter for any *future*
    threshold-crossing.

    Raises propagation contract:
        LayerZeroViolation propagates uncaught.
        All other exceptions are logged and treated as "no cooldown
        data available" (returns False).
    """
    try:
        all_trades = await db.get_trades_for_period(days=max_lookback_days)
    except LayerZeroViolation:
        raise
    except Exception as e:
        log.error("consecutive_losses: cooldown db read failed for %s: %s",
                  pair, e)
        return False

    pair_closed = [
        t for t in (all_trades or [])
        if t.get("pair") == pair and t.get("status") == "closed"
    ]
    if not pair_closed:
        return False

    # Walk forward in time so that runs are detected in chronological
    # order; the LAST update to `most_recent_in_run` is the most recent
    # loss participating in any threshold-reaching run.
    pair_closed.sort(key=lambda t: t.get("closed_at") or "")

    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=COOLDOWN_DURATION_MINUTES,
    )

    streak = 0
    most_recent_in_run: datetime | None = None

    for trade in pair_closed:
        try:
            pnl = float(trade.get("pnl_usd") or 0)
        except (TypeError, ValueError):
            # Unparseable pnl resets the streak (cannot confirm a loss
            # without numeric data).
            streak = 0
            continue

        if pnl < 0:
            streak += 1
            if streak >= CONSECUTIVE_LOSS_THRESHOLD:
                closed_at_str = trade.get("closed_at")
                if closed_at_str:
                    try:
                        ts = datetime.fromisoformat(
                            closed_at_str.replace("Z", "+00:00"),
                        )
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        most_recent_in_run = ts
                    except (ValueError, TypeError):
                        pass
        else:
            streak = 0

    if most_recent_in_run is None:
        return False
    return most_recent_in_run >= cutoff
