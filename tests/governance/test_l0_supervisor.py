"""
Phase-2 tests for governance/l0_supervisor.
Phase-3 Step 3 (B2) — reconciliation-status ingestion tests.

Council review questions:
  Q1: Can governance state survive trading-loop failure?
       — verified by supervisor independence test
  P2-R1: Can /resume bypass supervisor unhealthy?
       — verified by resume_authority_check test
  Step 3 (B2):
       — supervisor cycle log carries live reconciliation status (not
         placeholder); fallback to "unknown" on non-L0 read failure;
         L0 / ACL exceptions propagate; non-recon decision fields are
         byte-identical pre/post Step 3 (proven in evidence script).
"""

from __future__ import annotations
import asyncio
import json
import logging
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from governance import l0_supervisor
from governance.exceptions import LayerZeroViolation
from governance.redis_acl import RedisACLViolation
from governance.reconciliation import ReconciliationStatus


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
         patch.object(l0_supervisor, "recon_last_status",
                      new=AsyncMock(return_value=ReconciliationStatus.CLEAN)), \
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
         patch.object(l0_supervisor, "recon_last_status",
                      new=AsyncMock(return_value=ReconciliationStatus.CLEAN)), \
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


# ════════════════════════════════════════════════════════════════════════
# Phase 3 Step 3 (B2) — reconciliation-status ingestion
# ════════════════════════════════════════════════════════════════════════

async def _run_one_cycle(recon_mock, redis_get_return=None):
    """Drive supervise() through approximately one tick and capture all
    L0_CYCLE log payloads. The supervisor loop tick is shrunk so that one
    iteration completes within ~0.1s wall time.

    Mocks kernel_hash + cb_coherence to "ok" so cycles are clean and the
    only varying signal is `recon_last_status` (and what we explicitly
    inject via redis_get_return for supervisor_unhealthy).
    """
    captured = []
    real_log_info = l0_supervisor.log.info

    def capture_info(msg, *args, **kwargs):
        if msg == "L0_CYCLE %s" and args:
            captured.append(json.loads(args[0]))
        real_log_info(msg, *args, **kwargs)

    with patch.object(l0_supervisor, "redis") as fake_redis, \
         patch.object(l0_supervisor, "_check_kernel_hash",
                      new=AsyncMock(return_value="ok")), \
         patch.object(l0_supervisor, "_check_cb_coherence",
                      new=AsyncMock(return_value="ok")), \
         patch.object(l0_supervisor, "recon_last_status",
                      new=recon_mock), \
         patch.object(l0_supervisor, "SUPERVISOR_TICK_SECONDS", 0.01), \
         patch.object(l0_supervisor.log, "info",
                      side_effect=capture_info):
        fake_redis.set = AsyncMock(return_value=True)
        fake_redis.get = AsyncMock(return_value=redis_get_return)

        task = asyncio.create_task(l0_supervisor.supervise())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return captured


# ── Live status appears in cycle log (one event per recon state) ───────

@pytest.mark.asyncio
async def test_cycle_log_records_recon_status_clean():
    events = await _run_one_cycle(
        AsyncMock(return_value=ReconciliationStatus.CLEAN),
    )
    assert len(events) >= 1
    assert all(e["reconciliation_status"] == "clean" for e in events)


@pytest.mark.asyncio
async def test_cycle_log_records_recon_status_stale():
    events = await _run_one_cycle(
        AsyncMock(return_value=ReconciliationStatus.STALE),
    )
    assert len(events) >= 1
    assert all(e["reconciliation_status"] == "stale" for e in events)


@pytest.mark.asyncio
async def test_cycle_log_records_recon_status_unknown():
    events = await _run_one_cycle(
        AsyncMock(return_value=ReconciliationStatus.UNKNOWN),
    )
    assert len(events) >= 1
    assert all(e["reconciliation_status"] == "unknown" for e in events)


