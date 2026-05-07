"""
Phase 3 Step 1 — Consecutive losses tracker tests.
Phase 3 Step 2 — Purely-derived cooldown predicate tests.

Council requirements:
  - tracker module functions correctly under various trade-history shapes
  - cooldown predicate is purely derived (no Redis writes, no ACL surface)
  - cooldown duration: 60 minutes fixed
  - cooldown end condition: duration-only (winning trades do NOT clear early)
  - distinct veto string `consecutive_losses_cooldown_on_{pair}`
  - layer_inputs adds boolean `consecutive_losses_cooldown_active`
  - orchestrator integration: streak >= threshold adds losses veto;
    cooldown_active adds cooldown veto; both can fire on same row
  - veto ordering stable: recon → streak-losses → cooldown
  - LAYER_INPUTS_SCHEMA_VERSION_HISTORY preserved (v1 + v2 entries untouched);
    v3 entry appended
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
async def test_layer_inputs_current_schema_includes_consecutive_losses():
    """Original Step-1 invariant: the consecutive_losses key remains present
    in layer_inputs after subsequent schema bumps. Now schema v3."""
    o = Orchestrator()
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)
    with patch.object(orch_module, "redis", fake_redis), \
         patch("governance.orchestrator.recon_last_status",
               new=AsyncMock(return_value=ReconciliationStatus.CLEAN)), \
         patch("governance.consecutive_losses.get_consecutive_loss_count",
               new=AsyncMock(return_value=0)), \
         patch("governance.consecutive_losses.is_in_cooldown",
               new=AsyncMock(return_value=False)):
        snapshot = await o._snapshot_inputs(
            pair="BTC/USDT", pipeline_state={"stage_reached": "rule"},
            proposed_size_usd=10.0, proposed_sl=99.0, proposed_tp=110.0,
        )
    assert snapshot["schema_version"] == LAYER_INPUTS_SCHEMA_VERSION
    assert snapshot["schema_version"] >= 2  # Step 1 still preserved
    assert "consecutive_losses" in snapshot
    assert snapshot["consecutive_losses"] == 0


@pytest.mark.asyncio
async def test_layer_inputs_required_keys_unchanged_after_bumps():
    """Every schema version must be a strict superset of v1 — six required
    keys still present at v3."""
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
               new=AsyncMock(return_value=0)), \
         patch("governance.consecutive_losses.is_in_cooldown",
               new=AsyncMock(return_value=False)):
        snapshot = await o._snapshot_inputs(
            pair="BTC/USDT", pipeline_state={"stage_reached": "rule"},
            proposed_size_usd=10.0, proposed_sl=99.0, proposed_tp=110.0,
        )
    missing = required - set(snapshot.keys())
    assert not missing, f"v3 dropped v1-required key(s): {missing}"


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
    including holds. Phase 3 Steps 1 & 2 must not change this."""
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
               new=AsyncMock(return_value=5)), \
         patch("governance.consecutive_losses.is_in_cooldown",
               new=AsyncMock(return_value=False)):
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
               new=AsyncMock(return_value=4)), \
         patch("governance.consecutive_losses.is_in_cooldown",
               new=AsyncMock(return_value=False)):
        await o.evaluate(
            pair="BTC/USDT", requested_action="buy",
            pipeline_state={"stage_reached": "rule"},
            proposed_size_usd=10.0,
        )
    decision = captured[0]
    assert decision.layer_inputs["consecutive_losses"] == 4
    assert decision.layer_inputs["schema_version"] == LAYER_INPUTS_SCHEMA_VERSION
    assert decision.layer_inputs["consecutive_losses_cooldown_active"] is False
    # In observe-only, final_action passes through; counterfactual records veto
    assert decision.final_action == "buy"
    assert decision.counterfactual_action == "buy"
    assert decision.counterfactual_mode == "restricted"
    assert any(v.startswith("consecutive_losses_4_on_BTC/USDT")
               for v in decision.layer_vetoes)


# ── HISTORY block preserved (v1 unchanged) ─────────────────────────────

