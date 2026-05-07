"""
Phase 2.5 R2 — Reconciliation orphan-lock telemetry tests.

Council-mandated tests:
  - test_overlap_event_contains_age_and_invocation_metadata
  - test_orphaned_lock_emits_critical_event
  - test_invalid_lock_payload_returns_unknown
  - test_orphaned_lock_not_force_cleared

Plus existing overlap behavior must continue passing (verified via
test_overlap_returns_unknown_and_alerts in test_reconciliation.py).
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock, MagicMock
import pytest

from governance import reconciliation as recon


def _build_existing_lock(
    age_seconds: float,
    token: str = "PRIOR_TOK",
    invocation_id: str = "PRIOR_INV",
) -> str:
    """Helper to build a valid existing-lock JSON payload of given age."""
    started_at = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    return json.dumps({
        "token": token, "started_at": started_at,
        "scheduler_invocation_id": invocation_id,
    }, sort_keys=True, separators=(",", ":"))


# ── R2 acceptance test: overlap event metadata ──────────────────────────

@pytest.mark.asyncio
async def test_overlap_event_contains_age_and_invocation_metadata(caplog):
    """Council R2 acceptance: overlap event must include
    scheduler_invocation_id, lock_age_seconds (from persisted started_at),
    lock_ttl_seconds, previous_lock_token, this_invocation_token."""
    caplog.set_level(logging.CRITICAL, logger="reconciliation")

    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(return_value=False)   # NX failed = lock held
    fake_redis.get = AsyncMock(return_value=_build_existing_lock(age_seconds=12.0))
    fake_redis.release_lock = AsyncMock(return_value=True)

    with patch("governance.reconciliation.settings") as fake_settings, \
         patch.object(recon, "redis", fake_redis), \
         patch("notifications.telegram_bot.telegram") as fake_tg:
        fake_settings.PAPER_TRADE = True
        fake_tg.send = AsyncMock()
        result = await recon.reconcile()

    assert result == recon.ReconciliationStatus.UNKNOWN

    # Find the overlap event
    overlap_records = [
        json.loads(r.getMessage().split("SEV1 ", 1)[1])
        for r in caplog.records
        if r.levelno == logging.CRITICAL
        and "reconciliation_overlap" in r.getMessage()
    ]
    assert len(overlap_records) == 1, "Expected exactly one overlap event"
    event = overlap_records[0]

    # All required R2 fields present
    assert event["event"] == "reconciliation_overlap"
    assert event["schema_version"] == 1
    assert "scheduler_invocation_id" in event
    assert event["scheduler_invocation_id"] != "PRIOR_INV"
    assert len(event["scheduler_invocation_id"]) == 32   # UUID4 hex
    assert event["previous_lock_token"] == "PRIOR_TOK"
    assert event["previous_invocation_id"] == "PRIOR_INV"
    assert "this_invocation_token" in event
    assert len(event["this_invocation_token"]) == 32
    assert event["this_invocation_token"] != "PRIOR_TOK"
    # Age derived from PERSISTED started_at, not local process start
    assert isinstance(event["lock_age_seconds"], (int, float))
    assert 11.0 <= event["lock_age_seconds"] <= 14.0   # ~12s ± clock noise
    assert event["lock_ttl_seconds"] == recon.RECON_LOCK_TTL_SECONDS


# ── R2 acceptance test: orphaned-lock SECOND event ──────────────────────

@pytest.mark.asyncio
async def test_orphaned_lock_emits_critical_event(caplog):
    """Council R2 acceptance: when lock_age > TTL, emit reconciliation_orphaned_lock
    as a SECOND critical event (in addition to overlap event)."""
    caplog.set_level(logging.CRITICAL, logger="reconciliation")

    # Build lock that is 100s past TTL (700s ago, TTL=600)
    orphan_age = recon.RECON_LOCK_TTL_SECONDS + 100.0
    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(return_value=False)
    fake_redis.get = AsyncMock(return_value=_build_existing_lock(
        age_seconds=orphan_age, token="ORPHAN_TOK", invocation_id="ORPHAN_INV",
    ))
    fake_redis.release_lock = AsyncMock(return_value=True)

    with patch("governance.reconciliation.settings") as fake_settings, \
         patch.object(recon, "redis", fake_redis), \
         patch("notifications.telegram_bot.telegram") as fake_tg:
        fake_settings.PAPER_TRADE = True
        fake_tg.send = AsyncMock()
        result = await recon.reconcile()

    assert result == recon.ReconciliationStatus.UNKNOWN

    # Find both events
    overlap_count = sum(1 for r in caplog.records
                        if r.levelno == logging.CRITICAL
                        and "reconciliation_overlap" in r.getMessage())
    orphan_records = [
        json.loads(r.getMessage().split("SEV1 ", 1)[1])
        for r in caplog.records
        if r.levelno == logging.CRITICAL
        and "reconciliation_orphaned_lock" in r.getMessage()
    ]
    assert overlap_count == 1
    assert len(orphan_records) == 1, "Expected SECOND critical event for orphan"

    orphan = orphan_records[0]
    assert orphan["event"] == "reconciliation_orphaned_lock"
    assert orphan["schema_version"] == 1
    assert orphan["orphaned_lock_token"] == "ORPHAN_TOK"
    assert orphan["orphaned_invocation_id"] == "ORPHAN_INV"
    assert orphan["lock_age_seconds"] > recon.RECON_LOCK_TTL_SECONDS
    assert orphan["lock_ttl_seconds"] == recon.RECON_LOCK_TTL_SECONDS
    assert "scheduler_invocation_id" in orphan


# ── R2 acceptance test: invalid lock payload ────────────────────────────

@pytest.mark.asyncio
async def test_invalid_lock_payload_returns_unknown(caplog):
    """Council R2 acceptance: malformed payload -> emit invalid event,
    return UNKNOWN, do NOT clear lock."""
    caplog.set_level(logging.CRITICAL, logger="reconciliation")

    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(return_value=False)
    fake_redis.get = AsyncMock(return_value="this is not valid json {{{{")
    # Track delete calls — must NOT be invoked
    fake_redis.delete = AsyncMock()
    fake_redis.release_lock = AsyncMock()

    with patch("governance.reconciliation.settings") as fake_settings, \
         patch.object(recon, "redis", fake_redis), \
         patch("notifications.telegram_bot.telegram") as fake_tg:
        fake_settings.PAPER_TRADE = True
        fake_tg.send = AsyncMock()
        result = await recon.reconcile()

    assert result == recon.ReconciliationStatus.UNKNOWN

    invalid_records = [
        json.loads(r.getMessage().split("SEV1 ", 1)[1])
        for r in caplog.records
        if r.levelno == logging.CRITICAL
        and "reconciliation_lock_payload_invalid" in r.getMessage()
    ]
    assert len(invalid_records) == 1
    assert invalid_records[0]["event"] == "reconciliation_lock_payload_invalid"
    assert "scheduler_invocation_id" in invalid_records[0]
    assert "raw_value_truncated" in invalid_records[0]

    # Critically: lock was NOT cleared. Neither delete nor release_lock called.
    fake_redis.delete.assert_not_called()
    fake_redis.release_lock.assert_not_called()


# ── R2 acceptance test: orphaned lock NOT auto-cleared ──────────────────

@pytest.mark.asyncio
async def test_orphaned_lock_not_force_cleared():
    """Council R2 acceptance: 'No auto-clear authorized. No lock stealing
    authorized. No forced unlock authorized. Human review only.'"""
    orphan_age = recon.RECON_LOCK_TTL_SECONDS + 1000.0
    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(return_value=False)
    fake_redis.get = AsyncMock(return_value=_build_existing_lock(
        age_seconds=orphan_age,
    ))
    fake_redis.delete = AsyncMock()
    fake_redis.release_lock = AsyncMock()

    with patch("governance.reconciliation.settings") as fake_settings, \
         patch.object(recon, "redis", fake_redis), \
         patch("notifications.telegram_bot.telegram") as fake_tg:
        fake_settings.PAPER_TRADE = True
        fake_tg.send = AsyncMock()
        await recon.reconcile()

    # MUST NOT have cleared the orphaned lock
    fake_redis.delete.assert_not_called()
    fake_redis.release_lock.assert_not_called()


