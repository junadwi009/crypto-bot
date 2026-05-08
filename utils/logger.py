"""
utils/logger.py
Setup logging ke console (Render logs) + Telegram untuk error.

PATCHED 2026-05-02:
- Pakai asyncio.get_running_loop() (deprecated get_event_loop di 3.12)
- Telegram emit dijaga agar tidak loop forever (handler error → log lagi → loop)
"""

import asyncio
import logging
from security.log_sanitizer import SanitizedFormatter


class TelegramErrorHandler(logging.Handler):
    """
    Kirim setiap log ERROR atau CRITICAL ke Telegram.
    Non-blocking — pakai create_task. Resilient to 'no running loop' error.
    """

    # Flag agar handler tidak rekursif (Telegram error juga di-log)
    _suppress = False

    def emit(self, record: logging.LogRecord):
        if record.levelno < logging.ERROR:
            return
        if TelegramErrorHandler._suppress:
            return
        try:
            msg = self.format(record)
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # Tidak ada loop yang berjalan (misal di tes synchronous)
                return
            if loop.is_running():
                loop.create_task(self._send(msg[:1000]))
        except Exception:
            pass  # Jangan sampai handler error bikin bot crash

    async def _send(self, text: str):
        TelegramErrorHandler._suppress = True
        try:
            from config.settings import settings
            from telegram import Bot
            bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            await bot.send_message(
                chat_id = settings.TELEGRAM_CHAT_ID,
                text    = f"\u26a0\ufe0f ERROR LOG\n\n{text}",
            )
        except Exception:
            pass
        finally:
            TelegramErrorHandler._suppress = False


def setup_logging():
    from config.settings import settings

    level = logging.DEBUG if settings.PAPER_TRADE else logging.INFO

    console = logging.StreamHandler()
    console.setFormatter(SanitizedFormatter(
        fmt     = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt = "%Y-%m-%d %H:%M:%S",
    ))

    tg = TelegramErrorHandler()
    tg.setLevel(logging.ERROR)
    tg.setFormatter(SanitizedFormatter(
        fmt     = "[%(levelname)s] %(name)s\n%(message)s",
        datefmt = "%H:%M:%S",
    ))

    logging.basicConfig(level=level, handlers=[console, tg])

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("pybit").setLevel(logging.WARNING)
    # HTTP/2 transport stack used internally by httpx → supabase-py.
    # `hpack` floods ~40 DEBUG lines per request when root level=DEBUG;
    # `h2` is its parent protocol library. Silence at WARNING.
    logging.getLogger("hpack").setLevel(logging.WARNING)
    logging.getLogger("h2").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
