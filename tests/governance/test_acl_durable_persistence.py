"""
Phase 2.5 R1 — Durable ACL event persistence tests.

Council R1 contract:
  1. persistence happens BEFORE the violation raises
  2. persistence failure emits CRITICAL but does NOT suppress raise
  3. RedisACLViolation still propagates as BaseException
  4. structured log path remains intact even when DB is down

These tests verify the contract end-to-end.
"""

from __future__ import annotations
import json
import logging
import time
from unittest.mock import patch, MagicMock
import pytest

from governance import redis_acl
from governance.redis_acl import (
    acl, RedisACLViolation, _persist_security_event,
    EVENT_SCHEMA_VERSION,
)


# ── Persistence helper itself ────────────────────────────────────────────

def test_persist_security_event_writes_to_db():
    """Helper inserts a row into security_events with the expected shape."""
    captured_inserts = []

    fake_table = MagicMock()
    fake_table.insert = MagicMock(side_effect=lambda payload: (
        captured_inserts.append(payload), MagicMock(execute=MagicMock(return_value=None))
    )[1])

    fake_db = MagicMock()
    fake_db._get.return_value.table.return_value = fake_table

    payload = {
        "schema_version":   EVENT_SCHEMA_VERSION,
        "event":            "redis_acl_violation",
        "caller":           "engine.signal_generator",
        "attempted_op":     "set",
        "attempted_key":    "l0:bot_paused",
        "allowed_prefixes": ["l2:"],
        "ts":               time.time(),
    }

    # The helper imports `db` lazily inside _persist_security_event;
    # patching sys.modules causes the lazy import to resolve to fake_db.
    with patch.dict("sys.modules", {"database.client": MagicMock(db=fake_db)}):
        _persist_security_event(payload)

    assert len(captured_inserts) == 1
    row = captured_inserts[0]
    assert row["event_type"] == "redis_acl_violation"
    assert row["severity"] == "critical"
    assert row["schema_version"] == EVENT_SCHEMA_VERSION
    assert row["caller"] == "engine.signal_generator"
    assert "event_time" in row
    assert row["payload"] == payload


def test_persist_failure_logs_critical_but_does_not_raise(caplog):
    """R1 contract: persistence failure must NOT raise. Caller must
    continue to its own raise step regardless."""
    caplog.set_level(logging.CRITICAL, logger="redis_acl")

    fake_db = MagicMock()
    fake_db._get.return_value.table.return_value.insert.return_value.execute.side_effect = (
        RuntimeError("supabase down")
    )

    payload = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event":          "redis_acl_violation",
        "caller":         "test",
        "attempted_op":   "set",
        "attempted_key":  "l0:test",
        "allowed_prefixes": [],
        "ts":             time.time(),
    }

    with patch.dict("sys.modules", {"database.client": MagicMock(db=fake_db)}):
        # MUST NOT raise
        _persist_security_event(payload)

    # CRITICAL log emitted with the failure marker
    fail_records = [r for r in caplog.records
                    if r.levelno == logging.CRITICAL
                    and "SECURITY_EVENT_PERSIST_FAILED" in r.getMessage()]
    assert len(fail_records) == 1
    fail_payload = json.loads(
        fail_records[0].getMessage().split("SECURITY_EVENT_PERSIST_FAILED ", 1)[1]
    )
    assert fail_payload["event"] == "security_event_persist_failed"
    assert "supabase down" in fail_payload["persist_error"]
    assert fail_payload["original_event"]["caller"] == "test"


# ── End-to-end: violation persists BEFORE raise ─────────────────────────

@pytest.mark.asyncio
async def test_acl_violation_persists_before_raising():
    """Verify call order: persist → log → raise."""
    call_order = []

    async def trace_set(*args, **kwargs):
        # raw_redis.set should never be reached on a denied op
        call_order.append("raw_redis.set")
        return True

    def trace_persist(payload):
        call_order.append("persist")

    captured_log = []
    original_critical = logging.getLogger("redis_acl").critical

    def trace_log(msg, *args, **kwargs):
        if "SECURITY " in str(msg):
            call_order.append("log")
        original_critical(msg, *args, **kwargs)

    with patch("governance.redis_acl._persist_security_event", new=trace_persist), \
         patch("governance.redis_acl._raw_redis") as fake_raw, \
         patch.object(logging.getLogger("redis_acl"), "critical", side_effect=trace_log):

        fake_raw.set = trace_set

        sg_client = acl.for_module("engine.signal_generator")
        with pytest.raises(RedisACLViolation):
            await sg_client.set("l0:bot_paused", "1")

    # Persistence happened FIRST, then log, then raise (raise unobservable
    # from inside this trace since pytest.raises catches it).
    assert call_order == ["persist", "log"], (
        f"Expected ['persist','log'] (raise observed externally); got {call_order}"
    )
    # Confirm raw_redis was never called — denial happened before any I/O
    assert "raw_redis.set" not in call_order