def test_orchestrator_version_bumped():
    """ORCHESTRATOR_VERSION must change when schema bumps. Step 2 marker
    is 'cooldown'; Step 1 marker 'cl' must remain present."""
    assert ORCHESTRATOR_VERSION != "0.2.0-observe"             # Phase 2 value
    assert ORCHESTRATOR_VERSION != "0.3.0-observe-with-cl"     # Step 1 value
    assert "cl" in ORCHESTRATOR_VERSION.lower()                # phase-3 marker
    assert "cooldown" in ORCHESTRATOR_VERSION.lower()          # Step 2 marker


def test_layer_inputs_schema_version_history_block_present():
    """Constraint 13: HISTORY block must enumerate v1, v2 AND v3 — append-only,
    prior entries verbatim."""
    import inspect
    src = inspect.getsource(orch_module)
    assert "v1 (Phase 2" in src
    assert "v2 (Phase 3 Step 1" in src
    assert "v3 (Phase 3 Step 2" in src
    # v1 entry must NOT be edited — its key list must still be present verbatim
    assert "schema_version, kernel_hash, kernel_version, orchestrator_version" in src
    # v2 entry must NOT be edited — its addition must still be described verbatim
    assert "consecutive_losses (int)" in src


# ════════════════════════════════════════════════════════════════════════
# Phase 3 Step 2 — cooldown predicate
# ════════════════════════════════════════════════════════════════════════

# ── Cooldown function logic ────────────────────────────────────────────

def _trade_at(pair: str, pnl: float, closed_minutes_ago: int) -> dict:
    return {
        "pair":      pair,
        "status":    "closed",
        "pnl_usd":   pnl,
        "closed_at": (datetime.now(timezone.utc)
                      - timedelta(minutes=closed_minutes_ago)).isoformat(),
    }


@pytest.mark.asyncio
async def test_cooldown_inactive_when_no_trades():
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=[])):
        active = await cl.is_in_cooldown("BTC/USDT")
    assert active is False


@pytest.mark.asyncio
async def test_cooldown_inactive_when_streak_below_threshold():
    """Two losses in last 30 minutes — below threshold → no cooldown."""
    trades = [
        _trade_at("BTC/USDT", -1.0, 30),
        _trade_at("BTC/USDT", -1.0, 20),
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)):
        active = await cl.is_in_cooldown("BTC/USDT")
    assert active is False


@pytest.mark.asyncio
async def test_cooldown_active_when_threshold_run_within_60_minutes():
    """3 losses, most recent 30 minutes ago → cooldown active."""
    trades = [
        _trade_at("BTC/USDT", -1.0, 50),
        _trade_at("BTC/USDT", -1.0, 40),
        _trade_at("BTC/USDT", -1.0, 30),
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)):
        active = await cl.is_in_cooldown("BTC/USDT")
    assert active is True


@pytest.mark.asyncio
async def test_cooldown_inactive_when_threshold_run_older_than_60_minutes():
    """3 losses, most recent 90 minutes ago → cooldown expired."""
    trades = [
        _trade_at("BTC/USDT", -1.0, 110),
        _trade_at("BTC/USDT", -1.0, 100),
        _trade_at("BTC/USDT", -1.0, 90),
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)):
        active = await cl.is_in_cooldown("BTC/USDT")
    assert active is False


@pytest.mark.asyncio
async def test_cooldown_active_at_60_minute_boundary():
    """Boundary check: a loss closed 'at' the cutoff still counts as
    within (>=). Slightly inside the window."""
    trades = [
        _trade_at("BTC/USDT", -1.0, 59),
        _trade_at("BTC/USDT", -1.0, 58),
        _trade_at("BTC/USDT", -1.0, 57),
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)):
        active = await cl.is_in_cooldown("BTC/USDT")
    assert active is True


@pytest.mark.asyncio
async def test_cooldown_persists_across_winning_trades_within_window():
    """Council-locked end condition: winning trades after the threshold
    crossing do NOT clear cooldown early. 3 losses, then a win, all
    within the cooldown window — cooldown still active because the
    last in-run loss is within 60 minutes."""
    trades = [
        _trade_at("BTC/USDT", -1.0, 50),
        _trade_at("BTC/USDT", -1.0, 40),
        _trade_at("BTC/USDT", -1.0, 30),  # last in-run loss, 30min ago
        _trade_at("BTC/USDT",  2.0, 20),  # win, does NOT clear cooldown
        _trade_at("BTC/USDT",  1.0, 10),  # another win, also does NOT clear
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)):
        active = await cl.is_in_cooldown("BTC/USDT")
    assert active is True


