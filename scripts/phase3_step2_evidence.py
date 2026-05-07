"""
scripts/phase3_step2_evidence.py
Phase 3 Step 2 — Purely-derived cooldown predicate evidence.

Captures three artifacts:
  - evidence_p3s2_cooldown.jsonl              cooldown function output for
                                              fixture trade-history shapes
  - evidence_p3s2_orchestrator_veto.jsonl     orchestrator decision rows
                                              showing cooldown veto in
                                              observe-only
  - evidence_p3s2_invariant_preservation.jsonl
                                              R3 hash unchanged for clean
                                              inputs; v3 ⊃ v2 ⊃ v1; HISTORY
                                              block append-only; cooldown
                                              path makes no Redis writes
"""
from __future__ import annotations
import asyncio
import json
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


def _trade_at(pair: str, pnl: float, closed_minutes_ago: int) -> dict:
    return {
        "pair":      pair,
        "status":    "closed",
        "pnl_usd":   pnl,
        "closed_at": (datetime.now(timezone.utc)
                      - timedelta(minutes=closed_minutes_ago)).isoformat(),
    }


# ── Evidence 1: cooldown function fixture coverage ─────────────────────

async def evidence_cooldown():
    print("\n[1/3] Cooldown predicate fixture coverage...")
    from governance import consecutive_losses as cl

    fixtures = [
        ("empty_history",
         [],
         "BTC/USDT", False),
        ("streak_below_threshold_30min_old",
         [_trade_at("BTC/USDT", -1, 30), _trade_at("BTC/USDT", -1, 20)],
         "BTC/USDT", False),
        ("threshold_run_within_60min",
         [_trade_at("BTC/USDT", -1, 50), _trade_at("BTC/USDT", -1, 40),
          _trade_at("BTC/USDT", -1, 30)],
         "BTC/USDT", True),
        ("threshold_run_older_than_60min",
         [_trade_at("BTC/USDT", -1, 110), _trade_at("BTC/USDT", -1, 100),
          _trade_at("BTC/USDT", -1, 90)],
         "BTC/USDT", False),
        ("at_60_minute_boundary_inside",
         [_trade_at("BTC/USDT", -1, 59), _trade_at("BTC/USDT", -1, 58),
          _trade_at("BTC/USDT", -1, 57)],
         "BTC/USDT", True),
        ("persists_across_winning_trades_within_window",
         [_trade_at("BTC/USDT", -1, 50), _trade_at("BTC/USDT", -1, 40),
          _trade_at("BTC/USDT", -1, 30), _trade_at("BTC/USDT", 2, 20),
          _trade_at("BTC/USDT", 1, 10)],
         "BTC/USDT", True),
        ("only_recent_run_below_threshold",
         [_trade_at("BTC/USDT", -1, 200), _trade_at("BTC/USDT", -1, 180),
          _trade_at("BTC/USDT", -1, 170), _trade_at("BTC/USDT", 2, 100),
          _trade_at("BTC/USDT", -1, 20),  _trade_at("BTC/USDT", -1, 10)],
         "BTC/USDT", False),
        ("most_recent_qualifying_run_drives_cooldown",
         [_trade_at("BTC/USDT", -1, 300), _trade_at("BTC/USDT", -1, 280),
          _trade_at("BTC/USDT", -1, 260), _trade_at("BTC/USDT", 2, 200),
          _trade_at("BTC/USDT", -1, 40),  _trade_at("BTC/USDT", -1, 30),
          _trade_at("BTC/USDT", -1, 20)],
         "BTC/USDT", True),
        ("filtered_by_pair_other_pair_in_cooldown",
         [_trade_at("ETH/USDT", -1, 50), _trade_at("ETH/USDT", -1, 40),
          _trade_at("ETH/USDT", -1, 30)],
         "BTC/USDT", False),
        ("breakeven_resets_streak",
         [_trade_at("BTC/USDT", -1, 50), _trade_at("BTC/USDT", 0, 40),
          _trade_at("BTC/USDT", -1, 30), _trade_at("BTC/USDT", -1, 20)],
         "BTC/USDT", False),
    ]

    events = []
    for name, trades, pair, expected in fixtures:
        with patch.object(cl.db, "get_trades_for_period",
                          new=AsyncMock(return_value=trades)):
            actual = await cl.is_in_cooldown(pair)
        events.append(_stamp({
            "fixture":   name,
            "pair":      pair,
            "expected":  expected,
            "actual":    actual,
            "verdict":   "PASS" if actual == expected else "FAIL",
        }))

    # Safe-default behaviour on DB error
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(side_effect=RuntimeError("db down"))):
        actual = await cl.is_in_cooldown("BTC/USDT")
    events.append(_stamp({
        "fixture":  "db_failure_safe_default",
        "pair":     "BTC/USDT",
        "expected": False,
        "actual":   actual,
        "verdict":  "PASS" if actual is False else "FAIL",
    }))

    _write(events, "evidence_p3s2_cooldown.jsonl")


