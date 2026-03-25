"""
utils/logger.py
Setup logging ke console (Render logs) + Telegram untuk error.
Tidak pakai Sentry — Telegram + Render logs sudah cukup.
Secret tidak pernah muncul di log — semua disanitasi.
"""

import asyncio
import logging
from security.log_sanitizer import SanitizedFormatter


class TelegramErrorHandler(logging.Handler):
    """
    Kirim setiap log ERROR atau CRITICAL langsung ke Telegram.
    Handler ini non-blocking — pakai create_task agar tidak block trading loop.
    """

    def emit(self, record: logging.LogRecord):
        if record.levelno < logging.ERROR:
            return
        try:
            msg = self.format(record)
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._send(msg[:1000]))
        except Exception:
            pass  # Jangan sampai handler error bikin bot crash

    async def _send(self, text: str):
        try:
            from config.settings import settings
            from telegram import Bot
            bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            await bot.send_message(
                chat_id = settings.TELEGRAM_CHAT_ID,
                text    = f"\u26a0\ufe0f ERROR LOG\n\n{text}",
            )
        except Exception:
            pass  # Kalau Telegram gagal, sudah ada Render logs


def setup_logging():
    from config.settings import settings

    level = logging.DEBUG if settings.PAPER_TRADE else logging.INFO

    # Console handler — tampil di Render logs
    console = logging.StreamHandler()
    console.setFormatter(SanitizedFormatter(
        fmt     = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt = "%Y-%m-%d %H:%M:%S",
    ))

    # Telegram handler — hanya untuk ERROR dan CRITICAL
    tg = TelegramErrorHandler()
    tg.setLevel(logging.ERROR)
    tg.setFormatter(SanitizedFormatter(
        fmt     = "[%(levelname)s] %(name)s\n%(message)s",
        datefmt = "%H:%M:%S",
    ))

    logging.basicConfig(level=level, handlers=[console, tg])

    # Kurangi noise dari library pihak ketiga
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("pybit").setLevel(logging.WARNING)