@pytest.mark.asyncio
async def test_cooldown_inactive_when_only_recent_run_below_threshold():
    """Old 3-loss run (>60min ago) followed by win, then 1-2 recent losses.
    The recent run is below threshold; the old run's last in-run loss
    is outside the window. Cooldown should NOT be active."""
    trades = [
        _trade_at("BTC/USDT", -1.0, 200),
        _trade_at("BTC/USDT", -1.0, 180),
        _trade_at("BTC/USDT", -1.0, 170),  # old run last loss, 170min ago
        _trade_at("BTC/USDT",  2.0, 100),  # win between runs
        _trade_at("BTC/USDT", -1.0,  20),  # recent, but only streak=1
        _trade_at("BTC/USDT", -1.0,  10),  # recent, streak=2 (below 3)
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)):
        active = await cl.is_in_cooldown("BTC/USDT")
    assert active is False


@pytest.mark.asyncio
async def test_cooldown_picks_most_recent_qualifying_run():
    """An old qualifying run AND a new qualifying run; cooldown is
    decided by the more recent one's last in-run loss."""
    trades = [
        _trade_at("BTC/USDT", -1.0, 300),  # old run
        _trade_at("BTC/USDT", -1.0, 280),
        _trade_at("BTC/USDT", -1.0, 260),
        _trade_at("BTC/USDT",  2.0, 200),  # win between
        _trade_at("BTC/USDT", -1.0,  40),  # new run, all within 60min
        _trade_at("BTC/USDT", -1.0,  30),
        _trade_at("BTC/USDT", -1.0,  20),
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)):
        active = await cl.is_in_cooldown("BTC/USDT")
    assert active is True


@pytest.mark.asyncio
async def test_cooldown_filters_by_pair():
    """A 3-loss run on ETH/USDT must not place BTC/USDT into cooldown."""
    trades = [
        _trade_at("ETH/USDT", -1.0, 50),
        _trade_at("ETH/USDT", -1.0, 40),
        _trade_at("ETH/USDT", -1.0, 30),
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)):
        eth_active = await cl.is_in_cooldown("ETH/USDT")
        btc_active = await cl.is_in_cooldown("BTC/USDT")
    assert eth_active is True
    assert btc_active is False


@pytest.mark.asyncio
async def test_cooldown_db_failure_returns_false_safely():
    """Safe direction — do not invent a cooldown when DB is unavailable."""
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(side_effect=RuntimeError("db down"))):
        active = await cl.is_in_cooldown("BTC/USDT")
    assert active is False


@pytest.mark.asyncio
async def test_cooldown_unparseable_pnl_resets_streak():
    """Unparseable pnl ends the running streak. If the would-be threshold
    crossing depended on that trade, no cooldown should fire."""
    trades = [
        _trade_at("BTC/USDT", -1.0, 50),
        {"pair": "BTC/USDT", "status": "closed", "pnl_usd": "garbage",
         "closed_at": (datetime.now(timezone.utc)
                       - timedelta(minutes=40)).isoformat()},
        _trade_at("BTC/USDT", -1.0, 30),
        _trade_at("BTC/USDT", -1.0, 20),
        # After the garbage row, the streak restarts: -1 (30min), -1 (20min)
        # — only 2, below threshold → no cooldown.
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)):
        active = await cl.is_in_cooldown("BTC/USDT")
    assert active is False


@pytest.mark.asyncio
async def test_cooldown_break_even_resets_streak():
    """Break-even (pnl=0) is not a loss; it ends a streak just like a win.
    Ensures cooldown is not falsely manufactured by zero-PnL trades."""
    trades = [
        _trade_at("BTC/USDT", -1.0, 50),
        _trade_at("BTC/USDT",  0.0, 40),  # break-even — resets streak
        _trade_at("BTC/USDT", -1.0, 30),
        _trade_at("BTC/USDT", -1.0, 20),
        # Streak after break-even: -1, -1 = 2, below threshold.
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)):
        active = await cl.is_in_cooldown("BTC/USDT")
    assert active is False


# ── Cooldown duration constant matches Council spec ────────────────────

def test_cooldown_duration_is_60_minutes():
    """Council-locked: COOLDOWN_DURATION_MINUTES = 60 (fixed)."""
    assert cl.COOLDOWN_DURATION_MINUTES == 60


