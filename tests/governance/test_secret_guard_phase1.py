"""
Phase-1 tests for security/secret_guard.

Verifies:
  - Boot fails when SESSION_SECRET is missing or shorter than 32 bytes.
  - Boot fails on weak/placeholder SESSION_SECRET.
  - PAPER_TRADE=false without LIVE_CONFIRM_TOKEN is silently overridden
    back to paper mode (operator-error class eliminated).
  - PAPER_TRADE=false WITH valid LIVE_CONFIRM_TOKEN is honored.

Each test runs in a subprocess with a curated env so we don't pollute
the test runner's import cache (settings is loaded at import time).
"""

from __future__ import annotations
import subprocess
import sys
import textwrap


def _run(env_overrides: dict[str, str], expect_exit_code: int | None = None,
         expect_stderr_contains: str | None = None,
         probe: str | None = None) -> subprocess.CompletedProcess:
    """Run a probe script with a CURATED env (not inherited from parent).

    Inheriting the parent's os.environ leaks any test-runner's SESSION_SECRET
    into the subprocess and defeats the missing-secret test. We pass ONLY
    the keys explicitly listed in env_overrides plus the minimum OS-level
    keys the Python interpreter needs to start (PATH, etc).
    """
    if probe is None:
        probe = textwrap.dedent("""
            from security.secret_guard import validate_secrets
            validate_secrets()
            print("OK validate_secrets passed")
        """)

    import os
    # Bare-minimum env so the interpreter starts; do NOT inherit secrets.
    minimal = {
        k: os.environ[k] for k in
        ("PATH", "PYTHONPATH", "SYSTEMROOT", "TEMP", "TMP", "PROCESSOR_ARCHITECTURE",
         "USERPROFILE", "WINDIR", "COMSPEC", "PATHEXT", "HOME", "LANG", "LC_ALL")
        if k in os.environ
    }
    env = {**minimal, **env_overrides}
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, timeout=15, env=env, text=True,
    )
    if expect_exit_code is not None:
        assert result.returncode == expect_exit_code, (
            f"Expected exit {expect_exit_code}, got {result.returncode}. "
            f"stderr={result.stderr!r}"
        )
    if expect_stderr_contains is not None:
        assert expect_stderr_contains in result.stderr or \
               expect_stderr_contains in result.stdout, (
            f"Expected {expect_stderr_contains!r} in output. "
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
    return result


# Minimum env to satisfy other REQUIRED keys (so we isolate the SESSION_SECRET test)
BASE_ENV = {
    "BYBIT_API_KEY":        "test_key",
    "BYBIT_API_SECRET":     "test_secret",
    "ANTHROPIC_API_KEY":    "sk-ant-test",
    "TELEGRAM_BOT_TOKEN":   "0:TEST",
    "TELEGRAM_CHAT_ID":     "12345",
    "SUPABASE_URL":         "https://example.supabase.co",
    "SUPABASE_SERVICE_KEY": "eyJtest",
    "REDIS_URL":            "redis://localhost",
    "BOT_PIN_HASH":         "0" * 64,
    "PAPER_TRADE":          "true",
}


def test_session_secret_missing_fails_boot():
    env = {**BASE_ENV}
    env.pop("SESSION_SECRET", None)
    result = _run(env, expect_exit_code=1)


def test_session_secret_too_short_fails_boot():
    env = {**BASE_ENV, "SESSION_SECRET": "a" * 16}
    result = _run(env, expect_exit_code=1)


def test_session_secret_placeholder_fails_boot():
    env = {**BASE_ENV, "SESSION_SECRET": "changeme_changeme_changeme_changeme_changeme"}
    result = _run(env, expect_exit_code=1)


def test_session_secret_strong_passes():
    env = {**BASE_ENV, "SESSION_SECRET": "Z" * 48}
    # "Z" * 48 has no weak-pattern substrings; should pass.
    result = _run(env, expect_exit_code=0)


def test_paper_false_without_live_token_forced_to_paper():
    """
    Boot must NOT fail, but PAPER_TRADE must be silently flipped back to true.
    """
    env = {
        **BASE_ENV,
        "PAPER_TRADE": "false",
        "SESSION_SECRET": "Z" * 48,
        # LIVE_CONFIRM_TOKEN intentionally unset
    }
    probe = textwrap.dedent("""
        from security.secret_guard import validate_secrets
        from config.settings import settings
        validate_secrets()
        # After validate, PAPER_TRADE should be True regardless of env
        assert settings.PAPER_TRADE is True, (
            f"PAPER_TRADE not flipped back to true; got {settings.PAPER_TRADE}"
        )
        print("OK paper_trade forced to true")
    """)
    _run(env, expect_exit_code=0, probe=probe)


def test_paper_false_with_correct_live_token_honored():
    env = {
        **BASE_ENV,
        "PAPER_TRADE": "false",
        "SESSION_SECRET": "Z" * 48,
        "LIVE_CONFIRM_TOKEN": "I_UNDERSTAND_REAL_MONEY_WILL_MOVE",
    }
    probe = textwrap.dedent("""
        from security.secret_guard import validate_secrets
        from config.settings import settings
        validate_secrets()
        assert settings.PAPER_TRADE is False, (
            f"PAPER_TRADE should be False with valid token; got {settings.PAPER_TRADE}"
        )
        print("OK live mode honored")
    """)
    _run(env, expect_exit_code=0, probe=probe)
