"""
tests/conftest.py
Shared pytest configuration dan fixtures.
"""

import pytest
import os

# Set test env vars sebelum import apapun
os.environ.setdefault("BYBIT_API_KEY",        "test_key")
os.environ.setdefault("BYBIT_API_SECRET",     "test_secret")
os.environ.setdefault("ANTHROPIC_API_KEY",    "sk-ant-test-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN",   "1234567890:test_token")
os.environ.setdefault("TELEGRAM_CHAT_ID",     "123456789")
os.environ.setdefault("SUPABASE_URL",         "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "eyJtest_service_key")
os.environ.setdefault("REDIS_URL",            "redis://localhost:6379")
os.environ.setdefault("BOT_PIN_HASH",         "8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92")
os.environ.setdefault("PAPER_TRADE",          "true")
os.environ.setdefault("INITIAL_CAPITAL",      "213")
os.environ.setdefault("BOT_TIMEZONE",         "Asia/Jakarta")


@pytest.fixture(scope="session")
def event_loop_policy():
    """Use default asyncio event loop."""
    import asyncio
    return asyncio.DefaultEventLoopPolicy()