# ── Orchestrator integration: layer_inputs v3 includes cooldown key ────

@pytest.mark.asyncio
async def test_layer_inputs_v3_includes_cooldown_active_key():
    """Schema v3 adds boolean consecutive_losses_cooldown_active."""
    o = Orchestrator()
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)
    with patch.object(orch_module, "redis", fake_redis), \
         patch("governance.orchestrator.recon_last_status",
               new=AsyncMock(return_value=ReconciliationStatus.CLEAN)), \
         patch("governance.consecutive_losses.get_consecutive_loss_count",
               new=AsyncMock(return_value=0)), \
         patch("governance.consecutive_losses.is_in_cooldown",
               new=AsyncMock(return_value=True)):
        snapshot = await o._snapshot_inputs(
            pair="BTC/USDT", pipeline_state={"stage_reached": "rule"},
            proposed_size_usd=10.0, proposed_sl=99.0, proposed_tp=110.0,
        )
    assert snapshot["schema_version"] == 3
    assert "consecutive_losses_cooldown_active" in snapshot
    assert snapshot["consecutive_losses_cooldown_active"] is True
    # And the bool — not int, not str
    assert isinstance(snapshot["consecutive_losses_cooldown_active"], bool)


@pytest.mark.asyncio
async def test_layer_inputs_v3_cooldown_key_absent_when_inactive():
    """Key always present — value is False when not in cooldown."""
    o = Orchestrator()
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)
    with patch.object(orch_module, "redis", fake_redis), \
         patch("governance.orchestrator.recon_last_status",
               new=AsyncMock(return_value=ReconciliationStatus.CLEAN)), \
         patch("governance.consecutive_losses.get_consecutive_loss_count",
               new=AsyncMock(return_value=0)), \
         patch("governance.consecutive_losses.is_in_cooldown",
               new=AsyncMock(return_value=False)):
        snapshot = await o._snapshot_inputs(
            pair="BTC/USDT", pipeline_state={"stage_reached": "rule"},
            proposed_size_usd=10.0, proposed_sl=99.0, proposed_tp=110.0,
        )
    assert snapshot["consecutive_losses_cooldown_active"] is False


# ── Counterfactual: cooldown veto branch ───────────────────────────────

def test_counterfactual_cooldown_active_streak_zero_adds_cooldown_veto():
    """Cooldown alone (streak=0) → cooldown veto fires, mode → restricted."""
    o = Orchestrator()
    layer_inputs = {
        "governance_mode": "normal",
        "reconciliation_raw": "clean",
        "reconciliation_implication": "normal",
        "pair": "BTC/USDT",
        "consecutive_losses": 0,
        "consecutive_losses_cooldown_active": True,
    }
    cf = o._compute_counterfactual("buy", 10.0, layer_inputs)
    assert "consecutive_losses_cooldown_on_BTC/USDT" in cf["vetoes"]
    # No streak veto since streak=0
    assert all(v != "consecutive_losses_0_on_BTC/USDT" for v in cf["vetoes"])
    assert cf["mode"] == "restricted"
    assert cf["size_scale"] == 0.5
    assert cf["size_usd"] == 5.0


def test_counterfactual_cooldown_inactive_no_cooldown_veto():
    """consecutive_losses_cooldown_active=False → no cooldown veto."""
    o = Orchestrator()
    layer_inputs = {
        "governance_mode": "normal",
        "reconciliation_raw": "clean",
        "reconciliation_implication": "normal",
        "pair": "BTC/USDT",
        "consecutive_losses": 0,
        "consecutive_losses_cooldown_active": False,
    }
    cf = o._compute_counterfactual("buy", 10.0, layer_inputs)
    assert all(not v.startswith("consecutive_losses_cooldown")
               for v in cf["vetoes"])
    assert cf["mode"] == "normal"


def test_counterfactual_cooldown_and_streak_both_fire():
    """Streak=3 AND cooldown=True → both vetoes fire on the same row."""
    o = Orchestrator()
    layer_inputs = {
        "governance_mode": "normal",
        "reconciliation_raw": "clean",
        "reconciliation_implication": "normal",
        "pair": "BTC/USDT",
        "consecutive_losses": 3,
        "consecutive_losses_cooldown_active": True,
    }
    cf = o._compute_counterfactual("buy", 10.0, layer_inputs)
    assert "consecutive_losses_3_on_BTC/USDT" in cf["vetoes"]
    assert "consecutive_losses_cooldown_on_BTC/USDT" in cf["vetoes"]
    # Single mode escalation — not "double-restricted"
    assert cf["mode"] == "restricted"
    assert cf["size_scale"] == 0.5