# ── Lock value shape & UUID-token preservation (invariant 11) ───────────

@pytest.mark.asyncio
async def test_lock_value_is_structured_json_with_token():
    """Lock value written to Redis is JSON {token, started_at, invocation_id}.
    UUID token field preserves invariant 11 (Lock release ownership strict
    via UUID token)."""
    captured = []

    async def capture_set(key, value, **kwargs):
        if key == recon.RECON_LOCK_KEY:
            captured.append(value)
        return True

    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(side_effect=capture_set)
    fake_redis.get = AsyncMock(return_value=None)
    fake_redis.release_lock = AsyncMock(return_value=True)

    with patch("governance.reconciliation.settings") as fake_settings, \
         patch.object(recon, "redis", fake_redis):
        fake_settings.PAPER_TRADE = True
        await recon.reconcile()

    assert len(captured) == 1
    parsed = json.loads(captured[0])
    assert "token" in parsed
    assert len(parsed["token"]) == 32     # UUID4 hex — invariant 11
    assert "started_at" in parsed
    assert "scheduler_invocation_id" in parsed
    assert len(parsed["scheduler_invocation_id"]) == 32


@pytest.mark.asyncio
async def test_release_uses_full_lock_value_for_compare():
    """Release passes the full lock_value (JSON) to release_lock so the
    Lua compare-and-delete script matches exactly. Token-ownership
    semantics preserved — only this invocation can release."""
    captured_lock_set = []
    captured_release = []

    async def capture_set(key, value, **kwargs):
        # Filter to lock-key writes only; _persist_status also writes
        # to RECON_STATUS_KEY and RECON_LAST_RUN_KEY in CLEAN paths.
        if key == recon.RECON_LOCK_KEY:
            captured_lock_set.append(value)
        return True

    async def capture_release(key, value):
        captured_release.append(value)
        return True

    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(side_effect=capture_set)
    fake_redis.get = AsyncMock(return_value=None)
    fake_redis.release_lock = AsyncMock(side_effect=capture_release)

    with patch("governance.reconciliation.settings") as fake_settings, \
         patch.object(recon, "redis", fake_redis):
        fake_settings.PAPER_TRADE = True
        await recon.reconcile()

    # release_lock argument must equal the lock_value we wrote at acquire
    assert len(captured_lock_set) == 1
    assert len(captured_release) == 1
    assert captured_release[0] == captured_lock_set[0]
