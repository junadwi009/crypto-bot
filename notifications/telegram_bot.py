"""
notifications/telegram_bot.py
Bot Telegram utama — inisialisasi, polling, dan semua metode send.
"""

from __future__ import annotations
import asyncio
import logging

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config.settings import settings

log = logging.getLogger("telegram")


class TelegramBot:

    def __init__(self):
        self._app: Application | None = None
        self._bot: Bot | None = None

    def _get_app(self) -> Application:
        if self._app is None:
            self._app = (
                Application.builder()
                .token(settings.TELEGRAM_BOT_TOKEN)
                .build()
            )
            self._register_handlers()
        return self._app

    def _get_bot(self) -> Bot:
        if self._bot is None:
            self._bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        return self._bot

    def _register_handlers(self):
        from notifications.handlers import (
            cmd_start, cmd_status, cmd_pause, cmd_resume,
            cmd_reset_cb, cmd_emergency_lock, cmd_trades,
            handle_message, handle_callback,
        )
        app = self._app
        app.add_handler(CommandHandler("start",          cmd_start))
        app.add_handler(CommandHandler("status",         cmd_status))
        app.add_handler(CommandHandler("pause",          cmd_pause))
        app.add_handler(CommandHandler("resume",         cmd_resume))
        app.add_handler(CommandHandler("reset_cb",       cmd_reset_cb))
        app.add_handler(CommandHandler("emergency_lock", cmd_emergency_lock))
        app.add_handler(CommandHandler("trades",         cmd_trades))
        app.add_handler(CallbackQueryHandler(handle_callback))
        app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, handle_message
        ))

    async def run(self):
        """Jalankan bot polling — dipanggil dari main.py sebagai async task."""
        log.info("Telegram bot starting polling...")
        app = self._get_app()
        async with app:
            await app.start()
            await app.updater.start_polling(
                allowed_updates=["message", "callback_query"],
                drop_pending_updates=True,
            )
            # Tunggu sampai di-stop dari luar
            while True:
                await asyncio.sleep(1)

    # ── Send methods ──────────────────────────────────────────────────────

    async def send(self, text: str, parse_mode: str | None = None):
        """Kirim pesan teks ke chat_id owner."""
        try:
            await self._get_bot().send_message(
                chat_id    = settings.TELEGRAM_CHAT_ID,
                text       = text[:4096],
                parse_mode = parse_mode,
            )
        except Exception as e:
            log.error("Telegram send error: %s", e)

    async def send_with_buttons(self, text: str,
                                 buttons: list[list[dict]]):
        """
        Kirim pesan dengan inline keyboard.
        buttons = [[{"text": "label", "url": "..."}, ...], ...]
        """
        keyboard = []
        for row in buttons:
            kb_row = []
            for btn in row:
                if "url" in btn:
                    kb_row.append(InlineKeyboardButton(
                        btn["text"], url=btn["url"]
                    ))
                elif "callback_data" in btn:
                    kb_row.append(InlineKeyboardButton(
                        btn["text"], callback_data=btn["callback_data"]
                    ))
            if kb_row:
                keyboard.append(kb_row)

        try:
            await self._get_bot().send_message(
                chat_id      = settings.TELEGRAM_CHAT_ID,
                text         = text[:4096],
                reply_markup = InlineKeyboardMarkup(keyboard),
            )
        except Exception as e:
            log.error("Telegram send_with_buttons error: %s", e)

    async def send_trade_opened(self, pair: str, side: str, size: float,
                                 price: float, source: str):
        from notifications.messages import trade_opened
        await self.send(trade_opened(pair, side, size, price, source))

    async def send_trade_closed(self, pair: str, pnl: float,
                                 reason: str, exit_price: float):
        from notifications.messages import trade_closed
        await self.send(trade_closed(pair, pnl, reason, exit_price))

    async def send_circuit_breaker(self, drawdown: float, capital: float):
        from notifications.messages import circuit_breaker_tripped
        await self.send(circuit_breaker_tripped(drawdown, capital))

    async def send_tier_upgrade(self, from_tier: str, to_tier: str,
                                 capital: float, days: int):
        from notifications.messages import tier_upgraded
        await self.send(tier_upgraded(from_tier, to_tier, capital, days))

    async def send_credit_alert(self, balance: float, level: str):
        from notifications.messages import claude_credit_warning
        from brains.credit_monitor import credit_monitor

        days_left = await credit_monitor.get_days_remaining()
        text      = claude_credit_warning(balance, days_left, level)

        await self.send_with_buttons(text, [[
            {"text": "Topup Anthropic",
             "url": "https://console.anthropic.com/billing"},
        ], [
            {"text": "Sudah topup",  "callback_data": "paid_claude"},
            {"text": "Stop bot",     "callback_data": "stop_bot"},
        ]])

    async def send_opus_report(self, summary: dict, actions: list):
        from notifications.messages import opus_weekly_report
        await self.send(opus_weekly_report(summary, actions))


# Global instance
telegram = TelegramBot()