def test_counterfactual_hold_request_bypasses_cooldown_veto():
    """Hold requests are non-actions — cooldown veto does not apply."""
    o = Orchestrator()
    layer_inputs = {
        "governance_mode": "normal",
        "reconciliation_raw": "clean",
        "reconciliation_implication": "normal",
        "pair": "BTC/USDT",
        "consecutive_losses": 0,
        "consecutive_losses_cooldown_active": True,
    }
    cf = o._compute_counterfactual("hold", 0.0, layer_inputs)
    assert all(not v.startswith("consecutive_losses_cooldown")
               for v in cf["vetoes"])


def test_counterfactual_cooldown_does_not_downgrade_frozen():
    """Mode escalation only: if recon already froze, cooldown veto records
    but mode stays frozen."""
    o = Orchestrator()
    layer_inputs = {
        "governance_mode": "normal",
        "reconciliation_raw": "unknown",
        "reconciliation_implication": "frozen",
        "pair": "BTC/USDT",
        "consecutive_losses": 0,
        "consecutive_losses_cooldown_active": True,
    }
    cf = o._compute_counterfactual("buy", 10.0, layer_inputs)
    assert "consecutive_losses_cooldown_on_BTC/USDT" in cf["vetoes"]
    assert cf["mode"] == "frozen"
    assert cf["action"] == "hold"
    assert cf["size_usd"] == 0.0


# ── Veto ordering: recon → streak-losses → cooldown ────────────────────

def test_veto_order_recon_then_streak_then_cooldown():
    """Council-locked ordering. All three vetoes present in correct order."""
    o = Orchestrator()
    layer_inputs = {
        "governance_mode": "normal",
        "reconciliation_raw": "stale",
        "reconciliation_implication": "restricted",
        "pair": "BTC/USDT",
        "consecutive_losses": 3,
        "consecutive_losses_cooldown_active": True,
    }
    cf = o._compute_counterfactual("buy", 10.0, layer_inputs)
    assert len(cf["vetoes"]) == 3
    assert cf["vetoes"][0].startswith("recon_")
    assert cf["vetoes"][1].startswith("consecutive_losses_3")
    assert cf["vetoes"][2].startswith("consecutive_losses_cooldown")


def test_veto_order_streak_then_cooldown_when_no_recon():
    """Streak before cooldown when only those two fire (no recon issue)."""
    o = Orchestrator()
    layer_inputs = {
        "governance_mode": "normal",
        "reconciliation_raw": "clean",
        "reconciliation_implication": "normal",
        "pair": "BTC/USDT",
        "consecutive_losses": 5,
        "consecutive_losses_cooldown_active": True,
    }
    cf = o._compute_counterfactual("buy", 10.0, layer_inputs)
    assert len(cf["vetoes"]) == 2
    assert cf["vetoes"][0] == "consecutive_losses_5_on_BTC/USDT"
    assert cf["vetoes"][1] == "consecutive_losses_cooldown_on_BTC/USDT"


# ── R3 hash determinism preserved across cooldown ──────────────────────

def test_hash_unchanged_when_cooldown_inactive_no_veto():
    """With cooldown_active=False and streak=0 and clean recon, hash is
    identical to the Phase 2 / Step 1 baseline."""
    h = compute_counterfactual_hash("buy", 1.0, "normal", [])
    assert h == "0ea24c8dabcac50e22a7ac7635bcf4ef454aaa3dee6da79a7f8236475091d7e7"


def test_hash_changes_when_cooldown_veto_fires():
    """R3: cooldown veto produces a different hash from no-veto."""
    h_no = compute_counterfactual_hash("buy", 1.0, "normal", [])
    h_yes = compute_counterfactual_hash(
        "buy", 0.5, "restricted", ["consecutive_losses_cooldown_on_BTC/USDT"],
    )
    assert h_no != h_yes


