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
        self._stop_event: asyncio.Event | None = None

    def _get_app(self) -> Application:
        if self._app is None:
            self._app = (
                Application.builder()
                .token(settings.TELEGRAM_BOT_TOKEN)
                # Timeout diset di sini (bukan di start_polling)
                # agar conflict cepat terdeteksi dan polling bisa restart
                .read_timeout(10)
                .write_timeout(10)
                .connect_timeout(10)
                .pool_timeout(10)
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

        # Step 1: Tunggu sampai instance lama benar-benar mati.
        # Menggunakan Redis lock agar tidak ada 2 instance polling bersamaan.
        await self._wait_for_deploy_lock()

        # Step 2: Hapus webhook — wajib sebelum polling
        await self._clear_webhook_with_retry()

        # Step 3: Jeda singkat agar Telegram server menutup koneksi lama
        log.info("Telegram: waiting 5s for Telegram server to release old connection...")
        await asyncio.sleep(5)

        app = self._get_app()
        self._stop_event = asyncio.Event()

        async with app:
            await app.start()
            await app.updater.start_polling(
                allowed_updates=["message", "callback_query"],
                drop_pending_updates=True,
            )
            log.info("Telegram bot polling active")

            try:
                await self._stop_event.wait()
            except asyncio.CancelledError:
                log.info("Telegram polling task cancelled — stopping...")
            finally:
                await app.updater.stop()
                await app.stop()
                log.info("Telegram bot stopped cleanly")

    async def _wait_for_deploy_lock(self, timeout: int = 60):
        """
        Gunakan Redis sebagai deployment lock.
        - Instance lama set 'tg_polling_active' dengan TTL 60 detik saat shutdown.
        - Instance baru tunggu sampai key itu expire / hilang.
        - Setelah itu, instance baru set key tersebut untuk dirinya sendiri.
        Ini mencegah 2 instance polling Telegram bersamaan.
        """
        from utils.redis_client import redis

        lock_key = "tg_polling_active"
        waited = 0
        interval = 3

        while waited < timeout:
            lock = await redis.get(lock_key)
            if not lock:
                break
            log.info(
                "Telegram: deploy lock active (old instance still running) — "
                "waiting... (%ds/%ds)", waited, timeout
            )
            await asyncio.sleep(interval)
            waited += interval

        if waited >= timeout:
            log.warning(
                "Telegram: deploy lock timeout after %ds — "
                "forcing start (old instance may have crashed)", timeout
            )

        # Klaim lock untuk instance ini — TTL 120 detik sebagai safety net.
        # Di-refresh saat polling aktif atau dihapus saat shutdown.
        await redis.setex(lock_key, 120, "1")
        log.info("Telegram: deploy lock acquired — this instance owns polling")

    async def _clear_webhook_with_retry(self, max_attempts: int = 5):
        """
        Hapus webhook + tunggu sampai Telegram konfirmasi tidak ada
        polling aktif. Retry dengan exponential backoff.
        delete_webhook() tidak memutus getUpdates yang sedang berjalan —
        kita perlu poll sekali dengan timeout=0 untuk 'mengambil alih'
        lalu biarkan instance lama timeout sendiri.
        """
        for attempt in range(1, max_attempts + 1):
            try:
                bot = self._get_bot()
                await bot.delete_webhook(drop_pending_updates=True)
                log.info("Telegram: webhook cleared (attempt %d)", attempt)

                # Lakukan satu getUpdates dengan timeout=0 untuk memaksa
                # Telegram memutus koneksi polling instance lama
                try:
                    await bot.get_updates(offset=-1, timeout=0,
                                          allowed_updates=["message"])
                    log.info("Telegram: old polling connection displaced")
                except Exception:
                    # Error di sini adalah normal — instance lama mungkin
                    # langsung conflict balik, tapi kita sudah 'menang'
                    pass

                return  # Berhasil — lanjut

            except Exception as e:
                wait = 2 ** attempt  # 2, 4, 8, 16, 32 detik
                log.warning(
                    "Telegram: webhook clear failed (attempt %d/%d): %s — "
                    "retrying in %ds",
                    attempt, max_attempts, e, wait
                )
                await asyncio.sleep(wait)

        log.error("Telegram: could not clear webhook after %d attempts — "
                  "proceeding anyway", max_attempts)

    async def stop(self):
        """Hentikan polling dari luar (dipanggil saat graceful shutdown)."""
        # Release deploy lock agar instance baru bisa langsung mulai polling
        try:
            from utils.redis_client import redis
            await redis.delete("tg_polling_active")
            log.info("Telegram: deploy lock released")
        except Exception as e:
            log.warning("Telegram: failed to release deploy lock: %s", e)

        if self._stop_event:
            self._stop_event.set()

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