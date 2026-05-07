"""
Phase 3 Step 1 — Consecutive losses tracker tests.

Council requirements:
  - tracker module functions correctly under various trade-history shapes
  - orchestrator integration: streak >= threshold adds counterfactual veto
  - layer_inputs schema v2 includes consecutive_losses key
  - LAYER_INPUTS_SCHEMA_VERSION_HISTORY preserved (v1 entry untouched)
  - R3 contract preserved: hash differs when veto fires; identical inputs
    still produce identical hash; canonical encoding intact
  - Constraint 18 preserved: decision row written for every invocation
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock, MagicMock
import pytest

from governance import consecutive_losses as cl
from governance import orchestrator as orch_module
from governance.orchestrator import (
    Orchestrator, LAYER_INPUTS_SCHEMA_VERSION, ORCHESTRATOR_VERSION,
    compute_counterfactual_hash,
)
from governance.reconciliation import ReconciliationStatus


def _trade(pair: str, pnl: float, closed_seconds_ago: int) -> dict:
    return {
        "pair":      pair,
        "status":    "closed",
        "pnl_usd":   pnl,
        "closed_at": (datetime.now(timezone.utc)
                      - timedelta(seconds=closed_seconds_ago)).isoformat(),
    }


# ── Tracker logic ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tracker_returns_zero_when_no_trades():
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=[])):
        n = await cl.get_consecutive_loss_count("BTC/USDT")
    assert n == 0


@pytest.mark.asyncio
async def test_tracker_counts_simple_loss_streak():
    trades = [
        _trade("BTC/USDT", -1.5, 100),   # most recent loss
        _trade("BTC/USDT", -0.8, 200),
        _trade("BTC/USDT", -0.2, 300),
        _trade("BTC/USDT",  2.0, 400),   # win — ends streak going backward
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)):
        n = await cl.get_consecutive_loss_count("BTC/USDT")
    assert n == 3


@pytest.mark.asyncio
async def test_tracker_streak_ends_at_first_win():
    trades = [
        _trade("BTC/USDT", -1.0, 100),
        _trade("BTC/USDT",  0.5, 200),   # win
        _trade("BTC/USDT", -1.0, 300),
        _trade("BTC/USDT", -1.0, 400),
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)):
        n = await cl.get_consecutive_loss_count("BTC/USDT")
    # Most recent is loss, then a win — streak is 1
    assert n == 1


@pytest.mark.asyncio
async def test_tracker_break_even_ends_streak():
    """pnl_usd == 0 (break-even) is treated as not-a-loss; streak ends."""
    trades = [
        _trade("BTC/USDT", -1.0, 100),
        _trade("BTC/USDT",  0.0, 200),
        _trade("BTC/USDT", -1.0, 300),
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)):
        n = await cl.get_consecutive_loss_count("BTC/USDT")
    assert n == 1


@pytest.mark.asyncio
async def test_tracker_filters_by_pair():
    trades = [
        _trade("ETH/USDT", -1.0, 100),
        _trade("BTC/USDT", -1.0, 200),
        _trade("BTC/USDT", -1.0, 300),
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)):
        n_btc = await cl.get_consecutive_loss_count("BTC/USDT")
        n_eth = await cl.get_consecutive_loss_count("ETH/USDT")
    assert n_btc == 2
    assert n_eth == 1


@pytest.mark.asyncio
async def test_tracker_ignores_non_closed_trades():
    trades = [
        {"pair": "BTC/USDT", "status": "open",   "pnl_usd": None,
         "closed_at": None},
        _trade("BTC/USDT", -1.0, 200),
        _trade("BTC/USDT", -1.0, 300),
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)):
        n = await cl.get_consecutive_loss_count("BTC/USDT")
    assert n == 2


@pytest.mark.asyncio
async def test_tracker_db_failure_returns_zero_safely():
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(side_effect=RuntimeError("db down"))):
        n = await cl.get_consecutive_loss_count("BTC/USDT")
    assert n == 0   # safe default — no veto manufactured from missing data


@pytest.mark.asyncio
async def test_tracker_unparseable_pnl_ends_streak():
    trades = [
        _trade("BTC/USDT", -1.0, 100),
        {"pair": "BTC/USDT", "status": "closed", "pnl_usd": "not a number",
         "closed_at": (datetime.now(timezone.utc)
                       - timedelta(seconds=200)).isoformat()},
        _trade("BTC/USDT", -1.0, 300),
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)):
        n = await cl.get_consecutive_loss_count("BTC/USDT")
    # First trade is loss; second has unparseable pnl — streak ends
    assert n == 1


# ── Orchestrator integration ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_layer_inputs_schema_v2_includes_consecutive_losses():
    o = Orchestrator()
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)
    with patch.object(orch_module, "redis", fake_redis), \
         patch("governance.orchestrator.recon_last_status",
               new=AsyncMock(return_value=ReconciliationStatus.CLEAN)), \
         patch("governance.consecutive_losses.get_consecutive_loss_count",
               new=AsyncMock(return_value=0)):
        snapshot = await o._snapshot_inputs(
            pair="BTC/USDT", pipeline_state={"stage_reached": "rule"},
            proposed_size_usd=10.0, proposed_sl=99.0, proposed_tp=110.0,
        )
    assert snapshot["schema_version"] == 2
    assert "consecutive_losses" in snapshot
    assert snapshot["consecutive_losses"] == 0


@pytest.mark.asyncio
async def test_layer_inputs_required_keys_unchanged_in_v2():
    """v2 must be a strict superset of v1 — six required keys still present."""
    required = {
        "schema_version", "kernel_hash", "orchestrator_version",
        "governance_mode", "reconciliation_raw", "reconciliation_implication",
    }
    o = Orchestrator()
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)
    with patch.object(orch_module, "redis", fake_redis), \
         patch("governance.orchestrator.recon_last_status",
               new=AsyncMock(return_value=ReconciliationStatus.CLEAN)), \
         patch("governance.consecutive_losses.get_consecutive_loss_count",
               new=AsyncMock(return_value=0)):
        snapshot = await o._snapshot_inputs(
            pair="BTC/USDT", pipeline_state={"stage_reached": "rule"},
            proposed_size_usd=10.0, proposed_sl=99.0, proposed_tp=110.0,
        )
    missing = required - set(snapshot.keys())
    assert not missing, f"v2 dropped v1-required key(s): {missing}"


def test_counterfactual_below_threshold_no_veto():
    """consecutive_losses < THRESHOLD (3) → no veto, no mode change."""
    o = Orchestrator()
    layer_inputs = {
        "governance_mode": "normal",
        "reconciliation_raw": "clean",
        "reconciliation_implication": "normal",
        "pair": "BTC/USDT",
        "consecutive_losses": 2,
    }
    cf = o._compute_counterfactual("buy", 10.0, layer_inputs)
    assert cf["vetoes"] == []
    assert cf["mode"] == "normal"
    assert cf["size_scale"] == 1.0


def test_counterfactual_at_threshold_adds_veto():
    """consecutive_losses == 3 → veto fires, mode → restricted, size × 0.5."""
    o = Orchestrator()
    layer_inputs = {
        "governance_mode": "normal",
        "reconciliation_raw": "clean",
        "reconciliation_implication": "normal",
        "pair": "BTC/USDT",
        "consecutive_losses": 3,
    }
    cf = o._compute_counterfactual("buy", 10.0, layer_inputs)
    assert any(v.startswith("consecutive_losses_") for v in cf["vetoes"])
    assert "consecutive_losses_3_on_BTC/USDT" in cf["vetoes"]
    assert cf["mode"] == "restricted"
    assert cf["size_scale"] == 0.5
    assert cf["size_usd"] == 5.0


def test_counterfactual_above_threshold_records_actual_count():
    """The veto string includes the actual streak count (5, not just 3+)."""
    o = Orchestrator()
    layer_inputs = {
        "governance_mode": "normal",
        "reconciliation_raw": "clean",
        "reconciliation_implication": "normal",
        "pair": "ETH/USDT",
        "consecutive_losses": 5,
    }
    cf = o._compute_counterfactual("buy", 10.0, layer_inputs)
    assert "consecutive_losses_5_on_ETH/USDT" in cf["vetoes"]


def test_counterfactual_consecutive_losses_does_not_downgrade_frozen():
    """Mode escalation only: if recon already froze, losses-veto does not
    downgrade to restricted. Frozen stays frozen."""
    o = Orchestrator()
    layer_inputs = {
        "governance_mode": "normal",
        "reconciliation_raw": "unknown",
        "reconciliation_implication": "frozen",
        "pair": "BTC/USDT",
        "consecutive_losses": 3,
    }
    cf = o._compute_counterfactual("buy", 10.0, layer_inputs)
    # Both vetoes recorded; frozen wins
    assert any(v.startswith("recon_") for v in cf["vetoes"])
    assert any(v.startswith("consecutive_losses_") for v in cf["vetoes"])
    assert cf["mode"] == "frozen"
    assert cf["action"] == "hold"
    assert cf["size_usd"] == 0.0


def test_counterfactual_on_hold_request_does_not_add_losses_veto():
    """Hold requests are non-actions — losses veto does not apply."""
    o = Orchestrator()
    layer_inputs = {
        "governance_mode": "normal",
        "reconciliation_raw": "clean",
        "reconciliation_implication": "normal",
        "pair": "BTC/USDT",
        "consecutive_losses": 5,
    }
    cf = o._compute_counterfactual("hold", 0.0, layer_inputs)
    assert all(not v.startswith("consecutive_losses_") for v in cf["vetoes"])


def test_veto_order_stable_recon_then_losses():
    """Constraint: list ordering preserved. recon vetoes always come
    before losses vetoes — anchored by code execution order."""
    o = Orchestrator()
    layer_inputs = {
        "governance_mode": "normal",
        "reconciliation_raw": "stale",
        "reconciliation_implication": "restricted",
        "pair": "BTC/USDT",
        "consecutive_losses": 3,
    }
    cf = o._compute_counterfactual("buy", 10.0, layer_inputs)
    assert len(cf["vetoes"]) == 2
    assert cf["vetoes"][0].startswith("recon_")
    assert cf["vetoes"][1].startswith("consecutive_losses_")


# ── R3 hash contract preserved ──────────────────────────────────────────

def test_hash_contract_preserved_when_no_losses_veto():
    """With cl=0 and clean recon, counterfactual is unchanged from
    Phase-2 behavior; hash matches what Phase 2 would compute."""
    h = compute_counterfactual_hash("buy", 1.0, "normal", [])
    # Phase 2 hash for the same inputs (recorded in evidence): 0ea24c8d...
    assert h == "0ea24c8dabcac50e22a7ac7635bcf4ef454aaa3dee6da79a7f8236475091d7e7"


def test_hash_changes_when_losses_veto_fires():
    """Adding the consecutive_losses veto produces a different hash —
    R3 contract: 'if vetoes change, hash MUST change'."""
    h_no = compute_counterfactual_hash("buy", 1.0, "normal", [])
    h_yes = compute_counterfactual_hash(
        "buy", 0.5, "restricted", ["consecutive_losses_3_on_BTC/USDT"],
    )
    assert h_no != h_yes


def test_hash_stable_for_identical_losses_vetoes():
    """Same veto string → same hash, twice."""
    h1 = compute_counterfactual_hash(
        "buy", 0.5, "restricted", ["consecutive_losses_3_on_BTC/USDT"],
    )
    h2 = compute_counterfactual_hash(
        "buy", 0.5, "restricted", ["consecutive_losses_3_on_BTC/USDT"],
    )
    assert h1 == h2


# ── Constraint 18 preserved ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_decision_row_still_written_for_holds_with_losses():
    """Constraint 18: every pipeline invocation produces a decision row,
    including holds. Phase 3 Step 1 must not change this."""
    o = Orchestrator()
    captured = []

    async def capture(decision):
        captured.append(decision)

    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)
    with patch.object(o, "_persist_decision", new=capture), \
         patch.object(orch_module, "redis", fake_redis), \
         patch("governance.orchestrator.recon_last_status",
               new=AsyncMock(return_value=ReconciliationStatus.CLEAN)), \
         patch("governance.consecutive_losses.get_consecutive_loss_count",
               new=AsyncMock(return_value=5)):
        await o.evaluate(
            pair="BTC/USDT", requested_action="hold",
            pipeline_state={"stage_reached": "rule"},
            proposed_size_usd=0.0,
        )
    assert len(captured) == 1
    assert captured[0].final_action == "hold"


@pytest.mark.asyncio
async def test_orchestrator_evaluate_records_consecutive_losses_in_layer_inputs():
    o = Orchestrator()
    captured = []

    async def capture(decision):
        captured.append(decision)

    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)
    with patch.object(o, "_persist_decision", new=capture), \
         patch.object(orch_module, "redis", fake_redis), \
         patch("governance.orchestrator.recon_last_status",
               new=AsyncMock(return_value=ReconciliationStatus.CLEAN)), \
         patch("governance.consecutive_losses.get_consecutive_loss_count",
               new=AsyncMock(return_value=4)):
        await o.evaluate(
            pair="BTC/USDT", requested_action="buy",
            pipeline_state={"stage_reached": "rule"},
            proposed_size_usd=10.0,
        )
    decision = captured[0]
    assert decision.layer_inputs["consecutive_losses"] == 4
    assert decision.layer_inputs["schema_version"] == 2
    # In observe-only, final_action passes through; counterfactual records veto
    assert decision.final_action == "buy"
    assert decision.counterfactual_action == "buy"
    assert decision.counterfactual_mode == "restricted"
    assert any(v.startswith("consecutive_losses_4_on_BTC/USDT")
               for v in decision.layer_vetoes)


# ── HISTORY block preserved (v1 unchanged) ─────────────────────────────

def test_orchestrator_version_bumped():
    """ORCHESTRATOR_VERSION must change when schema bumps."""
    assert ORCHESTRATOR_VERSION != "0.2.0-observe"   # Phase 2 value
    assert "cl" in ORCHESTRATOR_VERSION.lower()      # phase-3 marker


def test_layer_inputs_schema_version_history_block_present():
    """Constraint 13: HISTORY block must enumerate v1 AND v2."""
    import inspect
    src = inspect.getsource(orch_module)
    assert "v1 (Phase 2" in src
    assert "v2 (Phase 3 Step 1" in src
    # v1 entry must NOT be edited — its key list must still be present verbatim
    assert "schema_version, kernel_hash, kernel_version, orchestrator_version" in src
