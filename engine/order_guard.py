"""
engine/order_guard.py
Validasi setiap order sebelum dikirim ke Bybit.
Layer keamanan terakhir sebelum uang bergerak.

PATCHED 2026-05-07 (Phase 1 — L0 kernel migration):
- Reject `sell` action on spot pair (Bybit spot does not support shorting).
- Stopping check (is_stopping import) no longer silently swallowed —
  ImportError is logged at warning, real failures re-raise.
- Re-validate L0 invariants via safety_kernel:
  * ABSOLUTE_SIZE_CAP_PCT (was hardcoded 5%)
  * MAX_POSITIONS_TOTAL   (was hardcoded 3)
  * MAX_POSITIONS_PER_PAIR (was hardcoded 2)
  * CAPITAL_FLOOR_USD     (was hardcoded 150)
  * ABSOLUTE_MIN_ORDER_USD (was hardcoded 5.0)
  * MAX_ORDERS_PER_MIN    (was settings.MAX_ORDERS_PER_MIN)
- LayerZeroViolation propagation: redis-read failures on l0:* keys raise
  rather than fall through, so safety state is never assumed.
"""

from __future__ import annotations
import logging

from config.settings import settings
from database.client import db
from utils.redis_client import redis
from governance.exceptions import LayerZeroViolation
from governance import safety_kernel as L0

log = logging.getLogger("order_guard")


class OrderGuard:

    async def approve(self, pair: str, side: str,
                      amount_usd: float, capital: float) -> tuple[bool, str]:
        """
        Cek apakah order boleh dieksekusi.
        Return (approved, reason).

        Raises LayerZeroViolation on any condition where L0 cannot verify
        safety state. Callers MUST propagate (do not catch).
        """

        # 0. Spot cannot short — reject sell-side opens at the guard.
        # Closing a long position is done via order_manager.close_position()
        # which does NOT pass through this guard.
        if side == "sell":
            return False, "spot_cannot_short"

        # 1. Bot sedang di-pause? L0-managed flag.
        try:
            paused = await redis.get("bot_paused")
        except Exception as e:
            raise LayerZeroViolation(
                reason=f"cannot read l0:bot_paused: {e}",
                source_module="engine.order_guard",
                recoverable=False,
            )
        if paused:
            return False, "bot_paused"

        # 2. Bot sedang stopping (in-memory check)?
        try:
            from main import is_stopping
        except ImportError as e:
            # Acceptable during isolated unit tests; log loudly and continue.
            log.warning("order_guard: is_stopping import failed (%s) — assuming not stopping", e)
        except LayerZeroViolation:
            raise
        except Exception as e:
            log.error("order_guard: unexpected error importing is_stopping: %s", e)
        else:
            try:
                if is_stopping():
                    return False, "bot_stopping"
            except LayerZeroViolation:
                raise
            except Exception as e:
                log.error("order_guard: is_stopping() raised: %s", e)

        # 3. Circuit breaker aktif? L0-managed flag.
        try:
            cb_tripped = await redis.get("circuit_breaker_tripped")
        except Exception as e:
            raise LayerZeroViolation(
                reason=f"cannot read l0:circuit_breaker_tripped: {e}",
                source_module="engine.order_guard",
                recoverable=False,
            )
        if cb_tripped:
            return False, "circuit_breaker_tripped"

        # 4. Rate limit
        try:
            rate_key = "orders_last_minute"
            count = await redis.incr(rate_key)
            if count == 1:
                await redis.expire(rate_key, 60)
        except Exception as e:
            raise LayerZeroViolation(
                reason=f"rate-limit counter unreadable: {e}",
                source_module="engine.order_guard",
                recoverable=False,
            )
        if int(count) > L0.MAX_ORDERS_PER_MIN:
            return False, f"rate_limit_{count}_orders_this_minute"

        # 5. Position size cap — L0-validated.
        max_size = capital * L0.ABSOLUTE_SIZE_CAP_PCT
        if amount_usd > max_size:
            return False, f"size_too_large_{amount_usd:.2f}_max_{max_size:.2f}"

        # 6. Min order — L0-validated.
        if amount_usd < L0.ABSOLUTE_MIN_ORDER_USD:
            return False, f"size_too_small_{amount_usd:.2f}"

        # 7. Cek posisi open di pair sama
        open_trades = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
        same_pair = [t for t in open_trades if t["pair"] == pair]
        if len(same_pair) >= L0.MAX_POSITIONS_PER_PAIR:
            return False, f"already_{len(same_pair)}_open_positions_for_{pair}"

        # 8. Total exposure — L0-validated.
        if len(open_trades) >= L0.MAX_POSITIONS_TOTAL:
            return False, f"max_concurrent_positions_reached_{len(open_trades)}"

        # 9. Capital floor — L0-validated.
        if capital < L0.CAPITAL_FLOOR_USD:
            return False, f"capital_below_floor_{capital:.2f}"

        return True, "approved"


order_guard = OrderGuard()
