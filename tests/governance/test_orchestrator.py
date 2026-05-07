"""
Phase-2 tests for governance/orchestrator (observe-only).

Council review questions:
  Q3: Can every trade decision be reconstructed after the fact?
       — verified by audit-row-on-every-invocation tests
  Constraint 6: layer_inputs JSON includes 6 required keys
  Constraint 12: decision_time captured ONCE
  Constraint 18: decision row written for EVERY pipeline invocation
  Counterfactual veto persistence even during observe-only passthrough
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock, MagicMock
import pytest

from governance import orchestrator as orch_module
from governance.orchestrator import (
    Orchestrator, OrchestratorDecision,
    LAYER_INPUTS_SCHEMA_VERSION, ORCHESTRATOR_VERSION,
)
from governance.reconciliation import ReconciliationStatus, RECON_TO_GOVERNANCE


# ── Versioning ───────────────────────────────────────────────────────────

def test_schema_version_is_int_monotonic_from_one():
    """Constraint 13: schema versions are monotonically increasing integers.

    This test was originally pinned to == 1 in Phase 2 when v1 shipped.
    Phase 3 Step 1 bumped to 2 (additive: consecutive_losses key added).
    The forward-compatible assertion is: integer, ≥ 1.
    A complementary test in test_consecutive_losses.py verifies the
    HISTORY block preserves the v1 entry (constraint 13 'never repurposed').
    """
    assert isinstance(LAYER_INPUTS_SCHEMA_VERSION, int)
    assert LAYER_INPUTS_SCHEMA_VERSION >= 1


# ── Counterfactual computation ──────────────────────────────────────────

def test_counterfactual_clean_recon_no_vetoes():
    o = Orchestrator()
    layer_inputs = {
        "governance_mode": "normal",
        "reconciliation_raw": "clean",
        "reconciliation_implication": "normal",
    }
    cf = o._compute_counterfactual("buy", 10.0, layer_inputs)
    assert cf["action"] == "buy"
    assert cf["size_usd"] == 10.0
    assert cf["size_scale"] == 1.0
    assert cf["mode"] == "normal"
    assert cf["vetoes"] == []


def test_counterfactual_unknown_recon_freezes():
    """UNKNOWN recon → frozen mode → action=hold, vetoes=[recon_unknown]."""
    o = Orchestrator()
    layer_inputs = {
        "governance_mode": "normal",
        "reconciliation_raw": "unknown",
        "reconciliation_implication": "frozen",
    }
    cf = o._compute_counterfactual("buy", 10.0, layer_inputs)
    assert cf["action"] == "hold"
    assert cf["mode"] == "frozen"
    assert cf["size_usd"] == 0.0
    assert "recon_unknown" in cf["vetoes"]


def test_counterfactual_divergent_recon_freezes():
    o = Orchestrator()
    layer_inputs = {
        "governance_mode": "normal",
        "reconciliation_raw": "divergent",
        "reconciliation_implication": "frozen",
    }
    cf = o._compute_counterfactual("buy", 10.0, layer_inputs)
    assert cf["action"] == "hold"
    assert cf["mode"] == "frozen"


def test_counterfactual_stale_recon_restricts():
    """STALE recon → restricted mode → size scaled to 0.5."""
    o = Orchestrator()
    layer_inputs = {
        "governance_mode": "normal",
        "reconciliation_raw": "stale",
        "reconciliation_implication": "restricted",
    }
    cf = o._compute_counterfactual("buy", 10.0, layer_inputs)
    assert cf["action"] == "buy"
    assert cf["mode"] == "restricted"
    assert cf["size_scale"] == 0.5
    assert cf["size_usd"] == 5.0


# ── Constraint 12: single clock read per evaluate ───────────────────────

@pytest.mark.asyncio
async def test_decision_time_captured_once_per_evaluate():
    """Verify the orchestrator does not re-read the wall clock during one
    evaluation (Council mandate: avoid millisecond drift in audit rows)."""
    call_count = {"n": 0}
    real_now = datetime.now

    def counting_now(tz=None):
        call_count["n"] += 1
        return real_now(tz)

    DT = type("DT", (), {"now": staticmethod(counting_now)})

    with patch.object(orch_module, "datetime", DT), \
         patch.object(orch_module.orchestrator, "_persist_decision",
                      new=AsyncMock()), \
         patch("governance.orchestrator.recon_last_status",
               new=AsyncMock(return_value=ReconciliationStatus.CLEAN)), \
         patch.object(orch_module.orchestrator, "_snapshot_inputs",
                      new=AsyncMock(return_value={
                          "schema_version": 1,
                          "kernel_hash": "x", "kernel_version": "1.0.0",
                          "orchestrator_version": ORCHESTRATOR_VERSION,
                          "governance_mode": "normal",
                          "reconciliation_raw": "clean",
                          "reconciliation_implication": "normal",
                      })):

        await orch_module.orchestrator.evaluate(
            pair="BTC/USDT", requested_action="buy",
            pipeline_state={"stage_reached": "rule"},
            proposed_size_usd=10.0,
        )

    assert call_count["n"] == 1, (
        f"Orchestrator called wall clock {call_count['n']} times; "
        f"contract requires exactly 1"
    )


# ── Constraint 6: layer_inputs has all required keys ────────────────────

@pytest.mark.asyncio
async def test_layer_inputs_includes_all_required_keys():
    """Council mandate: 6 keys minimum for replay durability."""
    required = {
        "schema_version", "kernel_hash", "orchestrator_version",
        "governance_mode", "reconciliation_raw", "reconciliation_implication",
    }
    o = Orchestrator()
    with patch.object(orch_module, "redis") as fake_redis, \
         patch("governance.orchestrator.recon_last_status",
               new=AsyncMock(return_value=ReconciliationStatus.CLEAN)):
        fake_redis.get = AsyncMock(return_value=None)
        snapshot = await o._snapshot_inputs(
            pair="BTC/USDT",
            pipeline_state={"stage_reached": "rule"},
            proposed_size_usd=10.0,
            proposed_sl=99.0,
            proposed_tp=110.0,
        )
    missing = required - set(snapshot.keys())
    assert not missing, f"layer_inputs missing required keys: {missing}"


@pytest.mark.asyncio
async def test_layer_inputs_kernel_hash_is_real():
    """Kernel hash in snapshot must equal safety_kernel.KERNEL_HASH (truncated)."""
    from governance import safety_kernel as L0
    o = Orchestrator()
    with patch.object(orch_module, "redis") as fake_redis, \
         patch("governance.orchestrator.recon_last_status",
               new=AsyncMock(return_value=ReconciliationStatus.CLEAN)):
        fake_redis.get = AsyncMock(return_value=None)
        snapshot = await o._snapshot_inputs(
            pair="BTC/USDT", pipeline_state={"stage_reached": "rule"},
            proposed_size_usd=0, proposed_sl=None, proposed_tp=None,
        )
    assert snapshot["kernel_hash"] == L0.KERNEL_HASH[:16]
    assert snapshot["kernel_version"] == L0.KERNEL_VERSION


# ── Observe-only passthrough preserves counterfactual ───────────────────

@pytest.mark.asyncio
async def test_observe_only_passthrough_records_counterfactual_veto():
    """Council mandate: even when observe_only=true and final_action=
    requested_action, the counterfactual vetoes must persist on the row."""
    o = Orchestrator()
    o.observe_only = True

    saved_rows = []

    async def capture(row):
        saved_rows.append(row)

    with patch.object(o, "_persist_decision", new=capture), \
         patch.object(orch_module, "redis") as fake_redis, \
         patch("governance.orchestrator.recon_last_status",
               new=AsyncMock(return_value=ReconciliationStatus.UNKNOWN)):
        fake_redis.get = AsyncMock(return_value=None)
        decision = await o.evaluate(
            pair="BTC/USDT", requested_action="buy",
            pipeline_state={"stage_reached": "rule"},
            proposed_size_usd=10.0,
        )

    # In observe-only, final == requested even though counterfactual would veto
    assert decision.final_action == "buy"
    assert decision.observe_only_passthrough is True
    # But counterfactual records what enforcement WOULD have done
    assert decision.counterfactual_action == "hold"
    assert "recon_unknown" in decision.layer_vetoes
    assert decision.counterfactual_mode == "frozen"


# ── OrchestratorDecision row shape ──────────────────────────────────────

def test_decision_to_db_includes_required_fields():
    decision = OrchestratorDecision(
        decision_id="test-id",
        decision_time=datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
        pair="BTC/USDT",
        requested_action="buy",
        final_action="buy",
        size_usd=10.0,
        size_scale=1.0,
        observe_only_passthrough=True,
        counterfactual_action="hold",
        counterfactual_size_usd=0.0,
        counterfactual_size_scale=0.0,
        counterfactual_mode="frozen",
        layer_vetoes=["recon_unknown"],
        layer_inputs={"schema_version": 1},
        governance_mode="normal",
        explanation="test",
    )
    db_row = decision.to_db()
    assert db_row["pair"] == "BTC/USDT"
    assert db_row["counterfactual_action"] == "hold"
    assert db_row["layer_vetoes"] == ["recon_unknown"]
    assert db_row["observe_only_passthrough"] is True
    # decision_time serialized as ISO 8601
    assert "2026-05-07T12:00:00" in db_row["decision_time"]