def test_hash_stable_for_identical_cooldown_vetoes():
    """Same cooldown veto string → same hash, twice."""
    h1 = compute_counterfactual_hash(
        "buy", 0.5, "restricted", ["consecutive_losses_cooldown_on_BTC/USDT"],
    )
    h2 = compute_counterfactual_hash(
        "buy", 0.5, "restricted", ["consecutive_losses_cooldown_on_BTC/USDT"],
    )
    assert h1 == h2


def test_hash_streak_only_differs_from_streak_plus_cooldown():
    """Adding cooldown veto to a streak-only row produces a different hash."""
    h_streak_only = compute_counterfactual_hash(
        "buy", 0.5, "restricted", ["consecutive_losses_3_on_BTC/USDT"],
    )
    h_both = compute_counterfactual_hash(
        "buy", 0.5, "restricted",
        ["consecutive_losses_3_on_BTC/USDT",
         "consecutive_losses_cooldown_on_BTC/USDT"],
    )
    assert h_streak_only != h_both


def test_hash_cooldown_string_differs_from_streak_string():
    """Distinct veto strings produce distinct hashes — Council required
    separability for review-query analysis."""
    h_streak = compute_counterfactual_hash(
        "buy", 0.5, "restricted", ["consecutive_losses_3_on_BTC/USDT"],
    )
    h_cooldown = compute_counterfactual_hash(
        "buy", 0.5, "restricted", ["consecutive_losses_cooldown_on_BTC/USDT"],
    )
    assert h_streak != h_cooldown


# ── Constraint 18 with cooldown firing ─────────────────────────────────

@pytest.mark.asyncio
async def test_decision_row_written_when_cooldown_fires_on_hold():
    """Hold + cooldown_active still produces a decision row (Constraint 18)
    even though no cooldown veto fires for the hold (non-action)."""
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
               new=AsyncMock(return_value=0)), \
         patch("governance.consecutive_losses.is_in_cooldown",
               new=AsyncMock(return_value=True)):
        await o.evaluate(
            pair="BTC/USDT", requested_action="hold",
            pipeline_state={"stage_reached": "rule"},
            proposed_size_usd=0.0,
        )
    assert len(captured) == 1
    assert captured[0].final_action == "hold"
    assert captured[0].layer_inputs["consecutive_losses_cooldown_active"] is True


@pytest.mark.asyncio
async def test_orchestrator_evaluate_records_cooldown_veto():
    """End-to-end: cooldown active + actionable buy → cooldown veto in
    layer_vetoes; observe-only passthrough preserved."""
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
               new=AsyncMock(return_value=0)), \
         patch("governance.consecutive_losses.is_in_cooldown",
               new=AsyncMock(return_value=True)):
        await o.evaluate(
            pair="BTC/USDT", requested_action="buy",
            pipeline_state={"stage_reached": "rule"},
            proposed_size_usd=10.0,
        )
    decision = captured[0]
    assert "consecutive_losses_cooldown_on_BTC/USDT" in decision.layer_vetoes
    # Observe-only passthrough preserved
    assert decision.final_action == "buy"
    assert decision.observe_only_passthrough is True
    # Counterfactual reflects veto
    assert decision.counterfactual_mode == "restricted"
    assert decision.counterfactual_size_scale == 0.5
    assert decision.layer_inputs["consecutive_losses_cooldown_active"] is True


# ── Architecture invariant: no Redis writes from cooldown path ─────────

@pytest.mark.asyncio
async def test_cooldown_function_makes_no_redis_calls():
    """Architecture invariant (Council-locked): is_in_cooldown is purely
    derived from the trades table — no Redis interaction at all."""
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)
    fake_redis.set = AsyncMock()
    fake_redis.setex = AsyncMock()
    fake_redis.expire = AsyncMock()
    fake_redis.delete = AsyncMock()
    trades = [
        _trade_at("BTC/USDT", -1.0, 30),
        _trade_at("BTC/USDT", -1.0, 20),
        _trade_at("BTC/USDT", -1.0, 10),
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)), \
         patch.object(orch_module, "redis", fake_redis):
        await cl.is_in_cooldown("BTC/USDT")
    # No Redis method called
    fake_redis.set.assert_not_called()
    fake_redis.setex.assert_not_called()
    fake_redis.expire.assert_not_called()
    fake_redis.get.assert_not_called()
    fake_redis.delete.assert_not_called()
