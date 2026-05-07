"""
Phase-2 tests for governance/redis_acl.

Council-mandated acceptance tests:
  - Module not in CALLER_PREFIX_RULES cannot bootstrap (boot-fail)
  - L2 caller cannot write l0:* keys (cross-layer write blocked)
  - Read violations same severity as writes
  - Self-declared layer string cannot bypass (no `layer=` parameter)
  - Violation emits structured CRITICAL event AND raises BaseException
  - eval is private; only release_lock public; Lua script literal frozen
  - Schema version present on every event
  - RedisACLViolation does not subclass Exception (propagation guarantee)
"""

from __future__ import annotations
import inspect
import json
import logging
from unittest.mock import patch, AsyncMock

import pytest

from governance.redis_acl import (
    acl, RedisACLClient, RedisACLViolation,
    CALLER_PREFIX_RULES, EVENT_SCHEMA_VERSION,
    _RECONCILIATION_RELEASE_SCRIPT,
)


# ── Type contract: BaseException, not Exception ──────────────────────────

def test_violation_inherits_base_exception_not_exception():
    """Council mandate: same propagation contract as LayerZeroViolation."""
    assert issubclass(RedisACLViolation, BaseException)
    assert not issubclass(RedisACLViolation, Exception)


def test_violation_bypasses_except_exception():
    caught_by_exception = False
    try:
        try:
            raise RedisACLViolation(
                caller="t", attempted_op="set",
                attempted_key="l0:test", allowed_prefixes=["l2:"],
            )
        except Exception:
            caught_by_exception = True
    except RedisACLViolation:
        pass
    assert not caught_by_exception, \
        "RedisACLViolation was caught by 'except Exception' — propagation broken"


def test_violation_str_single_line():
    v = RedisACLViolation(
        caller="x", attempted_op="set",
        attempted_key="l0:a\nb", allowed_prefixes=["l2:"],
    )
    assert "\n" not in str(v)
    assert "[ACL]" in str(v)


# ── Bootstrap fail: undeclared module raises at acquisition ──────────────

def test_undeclared_module_bootstrap_fails():
    """Modules not in CALLER_PREFIX_RULES cannot obtain a client."""
    with pytest.raises(RedisACLViolation) as exc:
        acl.for_module("some.unknown.module")
    assert exc.value.attempted_op == "bootstrap"


def test_declared_module_bootstrap_succeeds():
    client = acl.for_module("governance.l0_supervisor")
    assert isinstance(client, RedisACLClient)
    assert "l0:" in client.allowed_prefixes


# ── Cross-layer write blocked ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_l2_module_cannot_write_l0_key():
    """L2 caller (signal_generator) cannot write to l0:* — Council acceptance."""
    sg_client = acl.for_module("engine.signal_generator")
    with pytest.raises(RedisACLViolation) as exc:
        await sg_client.set("l0:bot_paused", "1")
    assert exc.value.caller == "engine.signal_generator"
    assert exc.value.attempted_op == "set"
    assert exc.value.attempted_key == "l0:bot_paused"


@pytest.mark.asyncio
async def test_l1_module_cannot_write_l0_key():
    """Reconciliation (l1) cannot write l0:*."""
    recon_client = acl.for_module("governance.reconciliation")
    with pytest.raises(RedisACLViolation):
        await recon_client.set("l0:circuit_breaker_tripped", "1")


@pytest.mark.asyncio
async def test_l0_supervisor_can_write_l0_key():
    """Supervisor IS authorized for l0:*."""
    with patch("governance.redis_acl._raw_redis") as raw:
        raw.set = AsyncMock(return_value=True)
        sup_client = acl.for_module("governance.l0_supervisor")
        result = await sup_client.set("l0:bot_paused", "1")
        assert result is True
        raw.set.assert_called_once_with("l0:bot_paused", "1")


# ── Read violations same severity as writes ──────────────────────────────

@pytest.mark.asyncio
async def test_l2_module_cannot_read_l0_key():
    """Council mandate: read violations same severity as writes."""
    sg_client = acl.for_module("engine.signal_generator")
    with pytest.raises(RedisACLViolation) as exc:
        await sg_client.get("l0:supervisor_unhealthy")
    assert exc.value.attempted_op == "get"


