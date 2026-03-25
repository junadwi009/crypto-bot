"""
security/secret_guard.py
Validasi semua env var wajib ada sebelum bot mulai.
Jika ada yang kosong → log error dan exit.
"""

import logging
import sys
from config.settings import settings

log = logging.getLogger("secret_guard")

REQUIRED = {
    "BYBIT_API_KEY":        settings.BYBIT_API_KEY,
    "BYBIT_API_SECRET":     settings.BYBIT_API_SECRET,
    "ANTHROPIC_API_KEY":    settings.ANTHROPIC_API_KEY,
    "TELEGRAM_BOT_TOKEN":   settings.TELEGRAM_BOT_TOKEN,
    "TELEGRAM_CHAT_ID":     str(settings.TELEGRAM_CHAT_ID),
    "SUPABASE_URL":         settings.SUPABASE_URL,
    "SUPABASE_SERVICE_KEY": settings.SUPABASE_SERVICE_KEY,
    "REDIS_URL":            settings.REDIS_URL,
    "BOT_PIN_HASH":         settings.BOT_PIN_HASH,
}


def validate_secrets():
    missing = [key for key, val in REQUIRED.items() if not val or val == "0"]

    if missing:
        for key in missing:
            log.critical("Missing required env var: %s", key)
        log.critical("Bot cannot start — set all required env vars in Render dashboard")
        sys.exit(1)

    # Pastikan TELEGRAM_CHAT_ID valid integer
    if settings.TELEGRAM_CHAT_ID == 0:
        log.critical("TELEGRAM_CHAT_ID must be a valid integer — bot cannot start")
        sys.exit(1)

    # Warn jika masih paper trade
    if settings.PAPER_TRADE:
        log.warning("PAPER_TRADE=true — no real orders will be placed")

    log.info("All secrets validated OK")
