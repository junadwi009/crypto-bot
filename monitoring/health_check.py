"""
monitoring/health_check.py
Cek status semua komponen bot secara berkala.
Dipanggil dari FastAPI /health endpoint di main.py
dan dari seven_day_tracker untuk monitoring awal live.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone

from config.settings import settings
from utils.redis_client import redis

log = logging.getLogger("health_check")


class HealthChecker:

    async def full_check(self) -> dict:
        """
        Jalankan semua health check sekaligus.
        Return dict status setiap komponen.
        """
        checks = await _gather_all(
            ("database",    self._check_database()),
            ("redis",       self._check_redis()),
            ("bybit",       self._check_bybit()),
            ("telegram",    self._check_telegram()),
            ("scheduler",   self._check_scheduler()),
            ("circuit_cb",  self._check_circuit_breaker()),
            ("claude_mode", self._check_claude_mode()),
        )

        all_ok    = all(c["ok"] for c in checks.values())
        critical  = [k for k, v in checks.items()
                     if not v["ok"] and k in ("database", "redis", "bybit")]

        return {
            "ok":          all_ok,
            "critical":    critical,
            "paper_trade": settings.PAPER_TRADE,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "components":  checks,
        }

    async def quick_check(self) -> dict:
        """
        Check ringan — hanya komponen kritis.
        Dipakai oleh FastAPI /health endpoint (dipanggil Render tiap 30 detik).
        """
        db_ok     = await self._check_database()
        redis_ok  = await self._check_redis()
        paused    = bool(await redis.get("bot_paused"))
        stopping  = bool(await redis.get("bot_stopping"))

        status = "stopping" if stopping else "paused" if paused else "ok"
        if not db_ok["ok"] or not redis_ok["ok"]:
            status = "degraded"

        return {
            "status":      status,
            "paper_trade": settings.PAPER_TRADE,
            "db":          db_ok["ok"],
            "redis":       redis_ok["ok"],
        }

    # ── Individual checks ─────────────────────────────────────────────

    async def _check_database(self) -> dict:
        try:
            from database.client import db
            await db.ping()
            capital = await db.get_current_capital()
            return {"ok": True, "capital": capital}
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}

    async def _check_redis(self) -> dict:
        try:
            await redis.ping()
            mode = await redis.get("claude_mode") or "normal"
            return {"ok": True, "claude_mode": mode}
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}

    async def _check_bybit(self) -> dict:
        try:
            from exchange.bybit_client import bybit
            await bybit.ping()
            return {"ok": True, "testnet": settings.BYBIT_TESTNET}
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}

    async def _check_telegram(self) -> dict:
        try:
            from telegram import Bot
            bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            me  = await bot.get_me()
            return {"ok": True, "username": f"@{me.username}"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}

    async def _check_scheduler(self) -> dict:
        try:
            # Scheduler dianggap hidup kalau Redis bisa di-ping
            # (scheduler berjalan dalam process yang sama)
            last_snap = await redis.get("last_scheduler_tick")
            return {"ok": True, "last_tick": last_snap or "unknown"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}

    async def _check_circuit_breaker(self) -> dict:
        try:
            from engine.circuit_breaker import circuit_breaker
            status = await circuit_breaker.get_status()
            return {
                "ok":      not status["tripped"],
                "tripped": status["tripped"],
                "reason":  status.get("reason"),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}

    async def _check_claude_mode(self) -> dict:
        try:
            mode = await redis.get("claude_mode") or "normal"
            from brains.credit_monitor import credit_monitor
            balance   = await credit_monitor.get_balance() or 0
            days_left = await credit_monitor.get_days_remaining()
            return {
                "ok":       mode != "off",
                "mode":     mode,
                "balance":  balance,
                "days_est": days_left,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}


async def _gather_all(*coros_with_keys) -> dict:
    """Run semua coroutine dan kumpulkan hasilnya."""
    import asyncio
    keys    = [k for k, _ in coros_with_keys]
    coros   = [c for _, c in coros_with_keys]
    results = await asyncio.gather(*coros, return_exceptions=True)
    out = {}
    for key, result in zip(keys, results):
        if isinstance(result, Exception):
            out[key] = {"ok": False, "error": str(result)[:80]}
        else:
            out[key] = result
    return out


health_checker = HealthChecker()
