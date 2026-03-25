"""
notifications/payment_reminder.py
Reminder pembayaran Render.com dan topup Anthropic.
Dikirim setiap hari jam 10:00 WIB jika perlu.
Dipanggil dari schedulers/daily_tasks.py.
"""

from __future__ import annotations
import logging
from datetime import date, timedelta

from config.settings import settings
from database.client import db
from utils.redis_client import redis

log = logging.getLogger("payment_reminder")

# Redis keys
_RENDER_PAID_KEY   = "render_paid"
_RENDER_SNOOZE_KEY = "render_snoozed"

# Render billing: cek H-5, H-2, H-0
RENDER_REMIND_DAYS = [5, 2, 0]


class PaymentReminder:

    async def check_and_send(self):
        """
        Entry point — dipanggil scheduler setiap hari jam 10:00 WIB.
        Cek apakah ada yang perlu diingatkan hari ini.
        """
        log.info("Payment reminder check running")
        await self._check_render()
        await self._check_anthropic()

    # ── Render.com ────────────────────────────────────────────────────────

    async def _check_render(self):
        """Kirim reminder Render jika jatuh tempo mendekat."""
        # Skip kalau sudah dibayar bulan ini
        if await redis.get(_RENDER_PAID_KEY):
            return

        # Skip kalau di-snooze
        if await redis.get(_RENDER_SNOOZE_KEY):
            return

        days_left = self._days_until_render_billing()
        if days_left in RENDER_REMIND_DAYS:
            await self._send_render_reminder(days_left)

    async def _send_render_reminder(self, days_left: int):
        from notifications.telegram_bot import telegram

        fund_balance = await db.get_infra_balance()
        text = self._render_text(days_left, fund_balance)

        await telegram.send_with_buttons(text, [
            [{"text": "Buka halaman pembayaran",
              "url": "https://render.com/billing"}],
            [{"text": "Sudah dibayar",
              "callback_data": "paid_render"},
             {"text": "Ingatkan besok",
              "callback_data": "snooze_render"},
             {"text": "Stop bot",
              "callback_data": "stop_bot"}],
        ])

        await db.log_event(
            event_type = "render_reminder_sent",
            message    = f"Render reminder sent: {days_left} days left",
            data       = {"days_left": days_left,
                          "fund_balance": fund_balance},
        )
        log.info("Render reminder sent: %d days left", days_left)

    def _render_text(self, days_left: int, fund_balance: float) -> str:
        from notifications.messages import render_reminder
        return render_reminder(days_left, fund_balance)

    def _days_until_render_billing(self) -> int:
        """Hitung hari menuju tanggal billing Render."""
        today      = date.today()
        billing_day = settings.RENDER_BILLING_DAY

        # Cari tanggal billing bulan ini atau bulan depan
        try:
            billing_this = today.replace(day=billing_day)
        except ValueError:
            # Bulan tanpa hari tersebut (misal Feb tidak punya 30)
            import calendar
            last_day     = calendar.monthrange(today.year, today.month)[1]
            billing_this = today.replace(day=min(billing_day, last_day))

        if billing_this <= today:
            # Sudah lewat — hitung ke bulan depan
            if today.month == 12:
                billing_next = billing_this.replace(
                    year=today.year + 1, month=1
                )
            else:
                billing_next = billing_this.replace(month=today.month + 1)
            return (billing_next - today).days

        return (billing_this - today).days

    # ── Anthropic API ─────────────────────────────────────────────────────

    async def _check_anthropic(self):
        """
        Cek saldo Claude — reminder jika menipis.
        Credit monitor sudah handle notif, ini hanya top-up jika
        credit monitor belum kirim hari ini.
        """
        from brains.credit_monitor import credit_monitor
        balance = await credit_monitor.get_balance()
        if balance is None:
            return

        # Credit monitor sudah kirim notif sesuai level — skip duplikasi
        # Hanya log untuk audit trail
        log.debug("Anthropic balance check: $%.2f", balance)

    # ── Callback handlers (dipanggil dari telegram handlers.py) ──────────

    async def on_render_paid(self):
        """User klik 'Sudah dibayar' di Telegram."""
        # Set paid sampai billing bulan depan
        days_in_month = 30
        ttl = days_in_month * 86400
        await redis.setex(_RENDER_PAID_KEY, ttl, "1")
        await redis.delete(_RENDER_SNOOZE_KEY)

        await db.log_event(
            event_type = "render_payment_confirmed",
            message    = "Render payment confirmed by user",
        )
        log.info("Render payment confirmed")

    async def on_render_snooze(self):
        """User klik 'Ingatkan besok'."""
        await redis.setex(_RENDER_SNOOZE_KEY, 86400, "1")
        log.info("Render reminder snoozed 1 day")

    async def on_claude_paid(self, amount: float = 50.0):
        """User klik 'Sudah topup' Anthropic."""
        from brains.credit_monitor import credit_monitor
        await credit_monitor.record_topup(amount)
        log.info("Anthropic topup confirmed: $%.2f", amount)


payment_reminder = PaymentReminder()