# ── Evidence 2: orchestrator integration scenarios ─────────────────────

async def evidence_orchestrator_veto():
    print("\n[2/3] Orchestrator integration with cooldown predicate...")
    from governance import orchestrator as orch_module
    from governance.orchestrator import (
        Orchestrator, LAYER_INPUTS_SCHEMA_VERSION,
    )
    from governance.reconciliation import ReconciliationStatus

    captured_rows = []

    async def capture(decision):
        captured_rows.append(decision.to_db())

    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)

    o = Orchestrator()

    # Scenarios mirror Step 1's six but pivot on cooldown rather than streak.
    scenarios = [
        ("A_clean_no_streak_no_cooldown",
         ReconciliationStatus.CLEAN, 0, False, "buy", 10.0),
        ("B_clean_streak_2_no_cooldown",
         ReconciliationStatus.CLEAN, 2, False, "buy", 10.0),
        ("C_clean_no_streak_cooldown_active",
         ReconciliationStatus.CLEAN, 0, True,  "buy", 10.0),
        ("D_clean_streak_3_and_cooldown_both_fire",
         ReconciliationStatus.CLEAN, 3, True,  "buy", 10.0),
        ("E_recon_unknown_with_cooldown_frozen_dominates",
         ReconciliationStatus.UNKNOWN, 0, True, "buy", 10.0),
        ("F_hold_request_cooldown_active_no_cooldown_veto",
         ReconciliationStatus.CLEAN, 0, True,  "hold", 0.0),
        ("G_recon_stale_streak_3_cooldown_all_three",
         ReconciliationStatus.STALE, 3, True,  "buy", 10.0),
    ]

    with patch.object(o, "_persist_decision", new=capture), \
         patch.object(orch_module, "redis", fake_redis):
        for name, recon_status, cl_count, cooldown, action, size in scenarios:
            with patch("governance.orchestrator.recon_last_status",
                       new=AsyncMock(return_value=recon_status)), \
                 patch("governance.consecutive_losses.get_consecutive_loss_count",
                       new=AsyncMock(return_value=cl_count)), \
                 patch("governance.consecutive_losses.is_in_cooldown",
                       new=AsyncMock(return_value=cooldown)):
                pre = len(captured_rows)
                await o.evaluate(
                    pair="BTC/USDT", requested_action=action,
                    pipeline_state={"stage_reached": "rule"},
                    proposed_size_usd=size,
                )
                row = captured_rows[pre]
                row["scenario"]            = name
                row["expected_recon"]      = recon_status.value
                row["expected_cl_count"]   = cl_count
                row["expected_cooldown"]   = cooldown

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
            "consecutive_losses_cooldown_active":
                row["layer_inputs"].get("consecutive_losses_cooldown_active"),
            "reconciliation_raw":    row["layer_inputs"].get("reconciliation_raw"),
            "schema_version":        row["layer_inputs"].get("schema_version"),
            "counterfactual_hash":   row["counterfactual_hash"],
            "observe_only_passthrough": row["observe_only_passthrough"],
        })
    _write(annotated, "evidence_p3s2_orchestrator_veto.jsonl")


# ── Evidence 3: invariant preservation ─────────────────────────────────

