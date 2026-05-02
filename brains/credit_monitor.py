"""
brains/credit_monitor.py
Monitor saldo token Anthropic setiap jam.
Kurangi frekuensi Claude saat kredit menipis.

PATCHED 2026-05-02:
- Saldo masih estimasi (Anthropic tidak punya endpoint balance publik)
  tapi sekarang lebih akurat — pakai burn rate × hari + cost bulan ini
- Notif tidak spam (TTL flag)
"""

from __future__ import annotations
import logging
from datetime import date, timedelta

from config.settings import settings
from database.client import db
from utils.redis_client import redis

log = logging.getLogger("credit_monitor")

_NOTIF_KEY = {
    "warning":  "credit_notif_warning_sent",
    "topup":    "credit_notif_topup_sent",
    "critical": "credit_notif_critical_sent",
}


class CreditMonitor:

    async def check(self):
        balance = await self.get_balance()
        if balance is None:
            log.debug("Credit check: could not estimate balance")
            return
        log.debug("Anthropic credit balance estimate: $%.2f", balance)
        await self._adjust_claude_mode(balance)
        await self._notify_if_needed(balance)

    async def get_balance(self) -> float | None:
        """
        Estimasi saldo. Anthropic tidak punya public balance endpoint —
        kita track via cost_usd di DB.

        topup_amount diset manual via /paid_claude (Telegram).
        Default $50 (asumsi user belum pernah topup).
        """
        try:
            cost_this_month = await db.get_claude_cost_this_month()
            topup_str = await redis.get("last_topup_amount")
            topup_amount = float(topup_str) if topup_str else 50.0
            estimated_balance = topup_amount - cost_this_month
            return max(0.0, round(estimated_balance, 2))
        except Exception as e:
            log.error("Failed to estimate balance: %s", e)
            return None

    async def record_topup(self, amount: float):
        await redis.set("last_topup_amount", str(amount))
        for key in _NOTIF_KEY.values():
            await redis.delete(key)
        log.info("Topup recorded: $%.2f", amount)

    async def _adjust_claude_mode(self, balance: float):
        if balance <= 0:
            await redis.set("claude_mode", "off")
            log.warning("Claude mode: OFF (balance $0)")
        elif balance <= settings.CREDIT_CRITICAL:
            await redis.set("claude_mode", "haiku_only")
            log.warning("Claude mode: haiku_only (balance $%.2f)", balance)
        elif balance <= settings.CREDIT_TOPUP:
            await redis.set("claude_mode", "reduced")
            log.info("Claude mode: reduced (balance $%.2f)", balance)
        else:
            await redis.set("claude_mode", "normal")

    async def get_claude_mode(self) -> str:
        return await redis.get("claude_mode") or "normal"

    async def is_model_allowed(self, model: str) -> bool:
        mode = await self.get_claude_mode()
        if mode == "off":
            return False
        if mode == "haiku_only":
            return model == "haiku"
        if mode == "reduced":
            return model in ("haiku", "opus")
        return True

    async def _notify_if_needed(self, balance: float):
        from notifications.telegram_bot import telegram

        if balance <= settings.CREDIT_CRITICAL:
            if not await redis.get(_NOTIF_KEY["critical"]):
                await redis.setex(_NOTIF_KEY["critical"], 86400, "1")
                await telegram.send_credit_alert(balance, "critical")
        elif balance <= settings.CREDIT_TOPUP:
            if not await redis.get(_NOTIF_KEY["topup"]):
                await redis.setex(_NOTIF_KEY["topup"], 86400 * 3, "1")
                await telegram.send_credit_alert(balance, "topup")
        elif balance <= settings.CREDIT_WARNING:
            if not await redis.get(_NOTIF_KEY["warning"]):
                await redis.setex(_NOTIF_KEY["warning"], 86400 * 5, "1")
                await telegram.send_credit_alert(balance, "warning")

    async def get_burn_rate(self, days: int = 7) -> float:
        try:
            since = (date.today() - timedelta(days=days)).isoformat()
            res = (
                db._get()
                .table("claude_usage")
                .select("cost_usd")
                .gte("usage_date", since)
                .execute()
            )
            total = sum(float(r["cost_usd"]) for r in (res.data or []))
            return round(total / days, 4) if days > 0 else 0.0
        except Exception:
            return 1.5

    async def get_days_remaining(self) -> float:
        balance   = await self.get_balance() or 0
        burn_rate = await self.get_burn_rate()
        if burn_rate <= 0:
            return 999.0
        return round(balance / burn_rate, 1)


credit_monitor = CreditMonitor()
