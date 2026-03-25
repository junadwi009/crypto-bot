"""
security/log_sanitizer.py
Sanitasi log — pastikan secret tidak pernah muncul di output.
Dipakai oleh logger.py sebagai custom Formatter.
"""

from __future__ import annotations
import logging
import os
import re

# ── Pattern secret yang harus diredact ───────────────────────────────────────

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Anthropic API key
    (re.compile(r"sk-ant-[a-zA-Z0-9\-_]{20,}"), "[ANTHROPIC_KEY]"),

    # Telegram bot token: angka:string
    (re.compile(r"\b\d{8,12}:[a-zA-Z0-9\-_]{30,}\b"), "[TG_TOKEN]"),

    # JWT / Supabase service key (eyJ...)
    (re.compile(r"eyJ[a-zA-Z0-9\-_=]{40,}"), "[SUPABASE_KEY]"),

    # Bybit API key/secret (32 karakter hex/alphanum)
    (re.compile(r"\b[a-zA-Z0-9]{32}\b"), "[API_KEY]"),

    # Redis URL dengan password
    (re.compile(r"rediss?://[^@\s]+@"), "redis://[REDACTED]@"),

    # URL dengan password/token di query string
    (re.compile(r"(token|key|secret|password)=[^&\s]{4,}", re.IGNORECASE),
     r"\1=[REDACTED]"),

    # IPv4 address lengkap dengan port (Render internal IP)
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{4,5}\b"), "[IP:PORT]"),
]

# Ambil nilai secret aktual dari env untuk exact-match redaction
def _build_exact_patterns() -> list[tuple[re.Pattern, str]]:
    """Build pattern dari nilai secret aktual di env vars."""
    exact = []
    secret_env_vars = [
        "ANTHROPIC_API_KEY",
        "BYBIT_API_KEY",
        "BYBIT_API_SECRET",
        "TELEGRAM_BOT_TOKEN",
        "SUPABASE_SERVICE_KEY",
        "BOT_PIN_HASH",
    ]
    for var in secret_env_vars:
        value = os.getenv(var, "")
        if value and len(value) >= 8:
            exact.append((
                re.compile(re.escape(value)),
                f"[{var}]"
            ))
    return exact

_EXACT_PATTERNS = _build_exact_patterns()


def sanitize(text: str) -> str:
    """
    Hapus semua secret dari string.
    Diapply ke setiap log message sebelum ditulis.
    """
    if not text:
        return text

    # Exact match dulu (nilai aktual dari env)
    for pattern, replacement in _EXACT_PATTERNS:
        text = pattern.sub(replacement, text)

    # Pattern regex umum
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)

    return text


class SanitizedFormatter(logging.Formatter):
    """
    Custom log formatter yang sanitasi setiap message.
    Dipakai di utils/logger.py.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Sanitasi message utama
        record.msg = sanitize(str(record.msg))

        # Sanitasi args jika ada
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    sanitize(str(a)) if isinstance(a, str) else a
                    for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: sanitize(str(v)) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }

        # Format dengan formatter parent
        formatted = super().format(record)

        # Sanitasi sekali lagi pada hasil format (catch sisa)
        return sanitize(formatted)