async def evidence_invariant_preservation():
    print("\n[3/3] Invariant-preservation checks...")
    from governance.orchestrator import (
        compute_counterfactual_hash, LAYER_INPUTS_SCHEMA_VERSION,
        ORCHESTRATOR_VERSION,
    )
    from governance import orchestrator as orch_module
    from governance.orchestrator import Orchestrator
    from governance.reconciliation import ReconciliationStatus
    from governance import consecutive_losses as cl

    events = []

    # R3 hash for clean-recon, no-veto inputs must equal Phase-2/Step-1 evidence.
    h_clean = compute_counterfactual_hash("buy", 1.0, "normal", [])
    events.append(_stamp({
        "check": "r3_hash_unchanged_for_no_veto_clean_inputs",
        "computed_hash": h_clean,
        "phase2_hash":   "0ea24c8dabcac50e22a7ac7635bcf4ef454aaa3dee6da79a7f8236475091d7e7",
        "verdict": "PASS" if h_clean ==
            "0ea24c8dabcac50e22a7ac7635bcf4ef454aaa3dee6da79a7f8236475091d7e7"
            else "FAIL",
    }))

    # R3 hash differs when cooldown veto fires
    h_no = compute_counterfactual_hash("buy", 1.0, "normal", [])
    h_cooldown = compute_counterfactual_hash(
        "buy", 0.5, "restricted", ["consecutive_losses_cooldown_on_BTC/USDT"],
    )
    events.append(_stamp({
        "check":             "r3_hash_differs_when_cooldown_veto_fires",
        "hash_no_veto":      h_no,
        "hash_with_cooldown": h_cooldown,
        "verdict":           "PASS" if h_no != h_cooldown else "FAIL",
    }))

    # Cooldown veto string distinct from streak veto string
    h_streak = compute_counterfactual_hash(
        "buy", 0.5, "restricted", ["consecutive_losses_3_on_BTC/USDT"],
    )
    events.append(_stamp({
        "check":             "r3_cooldown_string_distinct_from_streak_string",
        "hash_streak_only":  h_streak,
        "hash_cooldown_only": h_cooldown,
        "verdict":           "PASS" if h_streak != h_cooldown else "FAIL",
    }))

    # Schema v3 contains v1 required keys (constraint 6)
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
    required_v1_keys = {
        "schema_version", "kernel_hash", "kernel_version", "orchestrator_version",
        "governance_mode", "reconciliation_raw", "reconciliation_implication",
    }
    missing = required_v1_keys - set(snapshot.keys())
    events.append(_stamp({
        "check":             "v3_is_strict_superset_of_v1_required_keys",
        "schema_version":    snapshot["schema_version"],
        "missing_v1_keys":   sorted(missing),
        "v3_added_keys":     sorted(set(snapshot.keys()) - required_v1_keys),
        "verdict":           "PASS" if not missing and snapshot["schema_version"] >= 3
                             else "FAIL",
    }))

    # v2 keys still present in v3 (Step 1 → Step 2 strict superset)
    v2_added = {"consecutive_losses"}
    missing_v2 = v2_added - set(snapshot.keys())
    events.append(_stamp({
        "check":             "v3_is_strict_superset_of_v2_added_keys",
        "missing_v2_keys":   sorted(missing_v2),
        "verdict":           "PASS" if not missing_v2 else "FAIL",
    }))

    # v3 introduces consecutive_losses_cooldown_active (boolean)
    events.append(_stamp({
        "check":             "v3_includes_cooldown_active_boolean",
        "key_present":       "consecutive_losses_cooldown_active" in snapshot,
        "value_type":        type(
            snapshot.get("consecutive_losses_cooldown_active")
        ).__name__,
        "verdict":           "PASS" if (
            "consecutive_losses_cooldown_active" in snapshot
            and isinstance(
                snapshot.get("consecutive_losses_cooldown_active"), bool,
            )
        ) else "FAIL",
    }))

    # Constraint 13: schema_version is monotonic integer >= 3
    events.append(_stamp({
        "check":         "schema_version_monotonic_int_at_three",
        "current_value": LAYER_INPUTS_SCHEMA_VERSION,
        "is_int":        isinstance(LAYER_INPUTS_SCHEMA_VERSION, int),
        "ge_three":      LAYER_INPUTS_SCHEMA_VERSION >= 3,
        "verdict":       "PASS" if isinstance(LAYER_INPUTS_SCHEMA_VERSION, int)
                          and LAYER_INPUTS_SCHEMA_VERSION >= 3 else "FAIL",
    }))

    # ORCHESTRATOR_VERSION bumped past Step 1 marker
    events.append(_stamp({
        "check":              "orchestrator_version_bumped_past_step1",
        "current":            ORCHESTRATOR_VERSION,
        "step1_value":        "0.3.0-observe-with-cl",
        "differs":            ORCHESTRATOR_VERSION != "0.3.0-observe-with-cl",
        "carries_cooldown_marker": "cooldown" in ORCHESTRATOR_VERSION.lower(),
        "verdict":            "PASS" if (
            ORCHESTRATOR_VERSION != "0.3.0-observe-with-cl"
            and "cooldown" in ORCHESTRATOR_VERSION.lower()
        ) else "FAIL",
    }))

    # HISTORY block: v1, v2, v3 entries all present (append-only)
    import inspect
    src = inspect.getsource(orch_module)
    events.append(_stamp({
        "check":         "history_block_v1_v2_v3_all_present",
        "v1_present":    "v1 (Phase 2" in src,
        "v2_present":    "v2 (Phase 3 Step 1" in src,
        "v3_present":    "v3 (Phase 3 Step 2" in src,
        "v1_keys_verbatim_present":
            "schema_version, kernel_hash, kernel_version, orchestrator_version" in src,
        "v2_addition_verbatim_present":
            "consecutive_losses (int)" in src,
        "verdict": "PASS" if all([
            "v1 (Phase 2" in src,
            "v2 (Phase 3 Step 1" in src,
            "v3 (Phase 3 Step 2" in src,
            "schema_version, kernel_hash, kernel_version, orchestrator_version" in src,
            "consecutive_losses (int)" in src,
        ]) else "FAIL",
    }))

    # Architecture invariant: cooldown function makes no Redis calls
    fake_redis2 = MagicMock()
    fake_redis2.get  = AsyncMock(return_value=None)
    fake_redis2.set  = AsyncMock()
    fake_redis2.setex = AsyncMock()
    fake_redis2.expire = AsyncMock()
    fake_redis2.delete = AsyncMock()
    trades = [
        _trade_at("BTC/USDT", -1, 30),
        _trade_at("BTC/USDT", -1, 20),
        _trade_at("BTC/USDT", -1, 10),
    ]
    with patch.object(cl.db, "get_trades_for_period",
                      new=AsyncMock(return_value=trades)), \
         patch.object(orch_module, "redis", fake_redis2):
        await cl.is_in_cooldown("BTC/USDT")
    no_writes = (
        not fake_redis2.set.called
        and not fake_redis2.setex.called
        and not fake_redis2.expire.called
        and not fake_redis2.delete.called
    )
    events.append(_stamp({
        "check":               "cooldown_path_makes_no_redis_writes",
        "set_called":          bool(fake_redis2.set.called),
        "setex_called":        bool(fake_redis2.setex.called),
        "expire_called":       bool(fake_redis2.expire.called),
        "delete_called":       bool(fake_redis2.delete.called),
        "verdict":             "PASS" if no_writes else "FAIL",
    }))

    # Council parameter: cooldown duration is 60 minutes (fixed)
    events.append(_stamp({
        "check":         "cooldown_duration_is_60_minutes",
        "current_value": cl.COOLDOWN_DURATION_MINUTES,
        "verdict":       "PASS" if cl.COOLDOWN_DURATION_MINUTES == 60 else "FAIL",
    }))

    _write(events, "evidence_p3s2_invariant_preservation.jsonl")


async def main():
    print("Phase 3 Step 2 evidence capture beginning...")
    await evidence_cooldown()
    await evidence_orchestrator_veto()
    await evidence_invariant_preservation()
    print("\nPhase 3 Step 2 evidence capture complete.")


if __name__ == "__main__":
    asyncio.run(main())
