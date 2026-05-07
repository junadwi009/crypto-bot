"""
security/secret_guard.py
Validasi semua env var wajib ada sebelum bot mulai.
Jika ada yang kosong → log error dan exit.

PATCHED 2026-05-07 (Phase 1 — L0 kernel migration):
- SESSION_SECRET sekarang WAJIB dan minimum 32 byte. Tanpa ini,
  dashboard_api fall-back ke derivasi dari BOT_PIN_HASH yang
  rainbow-table-able (~20 bit entropy). Council mandate M4.
- LIVE_CONFIRM_TOKEN gate: kalau PAPER_TRADE=false dan token tidak
  diset (atau tidak match LIVE_EXPECTED_TOKEN), boot tetap di paper
  mode tanpa error → cegah operator-error class. Council mandate M7.
- Boot fails (exit 1) bukan warning — settings yang tidak aman
  tidak boleh diizinkan jalan.
"""

import logging
import os
import sys

from config.settings import settings

log = logging.getLogger("secret_guard")

# Minimum entropy untuk SESSION_SECRET. 32 bytes (256-bit) adalah
# floor untuk HMAC key — kurang dari ini = forge-able dalam waktu wajar.
SESSION_SECRET_MIN_BYTES = 32

# LIVE confirmation token — operator harus set ini ke nilai non-default
# agar PAPER_TRADE=false benar-benar dihormati. Mencegah misconfig
# Render env yang tidak sengaja flip ke live trading.
LIVE_CONFIRM_EXPECTED = "I_UNDERSTAND_REAL_MONEY_WILL_MOVE"

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
    # NEW Phase 1:
    "SESSION_SECRET":       os.getenv("SESSION_SECRET", ""),
}


def validate_secrets():
    missing = [key for key, val in REQUIRED.items() if not val or val == "0"]

    if missing:
        for key in missing:
            log.critical("Missing required env var: %s", key)
        log.critical(
            "Bot cannot start — set all required env vars in Render dashboard. "
            "Required: %s",
            ", ".join(REQUIRED.keys()),
        )
        sys.exit(1)

    # Pastikan TELEGRAM_CHAT_ID valid integer
    if settings.TELEGRAM_CHAT_ID == 0:
        log.critical("TELEGRAM_CHAT_ID must be a valid integer — bot cannot start")
        sys.exit(1)

    # SESSION_SECRET strength check (M4)
    session_secret = os.getenv("SESSION_SECRET", "")
    if len(session_secret) < SESSION_SECRET_MIN_BYTES:
        log.critical(
            "SESSION_SECRET too short: %d bytes (minimum %d). "
            "Generate with: openssl rand -base64 48",
            len(session_secret), SESSION_SECRET_MIN_BYTES,
        )
        sys.exit(1)

    # Refuse trivially weak secrets (operator pasted a placeholder)
    weak_patterns = ("changeme", "placeholder", "secret", "password", "x" * 16)
    if any(p in session_secret.lower() for p in weak_patterns):
        log.critical(
            "SESSION_SECRET contains placeholder pattern — refusing to start. "
            "Use openssl rand -base64 48 to generate a real value."
        )
        sys.exit(1)

    # LIVE_CONFIRM_TOKEN gate (M7)
    # Three states:
    #   PAPER_TRADE=true            → no token required, normal start
    #   PAPER_TRADE=false + valid token → live mode authorized
    #   PAPER_TRADE=false + missing token → FORCE paper mode + warn loudly
    if not settings.PAPER_TRADE:
        token = os.getenv("LIVE_CONFIRM_TOKEN", "").strip()
        if token != LIVE_CONFIRM_EXPECTED:
            log.critical(
                "PAPER_TRADE=false but LIVE_CONFIRM_TOKEN missing or wrong. "
                "Forcing PAPER_TRADE=true to prevent accidental live trading. "
                "To enable live, set LIVE_CONFIRM_TOKEN=%s in Render env.",
                LIVE_CONFIRM_EXPECTED,
            )
            # Override the runtime setting — flag becomes paper regardless of env.
            # This is intentional: operator-error class of failure is eliminated
            # by refusing to honor PAPER_TRADE=false without explicit confirmation.
            settings.PAPER_TRADE = True
        else:
            log.warning(
                "LIVE TRADING ENABLED — LIVE_CONFIRM_TOKEN matches expected. "
                "All orders will use real capital."
            )

    if settings.PAPER_TRADE:
        log.warning("PAPER_TRADE=true — no real orders will be placed")

    log.info("All secrets validated OK")