@pytest.mark.asyncio
async def test_persistence_failure_does_not_swallow_violation():
    """If DB is unreachable, RedisACLViolation must STILL raise.
    Council R1: 'no swallowing, no downgrade to Exception subclass'."""

    def fail_persist(payload):
        # Simulate persist failure path (no raise, but logs CRITICAL inside)
        raise RuntimeError("if persist raised, we would have a bug — but we don't")

    # Wait — _persist_security_event MUST NOT raise per its own contract.
    # So this test instead patches the inner DB call to fail and verifies
    # the helper swallows DB error AND still permits the eventual raise.
    fake_db = MagicMock()
    fake_db._get.return_value.table.return_value.insert.return_value.execute.side_effect = (
        ConnectionError("DB unreachable")
    )

    with patch.dict("sys.modules", {"database.client": MagicMock(db=fake_db)}), \
         patch("governance.redis_acl._raw_redis") as fake_raw:

        sg_client = acl.for_module("engine.signal_generator")
        with pytest.raises(RedisACLViolation) as exc:
            await sg_client.set("l0:bot_paused", "1")

        # Violation propagated exactly as if DB were healthy
        assert exc.value.caller == "engine.signal_generator"
        assert exc.value.attempted_op == "set"
        assert exc.value.attempted_key == "l0:bot_paused"

        # Raw redis must NOT have been called
        fake_raw.set.assert_not_called()


@pytest.mark.asyncio
async def test_persistence_failure_does_not_change_exception_type():
    """The raised type must be RedisACLViolation (BaseException) regardless
    of DB state. Confirms 'no downgrade to Exception subclass'."""
    fake_db = MagicMock()
    fake_db._get.return_value.table.return_value.insert.return_value.execute.side_effect = (
        Exception("any DB failure mode")
    )

    with patch.dict("sys.modules", {"database.client": MagicMock(db=fake_db)}), \
         patch("governance.redis_acl._raw_redis"):

        sg_client = acl.for_module("engine.signal_generator")
        try:
            try:
                await sg_client.set("l0:bot_paused", "1")
            except Exception:
                pytest.fail("RedisACLViolation got downgraded; caught by 'except Exception'")
        except RedisACLViolation as v:
            assert isinstance(v, BaseException)
            assert not isinstance(v, Exception)


# ── Bootstrap-fail path also persists ───────────────────────────────────

def test_bootstrap_fail_persists_event_before_raise():
    """Undeclared module → bootstrap-fail path also persists + logs + raises."""
    captured_payloads = []

    def trace_persist(payload):
        captured_payloads.append(payload)

    with patch("governance.redis_acl._persist_security_event", new=trace_persist):
        with pytest.raises(RedisACLViolation) as exc:
            acl.for_module("attacker.unknown.module")

    assert len(captured_payloads) == 1
    p = captured_payloads[0]
    assert p["event"] == "redis_acl_violation"
    assert p["caller"] == "attacker.unknown.module"
    assert p["attempted_op"] == "bootstrap"
    assert p["attempted_key"] == "<acl_registration>"
    assert exc.value.attempted_op == "bootstrap"


def test_bootstrap_persistence_failure_still_raises():
    """Even if security_events insert fails at bootstrap time, the bootstrap
    must still raise — the system must not boot with an undeclared module."""
    fake_db = MagicMock()
    fake_db._get.return_value.table.return_value.insert.return_value.execute.side_effect = (
        RuntimeError("DB down at boot")
    )

    with patch.dict("sys.modules", {"database.client": MagicMock(db=fake_db)}):
        with pytest.raises(RedisACLViolation) as exc:
            acl.for_module("attacker.unknown.module")

    assert exc.value.attempted_op == "bootstrap"


# ── Schema-version preserved on persisted row ──────────────────────────

def test_persisted_row_carries_schema_version():
    captured = []

    def fake_insert(row):
        captured.append(row)
        return MagicMock(execute=MagicMock(return_value=None))

    fake_db = MagicMock()
    fake_db._get.return_value.table.return_value.insert.side_effect = fake_insert

    with patch.dict("sys.modules", {"database.client": MagicMock(db=fake_db)}):
        _persist_security_event({
            "schema_version":   EVENT_SCHEMA_VERSION,
            "event":            "redis_acl_violation",
            "caller":           "x",
            "attempted_op":     "set",
            "attempted_key":    "l0:k",
            "allowed_prefixes": [],
            "ts":               time.time(),
        })

    assert len(captured) == 1
    assert captured[0]["schema_version"] == EVENT_SCHEMA_VERSION
    assert isinstance(captured[0]["schema_version"], int)
