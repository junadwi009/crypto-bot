"""
notifications/auth.py
PIN auth + session management untuk Telegram bot.
3 layer: chat_id whitelist → PIN → session TTL.
"""

from __future__ import annotations
import hashlib
import logging
import time

from config.settings import settings
from utils.redis_client import redis

log = logging.getLogger("auth")

_SESSION_KEY   = "session:{}"
_ATTEMPTS_KEY  = "attempts:{}"
_LOCKOUT_KEY   = "lockout:{}"
_CONFIRM_KEY   = "confirm:{}:{}"


class AuthGuard:

    # ── Layer 1: Chat ID ─────────────────────────────────────────────────

    def is_allowed_chat(self, chat_id: int) -> bool:
        return chat_id == settings.TELEGRAM_CHAT_ID

    # ── Layer 2 & 3: PIN + session ───────────────────────────────────────

    async def check_session(self, chat_id: int) -> tuple[bool, str]:
        """
        Cek apakah sesi aktif.
        Return (has_session, status_reason).
        """
        # Cek lockout
        if await redis.get(_LOCKOUT_KEY.format(chat_id)):
            ttl = await redis.ttl(_LOCKOUT_KEY.format(chat_id))
            return False, f"locked_{max(ttl, 0)}"

        # Cek session
        if await redis.get(_SESSION_KEY.format(chat_id)):
            # Refresh idle timer
            await redis.expire(
                _SESSION_KEY.format(chat_id), settings.IDLE_TTL
            )
            return True, "ok"

        return False, "need_pin"

    async def verify_pin(self, chat_id: int, pin: str) -> tuple[bool, str]:
        """
        Verifikasi PIN.
        Return (success, reason).
        """
        # Cek lockout
        if await redis.get(_LOCKOUT_KEY.format(chat_id)):
            ttl = await redis.ttl(_LOCKOUT_KEY.format(chat_id))
            return False, f"locked_{max(ttl, 0)}"

        # Hash dan bandingkan
        pin_hash = hashlib.sha256(pin.strip().encode()).hexdigest()

        if pin_hash != settings.BOT_PIN_HASH:
            # Increment attempts
            attempts = await redis.incr(_ATTEMPTS_KEY.format(chat_id))
            await redis.expire(_ATTEMPTS_KEY.format(chat_id), 300)

            remaining = settings.MAX_PIN_ATTEMPTS - int(attempts)
            if remaining <= 0:
                # Lockout
                await redis.setex(
                    _LOCKOUT_KEY.format(chat_id),
                    settings.LOCKOUT_TTL, "1"
                )
                await redis.delete(_ATTEMPTS_KEY.format(chat_id))
                log.warning("Account locked for chat_id=%d", chat_id)
                return False, "locked_now"

            return False, f"wrong_{remaining}_remaining"

        # PIN benar — buat session
        await redis.delete(_ATTEMPTS_KEY.format(chat_id))
        await redis.setex(
            _SESSION_KEY.format(chat_id),
            settings.SESSION_TTL, "1"
        )
        log.info("Session created for chat_id=%d", chat_id)
        return True, "ok"

    async def end_session(self, chat_id: int):
        """Hapus session — force logout."""
        await redis.delete(_SESSION_KEY.format(chat_id))

    async def emergency_lock(self, chat_id: int):
        """Kunci semua akses segera — /emergency_lock."""
        await redis.delete(_SESSION_KEY.format(chat_id))
        await redis.setex(_LOCKOUT_KEY.format(chat_id), 3600, "emergency")
        log.warning("Emergency lock activated for chat_id=%d", chat_id)

    # ── Confirmation tokens (untuk aksi level 2) ─────────────────────────

    async def create_confirmation(self, chat_id: int,
                                   action: str) -> str:
        """Buat token konfirmasi sementara (30 detik)."""
        token = hashlib.sha256(
            f"{chat_id}{action}{time.time()}".encode()
        ).hexdigest()[:8]
        await redis.setex(
            _CONFIRM_KEY.format(chat_id, token), 30, action
        )
        return token

    async def verify_confirmation(self, chat_id: int,
                                   token: str) -> str | None:
        """Verifikasi token konfirmasi. Return action atau None."""
        key    = _CONFIRM_KEY.format(chat_id, token)
        action = await redis.get(key)
        if action:
            await redis.delete(key)
            return action
        return None

    def get_lockout_minutes(self) -> int:
        return settings.LOCKOUT_TTL // 60


auth = AuthGuard()
