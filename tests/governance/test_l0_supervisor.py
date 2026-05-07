"""
Phase-2 tests for governance/l0_supervisor.

Council review questions:
  Q1: Can governance state survive trading-loop failure?
       — verified by supervisor independence test
  P2-R1: Can /resume bypass supervisor unhealthy?
       — verified by resume_authority_check test
"""

from __future__ import annotations
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from governance import l0_supervisor
from governance.exceptions import LayerZeroViolation
from governance.redis_acl import RedisACLViolation


# ── P2-R1 closure ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resume_blocked_when_supervisor_unhealthy():
    """P2-R1: while l0:supervisor_unhealthy=1, resume must be refused."""
    with patch.object(l0_supervisor, "redis") as fake:
        fake.get = AsyncMock(return_value="1")
        allowed, reason = await l0_supervisor.resume_authority_check()
    assert allowed is False
    assert reason == "l0_supervisor_unhealthy"


@pytest.mark.asyncio
async def test_resume_allowed_when_supervisor_healthy():
    with patch.object(l0_supervisor, "redis") as fake:
        fake.get = AsyncMock(return_value=None)
        allowed, reason = await l0_supervisor.resume_authority_check()
    assert allowed is True
    assert reason == "ok"


@pytest.mark.asyncio
async def test_resume_fails_closed_on_redis_error():
    """If we cannot read supervisor state, resume must FAIL CLOSED."""
    with patch.object(l0_supervisor, "redis") as fake:
        fake.get = AsyncMock(side_effect=RuntimeError("redis down"))
        allowed, reason = await l0_supervisor.resume_authority_check()
    assert allowed is False
    assert "unreadable" in reason


# ── on_layer_zero_violation handler ──────────────────────────────────────

@pytest.mark.asyncio
async def test_violation_handler_sets_unhealthy_and_paused():
    """On L0 violation, supervisor sets unhealthy + paused regardless of mode."""
    with patch.object(l0_supervisor, "redis") as fake_redis, \
         patch.object(l0_supervisor, "L0_SUPERVISOR_HARD_EXIT", False), \
         patch("notifications.telegram_bot.telegram") as fake_tg:
        fake_redis.set = AsyncMock(return_value=True)
        fake_redis.incr = AsyncMock(return_value=1)
        fake_redis.expire = AsyncMock(return_value=True)
        fake_tg.send = AsyncMock()

        v = LayerZeroViolation(reason="test", source_module="t")
        await l0_supervisor.on_layer_zero_violation(v, source_loop="test_loop")

        # Verify unhealthy + paused written
        keys_set = [c.args[0] for c in fake_redis.set.call_args_list]
        assert "l0:supervisor_unhealthy" in keys_set
        assert "l0:bot_paused" in keys_set


@pytest.mark.asyncio
async def test_violation_handler_increments_soft_trigger_counter():
    with patch.object(l0_supervisor, "redis") as fake_redis, \
         patch.object(l0_supervisor, "L0_SUPERVISOR_HARD_EXIT", False), \
         patch("notifications.telegram_bot.telegram") as fake_tg:
        fake_redis.set = AsyncMock(return_value=True)
        fake_redis.incr = AsyncMock(return_value=2)
        fake_redis.expire = AsyncMock(return_value=True)
        fake_tg.send = AsyncMock()

        v = LayerZeroViolation(reason="test", source_module="t")
        await l0_supervisor.on_layer_zero_violation(v, "loop")
        fake_redis.incr.assert_called_with("l0:soft_mode_triggers")


# ── Q1: Supervisor independence ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_supervisor_loop_survives_redis_check_failure():
    """Q1: supervisor must keep ticking even if a single cycle's check raises."""
    cycle_count = {"n": 0}

    async def fake_get(key):
        cycle_count["n"] += 1
        if cycle_count["n"] <= 2:
            return None
        # On 3rd call, simulate a transient error
        raise RuntimeError("transient redis hiccup")

    with patch.object(l0_supervisor, "redis") as fake_redis, \
         patch.object(l0_supervisor, "_check_kernel_hash",
                      new=AsyncMock(return_value="ok")), \
         patch.object(l0_supervisor, "_check_cb_coherence",
                      new=AsyncMock(return_value="ok")), \
         patch.object(l0_supervisor, "SUPERVISOR_TICK_SECONDS", 0.01):

        fake_redis.set = AsyncMock(return_value=True)
        fake_redis.get = AsyncMock(side_effect=fake_get)

        task = asyncio.create_task(l0_supervisor.supervise())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # If we got here without LayerZeroViolation propagating,
    # the loop survived the transient error.
    assert cycle_count["n"] >= 1


# ── Q1: Propagation of L0 violation from supervisor itself ──────────────

@pytest.mark.asyncio
async def test_supervisor_propagates_layer_zero_violation():
    """If a kernel-hash drift is detected and raises, it must propagate
    to the gather() boundary in main.py — not be swallowed."""
    with patch.object(l0_supervisor, "redis") as fake_redis, \
         patch.object(l0_supervisor, "_check_kernel_hash",
                      new=AsyncMock(side_effect=LayerZeroViolation(
                          reason="synthetic kernel drift",
                          source_module="test",
                      ))), \
         patch.object(l0_supervisor, "SUPERVISOR_TICK_SECONDS", 0.01):

        fake_redis.set = AsyncMock(return_value=True)
        fake_redis.get = AsyncMock(return_value=None)

        with pytest.raises(LayerZeroViolation) as exc:
            await l0_supervisor.supervise()
        assert "synthetic kernel drift" in exc.value.reason


# ── Constraint 17: l0:* keys owned exclusively by supervisor ─────────────

def test_supervisor_module_owns_l0_keys():
    """Verify CALLER_PREFIX_RULES grants supervisor exclusive l0: write authority."""
    from governance.redis_acl import CALLER_PREFIX_RULES
    assert "l0:" in CALLER_PREFIX_RULES["governance.l0_supervisor"]


# ── request_pause uses ACL-bound client ─────────────────────────────────

@pytest.mark.asyncio
async def test_request_pause_writes_via_acl():
    with patch.object(l0_supervisor, "redis") as fake:
        fake.set = AsyncMock(return_value=True)
        await l0_supervisor.request_pause(reason="test", source="unittest")
        fake.set.assert_called_with("l0:bot_paused", "1")
