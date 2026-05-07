"""
Phase 2.5 R3 — Counterfactual determinism hash tests.

Council-mandated tests:
  - test_counterfactual_hash_stable_for_identical_inputs
  - test_counterfactual_hash_changes_when_vetoes_change
  - test_counterfactual_hash_persisted_on_decision_row

Plus invariants:
  - hash function is pure (no time/random dependency)
  - canonical encoding (sorted keys, deterministic separators)
  - same hash across two evaluate() calls with identical inputs
"""

from __future__ import annotations
import hashlib
import json
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock, MagicMock
import pytest

from governance.orchestrator import (
    Orchestrator, OrchestratorDecision,
    compute_counterfactual_hash,
)
from governance.reconciliation import ReconciliationStatus


# ── Council acceptance test 1: stable across runs ───────────────────────

def test_counterfactual_hash_stable_for_identical_inputs():
    """Same {action, size_scale, mode, vetoes} -> same hash, every time."""
    h1 = compute_counterfactual_hash(
        action="buy", size_scale=1.0, mode="normal", vetoes=[],
    )
    h2 = compute_counterfactual_hash(
        action="buy", size_scale=1.0, mode="normal", vetoes=[],
    )
    assert h1 == h2
    assert len(h1) == 64   # SHA-256 hex


def test_counterfactual_hash_with_vetoes_stable():
    h1 = compute_counterfactual_hash(
        action="hold", size_scale=0.0, mode="frozen",
        vetoes=["recon_unknown", "consecutive_losses"],
    )
    h2 = compute_counterfactual_hash(
        action="hold", size_scale=0.0, mode="frozen",
        vetoes=["recon_unknown", "consecutive_losses"],
    )
    assert h1 == h2


# ── Council acceptance test 2: vetoes mutation -> different hash ────────

def test_counterfactual_hash_changes_when_vetoes_change():
    base = compute_counterfactual_hash(
        action="buy", size_scale=1.0, mode="normal", vetoes=[],
    )
    add_one = compute_counterfactual_hash(
        action="buy", size_scale=1.0, mode="normal", vetoes=["recon_stale"],
    )
    add_two = compute_counterfactual_hash(
        action="buy", size_scale=1.0, mode="normal",
        vetoes=["recon_stale", "consecutive_losses"],
    )
    assert base != add_one
    assert add_one != add_two
    assert base != add_two


def test_counterfactual_hash_changes_when_action_changes():
    h_buy = compute_counterfactual_hash(
        action="buy", size_scale=1.0, mode="normal", vetoes=[],
    )
    h_hold = compute_counterfactual_hash(
        action="hold", size_scale=1.0, mode="normal", vetoes=[],
    )
    assert h_buy != h_hold


def test_counterfactual_hash_changes_when_mode_changes():
    h_normal = compute_counterfactual_hash(
        action="buy", size_scale=1.0, mode="normal", vetoes=[],
    )
    h_frozen = compute_counterfactual_hash(
        action="buy", size_scale=1.0, mode="frozen", vetoes=[],
    )
    assert h_normal != h_frozen


def test_counterfactual_hash_changes_when_size_scale_changes():
    h_full = compute_counterfactual_hash(
        action="buy", size_scale=1.0, mode="normal", vetoes=[],
    )
    h_half = compute_counterfactual_hash(
        action="buy", size_scale=0.5, mode="normal", vetoes=[],
    )
    assert h_full != h_half


# ── List ordering preserved (NOT auto-sorted) ───────────────────────────

def test_counterfactual_hash_preserves_list_order():
    """Council R3: 'stable list ordering preserved'. Vetoes [A,B] hashes
    differently from [B,A] — emission order is part of the identity."""
    h_ab = compute_counterfactual_hash(
        action="hold", size_scale=0.0, mode="frozen",
        vetoes=["recon_unknown", "consecutive_losses"],
    )
    h_ba = compute_counterfactual_hash(
        action="hold", size_scale=0.0, mode="frozen",
        vetoes=["consecutive_losses", "recon_unknown"],
    )
    assert h_ab != h_ba


# ── Canonical encoding contract ─────────────────────────────────────────