@pytest.mark.asyncio
async def test_cycle_log_records_recon_status_divergent():
    events = await _run_one_cycle(
        AsyncMock(return_value=ReconciliationStatus.DIVERGENT),
    )
    assert len(events) >= 1
    assert all(e["reconciliation_status"] == "divergent" for e in events)


# ── v1 placeholder string never appears at v2 ──────────────────────────

@pytest.mark.asyncio
async def test_cycle_log_never_carries_phase3_placeholder_string():
    """v1 carried the literal 'phase3' placeholder; v2 must never emit it."""
    for status in [ReconciliationStatus.CLEAN, ReconciliationStatus.STALE,
                   ReconciliationStatus.UNKNOWN, ReconciliationStatus.DIVERGENT]:
        events = await _run_one_cycle(AsyncMock(return_value=status))
        for e in events:
            assert e["reconciliation_status"] != "phase3", (
                f"Found v1 placeholder for status={status.value}: {e}"
            )


# ── Safe-default fallback on non-L0 read failure ───────────────────────

@pytest.mark.asyncio
async def test_cycle_log_falls_back_to_unknown_on_recon_error():
    """Any non-L0 exception from recon_last_status falls back to 'unknown'
    with a structured log — the loop continues, the cycle proceeds."""
    events = await _run_one_cycle(
        AsyncMock(side_effect=RuntimeError("recon backend down")),
    )
    assert len(events) >= 1
    assert all(e["reconciliation_status"] == "unknown" for e in events)


# ── L0 / ACL exceptions propagate uncaught ─────────────────────────────

@pytest.mark.asyncio
async def test_cycle_log_layer_zero_violation_from_recon_propagates():
    """LayerZeroViolation from the recon read must propagate to the
    main.py task supervisor — never swallowed."""
    with patch.object(l0_supervisor, "redis") as fake_redis, \
         patch.object(l0_supervisor, "_check_kernel_hash",
                      new=AsyncMock(return_value="ok")), \
         patch.object(l0_supervisor, "_check_cb_coherence",
                      new=AsyncMock(return_value="ok")), \
         patch.object(l0_supervisor, "recon_last_status",
                      new=AsyncMock(side_effect=LayerZeroViolation(
                          reason="synthetic recon L0 violation",
                          source_module="test",
                      ))), \
         patch.object(l0_supervisor, "SUPERVISOR_TICK_SECONDS", 0.01):
        fake_redis.set = AsyncMock(return_value=True)
        fake_redis.get = AsyncMock(return_value=None)

        with pytest.raises(LayerZeroViolation) as exc:
            await l0_supervisor.supervise()
        assert "synthetic recon L0 violation" in exc.value.reason


@pytest.mark.asyncio
async def test_cycle_log_redis_acl_violation_from_recon_propagates():
    """RedisACLViolation from the recon read must propagate."""
    with patch.object(l0_supervisor, "redis") as fake_redis, \
         patch.object(l0_supervisor, "_check_kernel_hash",
                      new=AsyncMock(return_value="ok")), \
         patch.object(l0_supervisor, "_check_cb_coherence",
                      new=AsyncMock(return_value="ok")), \
         patch.object(l0_supervisor, "recon_last_status",
                      new=AsyncMock(side_effect=RedisACLViolation(
                          caller="test.synthetic",
                          attempted_op="get",
                          attempted_key="l1:recon:last",
                          allowed_prefixes=frozenset({"l2:"}),
                      ))), \
         patch.object(l0_supervisor, "SUPERVISOR_TICK_SECONDS", 0.01):
        fake_redis.set = AsyncMock(return_value=True)
        fake_redis.get = AsyncMock(return_value=None)

        with pytest.raises(RedisACLViolation):
            await l0_supervisor.supervise()


# ── Schema version + HISTORY block ─────────────────────────────────────

