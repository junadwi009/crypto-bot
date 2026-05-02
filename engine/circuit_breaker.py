"""
engine/circuit_breaker.py
Hard stop otomatis saat drawdown > 15% dalam sehari.

PATCHED 2026-05-02:
- Kirim Telegram notif langsung saat trip (sebelumnya hanya event log)
"""

from __future__ import annotations
import logging

from config.settings import settings
from database.client import db
from utils.redis_client import redis

log = logging.getLogger("circuit_breaker")

CB_KEY    = "circuit_breaker_tripped"
CB_REASON = "circuit_breaker_reason"


class CircuitBreaker:

    async def is_tripped(self) -> bool:
        return bool(await redis.get(CB_KEY))

    async def check(self, capital_now: float, capital_start_of_day: float):
        if await self.is_tripped():
            return
        if capital_start_of_day <= 0:
            return

        drawdown = (capital_start_of_day - capital_now) / capital_start_of_day
        if drawdown >= settings.MAX_DAILY_DRAWDOWN:
            await self._trip(
                reason  = f"daily_drawdown_{drawdown:.1%}",
                capital = capital_now,
                drawdown= drawdown,
            )

    async def _trip(self, reason: str, capital: float, drawdown: float):
        await redis.set(CB_KEY, "1")
        await redis.set(CB_REASON, reason)
        await redis.set("bot_paused", "1")

        await db.log_event(
            event_type = "circuit_breaker_tripped",
            message    = f"Circuit breaker tripped: {reason}",
            severity   = "critical",
            data       = {
                "reason":   reason,
                "capital":  capital,
                "drawdown": round(drawdown, 4),
            },
        )

        log.critical("CIRCUIT BREAKER TRIPPED: %s | capital=$%.2f drawdown=%.1f%%",
                     reason, capital, drawdown * 100)

        # Telegram notif langsung
        try:
            from notifications.telegram_bot import telegram
            await telegram.send_circuit_breaker(drawdown, capital)
        except Exception as e:
            log.error("Failed to send CB notif: %s", e)

    async def reset(self):
        await redis.delete(CB_KEY)
        await redis.delete(CB_REASON)
        await redis.delete("bot_paused")

        await db.log_event(
            event_type = "circuit_breaker_reset",
            message    = "Circuit breaker manually reset",
            severity   = "info",
        )
        log.info("Circuit breaker reset")

    async def get_status(self) -> dict:
        tripped = await self.is_tripped()
        reason  = await redis.get(CB_REASON) if tripped else None
        return {
            "tripped": tripped,
            "reason":  reason,
        }


circuit_breaker = CircuitBreaker()
