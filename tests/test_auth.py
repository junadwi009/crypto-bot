"""
tests/test_auth.py
Unit test untuk sistem autentikasi PIN dan session management.
Jalankan: pytest tests/test_auth.py -v
"""

import hashlib
import pytest
from unittest.mock import AsyncMock, patch


VALID_PIN      = "123456"
VALID_PIN_HASH = hashlib.sha256(VALID_PIN.encode()).hexdigest()
WRONG_PIN      = "000000"
TEST_CHAT_ID   = 123456789


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_settings():
    with patch("notifications.auth.settings") as s:
        s.TELEGRAM_CHAT_ID = TEST_CHAT_ID
        s.BOT_PIN_HASH     = VALID_PIN_HASH
        s.MAX_PIN_ATTEMPTS = 3
        s.SESSION_TTL      = 14400
        s.IDLE_TTL         = 3600
        s.LOCKOUT_TTL      = 900
        yield s


# ── Auth check ────────────────────────────────────────────────────────────────

class TestAuthGuard:

    def test_allowed_chat_correct_id(self, mock_settings):
        from notifications.auth import AuthGuard
        guard = AuthGuard()
        assert guard.is_allowed_chat(TEST_CHAT_ID) is True

    def test_allowed_chat_wrong_id(self, mock_settings):
        from notifications.auth import AuthGuard
        guard = AuthGuard()
        assert guard.is_allowed_chat(999999999) is False

    @pytest.mark.asyncio
    async def test_check_session_no_session(self, mock_settings):
        from notifications.auth import AuthGuard
        guard = AuthGuard()

        with patch("notifications.auth.redis") as mock_redis:
            mock_redis.get    = AsyncMock(return_value=None)
            mock_redis.expire = AsyncMock()

            ok, reason = await guard.check_session(TEST_CHAT_ID)

        assert ok     is False
        assert reason == "need_pin"

    @pytest.mark.asyncio
    async def test_check_session_active_session(self, mock_settings):
        from notifications.auth import AuthGuard
        guard = AuthGuard()

        with patch("notifications.auth.redis") as mock_redis:
            # First get = lockout (None), second get = session ("1")
            mock_redis.get    = AsyncMock(side_effect=[None, "1"])
            mock_redis.expire = AsyncMock()

            ok, reason = await guard.check_session(TEST_CHAT_ID)

        assert ok     is True
        assert reason == "ok"

    @pytest.mark.asyncio
    async def test_check_session_locked_out(self, mock_settings):
        from notifications.auth import AuthGuard
        guard = AuthGuard()

        with patch("notifications.auth.redis") as mock_redis:
            mock_redis.get = AsyncMock(return_value="1")  # lockout exists
            mock_redis.ttl = AsyncMock(return_value=600)

            ok, reason = await guard.check_session(TEST_CHAT_ID)

        assert ok     is False
        assert reason.startswith("locked_")


# ── PIN verification ──────────────────────────────────────────────────────────

class TestPINVerification:

    @pytest.mark.asyncio
    async def test_correct_pin_creates_session(self, mock_settings):
        from notifications.auth import AuthGuard
        guard = AuthGuard()

        with patch("notifications.auth.redis") as mock_redis:
            mock_redis.get    = AsyncMock(return_value=None)
            mock_redis.setex  = AsyncMock()
            mock_redis.delete = AsyncMock()
            mock_redis.incr   = AsyncMock(return_value=1)
            mock_redis.expire = AsyncMock()

            ok, reason = await guard.verify_pin(TEST_CHAT_ID, VALID_PIN)

        assert ok     is True
        assert reason == "ok"
        mock_redis.setex.assert_called()

    @pytest.mark.asyncio
    async def test_wrong_pin_increments_attempts(self, mock_settings):
        from notifications.auth import AuthGuard
        guard = AuthGuard()

        with patch("notifications.auth.redis") as mock_redis:
            mock_redis.get    = AsyncMock(return_value=None)
            mock_redis.incr   = AsyncMock(return_value=1)
            mock_redis.expire = AsyncMock()
            mock_redis.setex  = AsyncMock()
            mock_redis.delete = AsyncMock()

            ok, reason = await guard.verify_pin(TEST_CHAT_ID, WRONG_PIN)

        assert ok     is False
        assert "wrong" in reason
        assert "2" in reason  # 2 remaining after 1 attempt

    @pytest.mark.asyncio
    async def test_three_wrong_pins_trigger_lockout(self, mock_settings):
        from notifications.auth import AuthGuard
        guard = AuthGuard()

        with patch("notifications.auth.redis") as mock_redis:
            mock_redis.get    = AsyncMock(return_value=None)
            mock_redis.incr   = AsyncMock(return_value=3)  # 3rd attempt
            mock_redis.expire = AsyncMock()
            mock_redis.setex  = AsyncMock()
            mock_redis.delete = AsyncMock()

            ok, reason = await guard.verify_pin(TEST_CHAT_ID, WRONG_PIN)

        assert ok     is False
        assert reason == "locked_now"
        # Lockout should be set
        mock_redis.setex.assert_called()

    @pytest.mark.asyncio
    async def test_pin_with_spaces_still_works(self, mock_settings):
        """PIN dengan spasi di awal/akhir harus tetap benar."""
        from notifications.auth import AuthGuard
        guard = AuthGuard()

        with patch("notifications.auth.redis") as mock_redis:
            mock_redis.get    = AsyncMock(return_value=None)
            mock_redis.setex  = AsyncMock()
            mock_redis.delete = AsyncMock()
            mock_redis.incr   = AsyncMock(return_value=1)
            mock_redis.expire = AsyncMock()

            ok, _ = await guard.verify_pin(TEST_CHAT_ID, f"  {VALID_PIN}  ")

        assert ok is True


# ── Confirmation tokens ───────────────────────────────────────────────────────

class TestConfirmationTokens:

    @pytest.mark.asyncio
    async def test_create_and_verify_token(self, mock_settings):
        from notifications.auth import AuthGuard
        guard = AuthGuard()

        with patch("notifications.auth.redis") as mock_redis:
            mock_redis.setex = AsyncMock()
            token = await guard.create_confirmation(TEST_CHAT_ID, "pause_bot")

        assert isinstance(token, str)
        assert len(token) == 8

        with patch("notifications.auth.redis") as mock_redis:
            mock_redis.get    = AsyncMock(return_value="pause_bot")
            mock_redis.delete = AsyncMock()
            action = await guard.verify_confirmation(TEST_CHAT_ID, token)

        assert action == "pause_bot"

    @pytest.mark.asyncio
    async def test_expired_token_returns_none(self, mock_settings):
        from notifications.auth import AuthGuard
        guard = AuthGuard()

        with patch("notifications.auth.redis") as mock_redis:
            mock_redis.get    = AsyncMock(return_value=None)  # expired
            mock_redis.delete = AsyncMock()

            action = await guard.verify_confirmation(TEST_CHAT_ID, "deadtoken")

        assert action is None

    @pytest.mark.asyncio
    async def test_emergency_lock(self, mock_settings):
        from notifications.auth import AuthGuard
        guard = AuthGuard()

        with patch("notifications.auth.redis") as mock_redis:
            mock_redis.delete = AsyncMock()
            mock_redis.setex  = AsyncMock()

            await guard.emergency_lock(TEST_CHAT_ID)

            mock_redis.delete.assert_called()
            mock_redis.setex.assert_called()
