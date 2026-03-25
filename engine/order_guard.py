"""
engine/order_guard.py
Validasi setiap order sebelum dikirim ke Bybit.
Layer keamanan terakhir sebelum uang bergerak.
"""

from __future__ import annotations
import logging

from config.settings import settings
from database.client import db
from utils.redis_client import redis

log = logging.getLogger("order_guard")


class OrderGuard:

    async def approve(self, pair: str, side: str,
                      amount_usd: float, capital: float) -> tuple[bool, str]:
        """
        Cek apakah order boleh dieksekusi.
        Return (approved, reason).
        """

        # 1. Bot sedang di-pause?
        if await redis.get("bot_paused"):
            return False, "bot_paused"

        # 2. Bot sedang stopping?
        if await redis.get("bot_stopping"):
            return False, "bot_stopping"

        # 3. Circuit breaker aktif?
        if await redis.get("circuit_breaker_tripped"):
            return False, "circuit_breaker_tripped"

        # 4. Rate limit: max order per menit
        rate_key = f"orders_last_minute"
        count    = await redis.incr(rate_key)
        if count == 1:
            await redis.expire(rate_key, 60)
        if int(count) > settings.MAX_ORDERS_PER_MIN:
            return False, f"rate_limit_{count}_orders_this_minute"

        # 5. Position size: max 5% modal per trade
        max_size = capital * 0.05
        if amount_usd > max_size:
            return False, f"size_too_large_{amount_usd:.2f}_max_{max_size:.2f}"

        # 6. Minimum order size
        if amount_usd < 5.0:
            return False, f"size_too_small_{amount_usd:.2f}"

        # 7. Cek berapa posisi open di pair yang sama
        open_trades = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
        same_pair   = [t for t in open_trades if t["pair"] == pair]
        if len(same_pair) >= 2:
            return False, f"already_{len(same_pair)}_open_positions_for_{pair}"

        # 8. Total exposure: max 3 posisi open sekaligus
        if len(open_trades) >= 3:
            return False, f"max_concurrent_positions_reached_{len(open_trades)}"

        # 9. Capital floor: jangan trade jika modal di bawah $150
        if capital < 150:
            return False, f"capital_below_floor_{capital:.2f}"

        return True, "approved"


order_guard = OrderGuard()