@pytest.mark.asyncio
async def test_l2_module_cannot_delete_l0_key():
    sg_client = acl.for_module("engine.signal_generator")
    with pytest.raises(RedisACLViolation) as exc:
        await sg_client.delete("l0:bot_paused")
    assert exc.value.attempted_op == "delete"


# ── Self-declared layer cannot bypass ────────────────────────────────────

def test_set_method_has_no_layer_parameter():
    """Council acceptance: caller cannot pass `layer="l0"` to bypass ACL."""
    sig = inspect.signature(RedisACLClient.set)
    assert "layer" not in sig.parameters, \
        "set() must not accept a layer= parameter — authority is identity-based"


def test_get_method_has_no_layer_parameter():
    sig = inspect.signature(RedisACLClient.get)
    assert "layer" not in sig.parameters


# ── Structured event emission ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_violation_emits_structured_event(caplog):
    """Council mandate: every denied op emits a structured CRITICAL event."""
    caplog.set_level(logging.CRITICAL, logger="redis_acl")
    sg_client = acl.for_module("engine.signal_generator")
    with pytest.raises(RedisACLViolation):
        await sg_client.set("l0:bot_paused", "1")

    sec_records = [r for r in caplog.records
                   if r.levelno == logging.CRITICAL
                   and "redis_acl_violation" in r.getMessage()]
    assert len(sec_records) >= 1, "ACL violation did not emit structured event"

    payload = json.loads(sec_records[-1].getMessage().split("SECURITY ", 1)[1])
    assert payload["schema_version"] == EVENT_SCHEMA_VERSION
    assert payload["caller"] == "engine.signal_generator"
    assert payload["attempted_op"] == "set"
    assert payload["attempted_key"] == "l0:bot_paused"
    assert "l2:" in payload["allowed_prefixes"]


# ── Schema version present and monotonic ─────────────────────────────────

def test_event_schema_version_is_int_one():
    assert EVENT_SCHEMA_VERSION == 1
    assert isinstance(EVENT_SCHEMA_VERSION, int)


# ── Eval is private; only release_lock public ────────────────────────────

def test_no_public_eval_method():
    """Constraint 16: eval is private. Only release_lock is public."""
    public = [m for m in dir(RedisACLClient)
              if not m.startswith("_") and callable(getattr(RedisACLClient, m, None))]
    # Acceptable public methods only
    assert "eval" not in public
    assert "release_lock" in public


def test_release_script_is_module_constant():
    """Constraint 16: Lua script must be a module-level constant, not
    constructible from caller input."""
    assert "redis.call" in _RECONCILIATION_RELEASE_SCRIPT
    assert "ARGV[1]" in _RECONCILIATION_RELEASE_SCRIPT


@pytest.mark.asyncio
async def test_release_lock_calls_eval_internally():
    with patch("governance.redis_acl._raw_redis") as raw:
        raw.eval = AsyncMock(return_value=1)
        recon_client = acl.for_module("governance.reconciliation")
        result = await recon_client.release_lock("l1:reconciliation_active", "tok123")
        assert result is True
        raw.eval.assert_called_once()
        # Verify the Lua script passed was the module-private one
        args = raw.eval.call_args.args
        assert "redis.call('get', KEYS[1])" in args[0]


# ── Surface minimization ─────────────────────────────────────────────────

def test_acl_client_exposes_only_narrow_surface():
    """Constraint 15: surface is set/get/delete/incr/expire/release_lock only."""
    expected_public = {"set", "get", "delete", "incr", "expire",
                       "release_lock", "caller", "allowed_prefixes"}
    actual_public = {m for m in dir(RedisACLClient) if not m.startswith("_")}
    extras = actual_public - expected_public
    assert not extras, f"Unexpected public surface: {extras}"


# ── No __getattr__ forwarding ────────────────────────────────────────────

def test_no_getattr_forwarding():
    """Constraint 15: __getattr__ forwarding to raw client is forbidden."""
    sg_client = acl.for_module("engine.signal_generator")
    # Trying to access an undefined method must raise AttributeError,
    # NOT silently forward to the raw redis client.
    with pytest.raises(AttributeError):
        _ = sg_client.hget   # not in our narrow surface