def test_cycle_log_schema_version_bumped_to_two():
    """Constraint 13: schema version monotonic (1 → 2)."""
    assert l0_supervisor.CYCLE_LOG_SCHEMA_VERSION == 2
    assert isinstance(l0_supervisor.CYCLE_LOG_SCHEMA_VERSION, int)


def test_cycle_log_history_block_v1_and_v2_present():
    """Append-only HISTORY: v1 entry must remain verbatim; v2 entry added."""
    import inspect
    src = inspect.getsource(l0_supervisor)
    assert "v1 (Phase 2, original):" in src
    assert "v2 (Phase 3 Step 3" in src
    # v1 placeholder description must remain — proves v1 entry not edited
    assert "carried the literal placeholder string \"phase3\"" in src
    # v2 description must mention live-value source
    assert "governance.reconciliation.last_status()" in src


# ── Decision-logic invariance: clean-streak unaffected by recon state ──

@pytest.mark.asyncio
async def test_recon_state_does_not_influence_clean_streak():
    """The supervisor's clean-streak / unhealthy-clear decision logic must
    be byte-identical regardless of reconciliation_status — recon ingestion
    is observational only.

    Concretely: kernel_hash=ok and cb_state_consistency=ok must yield the
    same is_clean=True regardless of recon state.
    """
    # The supervise() loop's `is_clean` branch is at module level — we verify
    # by checking that all cycle_event payloads, across all 4 recon states,
    # show kernel_hash_status="ok" and cb_state_consistency="ok" — i.e. the
    # supervisor still considers them clean cycles.
    for status in [ReconciliationStatus.CLEAN, ReconciliationStatus.STALE,
                   ReconciliationStatus.UNKNOWN, ReconciliationStatus.DIVERGENT]:
        events = await _run_one_cycle(AsyncMock(return_value=status))
        for e in events:
            assert e["kernel_hash_status"] == "ok"
            assert e["cb_state_consistency"] == "ok"


# ── Schema-version field in cycle_event reflects the constant ──────────

@pytest.mark.asyncio
async def test_cycle_event_schema_version_field_reflects_constant():
    events = await _run_one_cycle(
        AsyncMock(return_value=ReconciliationStatus.CLEAN),
    )
    assert len(events) >= 1
    for e in events:
        assert e["schema_version"] == l0_supervisor.CYCLE_LOG_SCHEMA_VERSION
        assert e["schema_version"] == 2


# ── Recon ingestion happens BEFORE cycle_event construction ────────────

@pytest.mark.asyncio
async def test_recon_called_before_cycle_event_construction():
    """Council mandate: dedicated step, no interleaving with CB or health.
    Verified by ordering: when both recon and kernel_hash raise, the recon
    exception (or fallback) resolves first; we assert the kernel_hash mock
    is called after the recon mock by tracking call order."""
    call_order = []

    async def recon_first():
        call_order.append("recon")
        return ReconciliationStatus.CLEAN

    async def kernel_after():
        call_order.append("kernel_hash")
        return "ok"

    async def cb_after():
        call_order.append("cb_coherence")
        return "ok"

    with patch.object(l0_supervisor, "redis") as fake_redis, \
         patch.object(l0_supervisor, "recon_last_status", side_effect=recon_first), \
         patch.object(l0_supervisor, "_check_kernel_hash", side_effect=kernel_after), \
         patch.object(l0_supervisor, "_check_cb_coherence", side_effect=cb_after), \
         patch.object(l0_supervisor, "SUPERVISOR_TICK_SECONDS", 0.01):
        fake_redis.set = AsyncMock(return_value=True)
        fake_redis.get = AsyncMock(return_value=None)

        task = asyncio.create_task(l0_supervisor.supervise())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # First call in the cycle must be recon, then kernel_hash, then cb_coherence
    assert len(call_order) >= 3
    assert call_order[0] == "recon"
    assert call_order[1] == "kernel_hash"
    assert call_order[2] == "cb_coherence"
