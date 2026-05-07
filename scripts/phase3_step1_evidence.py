"""
scripts/phase3_step1_evidence.py
Phase 3 Step 1 — Consecutive losses tracker evidence.

Captures three artifacts:
  - evidence_p3s1_tracker.jsonl          tracker output for known fixtures
  - evidence_p3s1_orchestrator_veto.jsonl orchestrator decision row showing
                                           consecutive_losses veto in observe-only
  - evidence_p3s1_invariant_preservation.jsonl
                                          checks that v1 hash for clean inputs is
                                          unchanged + schema v2 has all v1 keys +
                                          R3 hash differs when veto fires
"""
from __future__ import annotations
import asyncio
import json
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock, MagicMock


def _stamp(rec: dict) -> dict:
    rec.setdefault("ts", datetime.now(timezone.utc).isoformat())
    return rec


def _write(records: list, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, default=str, sort_keys=True) + "\n")
    print(f"  -> wrote {path} ({len(records)} events)")


def _trade(pair: str, pnl: float, closed_seconds_ago: int) -> dict:
    return {
        "pair":      pair,
        "status":    "closed",
        "pnl_usd":   pnl,
        "closed_at": (datetime.now(timezone.utc)
                      - timedelta(seconds=closed_seconds_ago)).isoformat(),
    }


# ── Evidence 1: tracker logic ───────────────────────────────────────────

async def evidence_tracker():
    print("\n[1/3] Tracker fixture coverage...")
    from governance import consecutive_losses as cl

    events = []
    fixtures = [
        ("empty_history",
         [],
         "BTC/USDT", 0),
        ("3_consecutive_losses",
         [_trade("BTC/USDT", -1.0, 100), _trade("BTC/USDT", -1.0, 200),
          _trade("BTC/USDT", -1.0, 300), _trade("BTC/USDT",  2.0, 400)],
         "BTC/USDT", 3),
        ("5_consecutive_losses",
         [_trade("BTC/USDT", -1, n*100) for n in range(1, 6)],
         "BTC/USDT", 5),
        ("win_breaks_streak_at_2",
         [_trade("BTC/USDT", -1, 100), _trade("BTC/USDT", -1, 200),
          _trade("BTC/USDT",  1, 300), _trade("BTC/USDT", -1, 400)],
         "BTC/USDT", 2),
        ("breakeven_breaks_streak",
         [_trade("BTC/USDT", -1, 100), _trade("BTC/USDT", 0, 200),
          _trade("BTC/USDT", -1, 300)],
         "BTC/USDT", 1),
        ("filtered_by_pair",
         [_trade("ETH/USDT", -1, 100), _trade("BTC/USDT", -1, 200),
          _trade("BTC/USDT", -1, 300), _trade("BTC/USDT", -1, 400)],
         "BTC/USDT", 3),
    ]
    for name, trades, pair, expected in fixtures:
        with patch.object(cl.db, "get_trades_for_period",
                          new=AsyncMock(return_value=trades)):
            actual = await cl.get_consecutive_loss_count(pair)
        events.append(_stamp({
            "fixture":  name,
            "pair":     pair,
            "expected": expected,
            "actual":   actual,
            "verdict":  "PASS" if actual == expected else "FAIL",
        }))
    _write(events, "evidence_p3s1_tracker.jsonl")


# ── Evidence 2: orchestrator records consecutive_losses veto ────────────

async def evidence_orchestrator_veto():
    print("\n[2/3] Orchestrator integration with consecutive_losses...")
    from governance import orchestrator as orch_module
    from governance.orchestrator import Orchestrator, LAYER_INPUTS_SCHEMA_VERSION
    from governance.reconciliation import ReconciliationStatus

    captured_rows = []

    async def capture(decision):
        captured_rows.append(decision.to_db())

    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)

    o = Orchestrator()
    scenarios = [
        ("A_clean_no_streak",
         ReconciliationStatus.CLEAN, 0, "buy", 10.0),
        ("B_clean_streak_2_below_threshold",
         ReconciliationStatus.CLEAN, 2, "buy", 10.0),
        ("C_clean_streak_3_at_threshold",
         ReconciliationStatus.CLEAN, 3, "buy", 10.0),
        ("D_clean_streak_5_above_threshold",
         ReconciliationStatus.CLEAN, 5, "buy", 10.0),
        ("E_recon_unknown_with_streak_5_frozen_dominates",
         ReconciliationStatus.UNKNOWN, 5, "buy", 10.0),
        ("F_hold_request_streak_5_no_losses_veto",
         ReconciliationStatus.CLEAN, 5, "hold", 0.0),
    ]
    with patch.object(o, "_persist_decision", new=capture), \
         patch.object(orch_module, "redis", fake_redis):
        for name, recon_status, cl_count, action, size in scenarios:
            with patch("governance.orchestrator.recon_last_status",
                       new=AsyncMock(return_value=recon_status)), \
                 patch("governance.consecutive_losses.get_consecutive_loss_count",
                       new=AsyncMock(return_value=cl_count)):
                pre = len(captured_rows)
                await o.evaluate(
                    pair="BTC/USDT", requested_action=action,
                    pipeline_state={"stage_reached": "rule"},
                    proposed_size_usd=size,
                )
                row = captured_rows[pre]
                row["scenario"] = name
                row["expected_recon"] = recon_status.value
                row["expected_cl_count"] = cl_count

    annotated = []
    for row in captured_rows:
        annotated.append({
            "scenario":              row["scenario"],
            "decision_id":           row["decision_id"],
            "pair":                  row["pair"],
            "requested_action":      row["requested_action"],
            "final_action":          row["final_action"],
            "counterfactual_action": row["counterfactual_action"],
            "counterfactual_mode":   row["counterfactual_mode"],
            "counterfactual_size_usd":   row["counterfactual_size_usd"],
            "counterfactual_size_scale": row["counterfactual_size_scale"],
            "layer_vetoes":          row["layer_vetoes"],
            "consecutive_losses":    row["layer_inputs"].get("consecutive_losses"),
            "reconciliation_raw":    row["layer_inputs"].get("reconciliation_raw"),
            "schema_version":        row["layer_inputs"].get("schema_version"),
            "counterfactual_hash":   row["counterfactual_hash"],
            "observe_only_passthrough": row["observe_only_passthrough"],
        })
    _write(annotated, "evidence_p3s1_orchestrator_veto.jsonl")