def test_counterfactual_hash_uses_canonical_json():
    """Verify the hash matches what canonical json.dumps produces."""
    expected = hashlib.sha256(
        json.dumps({
            "action": "buy", "size_scale": 1.0,
            "mode": "normal", "vetoes": [],
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    actual = compute_counterfactual_hash("buy", 1.0, "normal", [])
    assert actual == expected


def test_counterfactual_hash_size_scale_normalized_to_float():
    """int 1 and float 1.0 should produce the same hash (size_scale is float)."""
    h_int = compute_counterfactual_hash("buy", 1, "normal", [])
    h_float = compute_counterfactual_hash("buy", 1.0, "normal", [])
    assert h_int == h_float


# ── Cross-process determinism ───────────────────────────────────────────

def test_counterfactual_hash_deterministic_across_processes():
    """A subprocess computing the same hash must produce the same value.
    Guards against any module-load-time non-determinism."""
    probe = textwrap.dedent("""
        from governance.orchestrator import compute_counterfactual_hash
        h = compute_counterfactual_hash(
            action="buy", size_scale=1.0, mode="normal", vetoes=[],
        )
        print(h)
    """)
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, timeout=15, text=True,
    )
    assert result.returncode == 0, f"subprocess failed: {result.stderr}"
    sub_hash = result.stdout.strip()
    in_process_hash = compute_counterfactual_hash("buy", 1.0, "normal", [])
    assert sub_hash == in_process_hash


# ── Council acceptance test 3: hash persists on decision row ────────────

def test_counterfactual_hash_persisted_on_decision_row():
    """The hash field must be on every persisted row's to_db() output."""
    decision = OrchestratorDecision(
        decision_id="test-id",
        decision_time=datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
        pair="BTC/USDT",
        requested_action="buy",
        final_action="buy",
        size_usd=10.0, size_scale=1.0,
        observe_only_passthrough=True,
        counterfactual_action="hold",
        counterfactual_size_usd=0.0,
        counterfactual_size_scale=0.0,
        counterfactual_mode="frozen",
        counterfactual_hash="0" * 64,
        layer_vetoes=["recon_unknown"],
        layer_inputs={"schema_version": 1},
        governance_mode="normal",
        explanation="test",
    )
    row = decision.to_db()
    assert "counterfactual_hash" in row
    assert row["counterfactual_hash"] == "0" * 64


@pytest.mark.asyncio
async def test_orchestrator_evaluate_attaches_hash():
    """Orchestrator.evaluate() must populate counterfactual_hash on the
    decision before persisting."""
    from governance import orchestrator as orch_module
    captured_decisions = []

    async def capture(decision):
        captured_decisions.append(decision)

    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)

    o = Orchestrator()
    with patch.object(o, "_persist_decision", new=capture), \
         patch.object(orch_module, "redis", fake_redis), \
         patch("governance.orchestrator.recon_last_status",
               new=AsyncMock(return_value=ReconciliationStatus.CLEAN)):
        await o.evaluate(
            pair="BTC/USDT", requested_action="buy",
            pipeline_state={"stage_reached": "rule"},
            proposed_size_usd=10.0,
        )

    assert len(captured_decisions) == 1
    decision = captured_decisions[0]
    assert decision.counterfactual_hash is not None
    assert len(decision.counterfactual_hash) == 64

    # Hash must match canonical computation of the counterfactual fields
    expected = compute_counterfactual_hash(
        action     = decision.counterfactual_action,
        size_scale = decision.counterfactual_size_scale,
        mode       = decision.counterfactual_mode,
        vetoes     = decision.layer_vetoes,
    )
    assert decision.counterfactual_hash == expected


@pytest.mark.asyncio
async def test_two_evaluate_calls_with_identical_inputs_match_hash():
    """Two orchestrator evaluations with the same governance state and
    inputs MUST produce the same counterfactual_hash."""
    from governance import orchestrator as orch_module
    decisions = []

    async def capture(decision):
        decisions.append(decision)

    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)

    o = Orchestrator()
    with patch.object(o, "_persist_decision", new=capture), \
         patch.object(orch_module, "redis", fake_redis), \
         patch("governance.orchestrator.recon_last_status",
               new=AsyncMock(return_value=ReconciliationStatus.CLEAN)):
        for _ in range(2):
            await o.evaluate(
                pair="BTC/USDT", requested_action="buy",
                pipeline_state={"stage_reached": "rule"},
                proposed_size_usd=10.0,
            )

    assert len(decisions) == 2
    # Different decision_ids (UUIDs), same counterfactual_hash
    assert decisions[0].decision_id != decisions[1].decision_id
    assert decisions[0].counterfactual_hash == decisions[1].counterfactual_hash


@pytest.mark.asyncio
async def test_different_recon_state_produces_different_hash():
    """If reconciliation transitions CLEAN -> UNKNOWN between two evaluations,
    the counterfactual changes (vetoes added) so hash MUST change."""
    from governance import orchestrator as orch_module
    decisions = []

    async def capture(decision):
        decisions.append(decision)

    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)

    o = Orchestrator()
    with patch.object(o, "_persist_decision", new=capture), \
         patch.object(orch_module, "redis", fake_redis):

        with patch("governance.orchestrator.recon_last_status",
                   new=AsyncMock(return_value=ReconciliationStatus.CLEAN)):
            await o.evaluate(pair="BTC/USDT", requested_action="buy",
                             pipeline_state={"stage_reached": "rule"},
                             proposed_size_usd=10.0)

        with patch("governance.orchestrator.recon_last_status",
                   new=AsyncMock(return_value=ReconciliationStatus.UNKNOWN)):
            await o.evaluate(pair="BTC/USDT", requested_action="buy",
                             pipeline_state={"stage_reached": "rule"},
                             proposed_size_usd=10.0)

    assert decisions[0].counterfactual_hash != decisions[1].counterfactual_hash
    assert decisions[0].layer_vetoes == []
    assert "recon_unknown" in decisions[1].layer_vetoes