# ── Evidence 3: invariant preservation ──────────────────────────────────

async def evidence_invariant_preservation():
    print("\n[3/3] Invariant-preservation checks...")
    from governance.orchestrator import (
        compute_counterfactual_hash, LAYER_INPUTS_SCHEMA_VERSION,
        ORCHESTRATOR_VERSION,
    )

    events = []

    # R3 hash for clean-recon, no-veto inputs must equal Phase-2 evidence.
    h_clean = compute_counterfactual_hash("buy", 1.0, "normal", [])
    events.append(_stamp({
        "check": "r3_hash_unchanged_for_no_veto_clean_inputs",
        "computed_hash": h_clean,
        "phase2_hash":   "0ea24c8dabcac50e22a7ac7635bcf4ef454aaa3dee6da79a7f8236475091d7e7",
        "verdict": "PASS" if h_clean ==
            "0ea24c8dabcac50e22a7ac7635bcf4ef454aaa3dee6da79a7f8236475091d7e7"
            else "FAIL",
    }))

    # R3 hash differs when consecutive_losses veto fires
    h_no = compute_counterfactual_hash("buy", 1.0, "normal", [])
    h_yes = compute_counterfactual_hash(
        "buy", 0.5, "restricted", ["consecutive_losses_3_on_BTC/USDT"],
    )
    events.append(_stamp({
        "check":   "r3_hash_differs_when_losses_veto_fires",
        "hash_no_veto":  h_no,
        "hash_with_veto": h_yes,
        "verdict": "PASS" if h_no != h_yes else "FAIL",
    }))

    # Schema v2 contains v1 required keys (constraint 6)
    from governance import orchestrator as orch_module
    from governance.orchestrator import Orchestrator
    from governance.reconciliation import ReconciliationStatus
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
    required_v1_keys = {
        "schema_version", "kernel_hash", "kernel_version", "orchestrator_version",
        "governance_mode", "reconciliation_raw", "reconciliation_implication",
    }
    missing = required_v1_keys - set(snapshot.keys())
    events.append(_stamp({
        "check":             "v2_is_strict_superset_of_v1_required_keys",
        "schema_version":    snapshot["schema_version"],
        "missing_v1_keys":   sorted(missing),
        "v2_added_keys":     sorted(set(snapshot.keys()) - required_v1_keys),
        "verdict":           "PASS" if not missing and snapshot["schema_version"] >= 2
                             else "FAIL",
    }))

    # Constraint 13: schema_version is monotonic integer >= 1
    events.append(_stamp({
        "check":         "schema_version_monotonic_int",
        "current_value": LAYER_INPUTS_SCHEMA_VERSION,
        "is_int":        isinstance(LAYER_INPUTS_SCHEMA_VERSION, int),
        "ge_one":        LAYER_INPUTS_SCHEMA_VERSION >= 1,
        "verdict":       "PASS" if isinstance(LAYER_INPUTS_SCHEMA_VERSION, int)
                          and LAYER_INPUTS_SCHEMA_VERSION >= 1 else "FAIL",
    }))

    # ORCHESTRATOR_VERSION bumped from Phase 2 string
    events.append(_stamp({
        "check":              "orchestrator_version_bumped",
        "current":            ORCHESTRATOR_VERSION,
        "phase2_value":       "0.2.0-observe",
        "differs":            ORCHESTRATOR_VERSION != "0.2.0-observe",
        "verdict":            "PASS" if ORCHESTRATOR_VERSION != "0.2.0-observe"
                              else "FAIL",
    }))

    _write(events, "evidence_p3s1_invariant_preservation.jsonl")


async def main():
    print("Phase 3 Step 1 evidence capture beginning...")
    await evidence_tracker()
    await evidence_orchestrator_veto()
    await evidence_invariant_preservation()
    print("\nPhase 3 Step 1 evidence capture complete.")


if __name__ == "__main__":
    asyncio.run(main())
