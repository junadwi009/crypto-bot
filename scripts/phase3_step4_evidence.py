"""
scripts/phase3_step4_evidence.py
Phase 3 Step 4 (B3-prep) — Raw CB state recorded in supervisor cycle log.

Captures two artifacts:
  - evidence_p3s4_cb_state_capture.jsonl
        Per-CB-state-combination cycle_event capture covering the 5-cell
        fixture matrix (Council-approved):
            (None, None), (False, False), (True, None),
            (None, True),  (True, True)
        Verifies each combination's cb_state_l0 and cb_state_legacy in
        the cycle log match the controlled tuple, and the v1 placeholder
        string "phase3" is never emitted.

  - evidence_p3s4_invariant_preservation.jsonl
        Council-mandated checks:
          * CYCLE_LOG_SCHEMA_VERSION = 3 (monotonic int >= 3)
          * append-only HISTORY block — v1 + v2 entries verbatim, v3 added
          * byte-identical proof: across all 5 CB-state combinations the
            non-CB / non-recon decision fields produce a SINGLE canonical
            JSON
          * None-vs-False distinction preserved in the cycle log
          * no new write surface — CALLER_PREFIX_RULES unchanged for
            governance.l0_supervisor (still exactly {l0:})
          * propagation contract preserved on the new raw reads
"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

NON_TIMING_VARYING_FIELDS = {
    "loop_latency_ms", "ts",
    "cb_state_l0", "cb_state_legacy",
}


def _stamp(rec: dict) -> dict:
    rec.setdefault("ts", datetime.now(timezone.utc).isoformat())
    return rec


def _write(records: list, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, default=str, sort_keys=True) + "\n")
    print(f"  -> wrote {path} ({len(records)} events)")


async def _capture_with_cb_tuple(cb_l0, cb_legacy):
    """Drive supervise() with `_check_cb_coherence` patched to return a
    controlled `("ok", cb_l0, cb_legacy)` tuple. recon held at CLEAN to
    keep that dimension constant across all fixtures."""
    from governance import l0_supervisor
    from governance.reconciliation import ReconciliationStatus

    captured = []

    def capture_info(msg, *args, **kwargs):
        if msg == "L0_CYCLE %s" and args:
            try:
                captured.append(json.loads(args[0]))
            except Exception:
                pass

    with patch.object(l0_supervisor, "redis") as fake_redis, \
         patch.object(l0_supervisor, "_check_kernel_hash",
                      new=AsyncMock(return_value="ok")), \
         patch.object(l0_supervisor, "_check_cb_coherence",
                      new=AsyncMock(return_value=("ok", cb_l0, cb_legacy))), \
         patch.object(l0_supervisor, "recon_last_status",
                      new=AsyncMock(return_value=ReconciliationStatus.CLEAN)), \
         patch.object(l0_supervisor, "SUPERVISOR_TICK_SECONDS", 0.01), \
         patch.object(l0_supervisor.log, "info",
                      side_effect=capture_info):
        fake_redis.set = AsyncMock(return_value=True)
        fake_redis.get = AsyncMock(return_value=None)

        task = asyncio.create_task(l0_supervisor.supervise())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return captured


# ── Evidence 1: 5-cell fixture matrix ──────────────────────────────────

async def evidence_cb_state_capture():
    print("\n[1/2] Per-CB-state-combination cycle_event capture...")

    fixtures = [
        ("none_none",   None,  None),
        ("false_false", False, False),
        ("true_none",   True,  None),
        ("none_true",   None,  True),
        ("true_true",   True,  True),
    ]

    events = []
    for label, cb_l0, cb_legacy in fixtures:
        cycles = await _capture_with_cb_tuple(cb_l0, cb_legacy)
        if not cycles:
            events.append(_stamp({
                "fixture":           label,
                "expected_cb_l0":    cb_l0,
                "expected_cb_legacy": cb_legacy,
                "captured_cycles":   0,
                "verdict":           "FAIL",
                "reason":            "no L0_CYCLE log events captured",
            }))
            continue

        all_match = all(
            c.get("cb_state_l0") == cb_l0
            and c.get("cb_state_legacy") == cb_legacy
            for c in cycles
        )
        no_placeholder = all(
            c.get("reconciliation_status") != "phase3" for c in cycles
        )
        # Spot-sample first cycle for the audit trail
        sample = cycles[0]
        events.append(_stamp({
            "fixture":              label,
            "expected_cb_l0":       cb_l0,
            "expected_cb_legacy":   cb_legacy,
            "actual_cb_l0":         sample.get("cb_state_l0"),
            "actual_cb_legacy":     sample.get("cb_state_legacy"),
            "captured_cycles":      len(cycles),
            "all_match":            all_match,
            "v1_placeholder_absent": no_placeholder,
            "verdict":              "PASS" if (all_match and no_placeholder)
                                     else "FAIL",
        }))

    _write(events, "evidence_p3s4_cb_state_capture.jsonl")


# ── Evidence 2: invariant preservation ─────────────────────────────────

async def evidence_invariant_preservation():
    print("\n[2/2] Invariant-preservation checks...")
    from governance import l0_supervisor
    from governance.exceptions import LayerZeroViolation
    from governance.redis_acl import RedisACLViolation, CALLER_PREFIX_RULES

    events = []

    # 1. Schema version monotonic 2 → 3
    events.append(_stamp({
        "check":         "cycle_log_schema_version_bumped_to_three",
        "current_value": l0_supervisor.CYCLE_LOG_SCHEMA_VERSION,
        "is_int":        isinstance(l0_supervisor.CYCLE_LOG_SCHEMA_VERSION, int),
        "ge_three":      l0_supervisor.CYCLE_LOG_SCHEMA_VERSION >= 3,
        "verdict":       "PASS" if (
            isinstance(l0_supervisor.CYCLE_LOG_SCHEMA_VERSION, int)
            and l0_supervisor.CYCLE_LOG_SCHEMA_VERSION == 3
        ) else "FAIL",
    }))

    # 2. HISTORY block append-only — v1 + v2 verbatim, v3 added
    import inspect
    src = inspect.getsource(l0_supervisor)
    history_checks = {
        "v1_header_present":  "v1 (Phase 2, original):" in src,
        "v2_header_present":  "v2 (Phase 3 Step 3" in src,
        "v3_header_present":  "v3 (Phase 3 Step 4" in src,
        "v1_placeholder_described":
            "carried the literal placeholder string \"phase3\"" in src,
        "v2_live_source_described":
            "governance.reconciliation.last_status()" in src,
        "v3_describes_cb_state_l0":     "cb_state_l0" in src,
        "v3_describes_cb_state_legacy": "cb_state_legacy" in src,
        "append_only_disclaimer":
            "append-only, never edit prior entries" in src,
    }
    events.append(_stamp({
        "check":   "history_block_v1_v2_verbatim_v3_appended",
        **history_checks,
        "verdict": "PASS" if all(history_checks.values()) else "FAIL",
    }))

    # 3. Byte-identical proof: non-CB/non-recon fields stable across all
    #    5 CB-state combinations.
    fixtures = [
        (None, None), (False, False), (True, None),
        (None, True), (True, True),
    ]
    canonical_per_fixture: dict[str, str] = {}
    for cb_l0, cb_legacy in fixtures:
        cycles = await _capture_with_cb_tuple(cb_l0, cb_legacy)
        if not cycles:
            continue
        ev = cycles[0]
        stripped = {
            k: v for k, v in ev.items()
            if k not in NON_TIMING_VARYING_FIELDS
        }
        label = f"({cb_l0!r},{cb_legacy!r})"
        canonical_per_fixture[label] = json.dumps(
            stripped, sort_keys=True, separators=(",", ":"),
        )

    if len(canonical_per_fixture) < len(fixtures):
        events.append(_stamp({
            "check": "byte_identical_non_cb_non_recon_across_cb_states",
            "captured_fixtures": sorted(canonical_per_fixture.keys()),
            "verdict": "FAIL",
            "reason":  "did not capture a cycle for every CB-state fixture",
        }))
    else:
        all_identical = len(set(canonical_per_fixture.values())) == 1
        events.append(_stamp({
            "check": "byte_identical_non_cb_non_recon_across_cb_states",
            "compared_fixtures":  sorted(canonical_per_fixture.keys()),
            "excluded_fields":    sorted(NON_TIMING_VARYING_FIELDS),
            "canonical_jsons_distinct": len(set(canonical_per_fixture.values())),
            "canonical_sample":   next(iter(canonical_per_fixture.values())),
            "verdict":            "PASS" if all_identical else "FAIL",
        }))

    # 4. None-vs-False distinction preserved (Council-locked)
    cycles_none  = await _capture_with_cb_tuple(None, None)
    cycles_false = await _capture_with_cb_tuple(False, False)
    if cycles_none and cycles_false:
        a = json.dumps({k: v for k, v in cycles_none[0].items()
                        if k not in {"loop_latency_ms", "ts"}},
                       sort_keys=True)
        b = json.dumps({k: v for k, v in cycles_false[0].items()
                        if k not in {"loop_latency_ms", "ts"}},
                       sort_keys=True)
        events.append(_stamp({
            "check":          "none_vs_false_distinction_preserved_in_cycle_log",
            "are_distinct":   a != b,
            "verdict":        "PASS" if a != b else "FAIL",
        }))
    else:
        events.append(_stamp({
            "check":   "none_vs_false_distinction_preserved_in_cycle_log",
            "verdict": "FAIL",
            "reason":  "one or both fixtures captured no cycles",
        }))

    # 5. No new write surface — supervisor CALLER_PREFIX_RULES exactly {l0:}
    supervisor_prefixes = CALLER_PREFIX_RULES.get(
        "governance.l0_supervisor", frozenset()
    )
    events.append(_stamp({
        "check":             "supervisor_caller_prefix_rules_unchanged_at_step4",
        "current_prefixes":  sorted(supervisor_prefixes),
        "expected_prefixes": ["l0:"],
        "verdict":           "PASS" if supervisor_prefixes == frozenset({"l0:"})
                              else "FAIL",
    }))

    # 6. LayerZeroViolation from CB read propagates uncaught
    propagated_l0 = False
    try:
        with patch.object(l0_supervisor, "redis") as fake_l0_redis:
            fake_l0_redis.get = AsyncMock(side_effect=LayerZeroViolation(
                reason="evidence-synthetic L0 in cb read",
                source_module="evidence",
            ))
            fake_l0_redis.set = AsyncMock()
            await l0_supervisor._check_cb_coherence()
    except LayerZeroViolation as exc:
        propagated_l0 = "evidence-synthetic L0 in cb read" in exc.reason
    events.append(_stamp({
        "check":      "layer_zero_violation_from_cb_read_propagates",
        "propagated": propagated_l0,
        "verdict":    "PASS" if propagated_l0 else "FAIL",
    }))

    # 7. RedisACLViolation from CB read propagates uncaught
    propagated_acl = False
    try:
        with patch.object(l0_supervisor, "redis") as fake_l0_redis:
            fake_l0_redis.get = AsyncMock(side_effect=RedisACLViolation(
                caller="evidence.synthetic",
                attempted_op="get",
                attempted_key="l0:circuit_breaker_tripped",
                allowed_prefixes=frozenset({"l2:"}),
            ))
            fake_l0_redis.set = AsyncMock()
            await l0_supervisor._check_cb_coherence()
    except RedisACLViolation:
        propagated_acl = True
    events.append(_stamp({
        "check":      "redis_acl_violation_from_cb_read_propagates",
        "propagated": propagated_acl,
        "verdict":    "PASS" if propagated_acl else "FAIL",
    }))

    # 8. Other-exception fallback yields safe tuple ("check_error", None, None)
    with patch.object(l0_supervisor, "redis") as fake_l0_redis:
        fake_l0_redis.get = AsyncMock(side_effect=RuntimeError("redis hiccup"))
        fake_l0_redis.set = AsyncMock()
        status, cb_l0, cb_legacy = await l0_supervisor._check_cb_coherence()
    events.append(_stamp({
        "check":          "non_l0_exception_yields_safe_tuple",
        "returned_status": status,
        "returned_cb_l0":  cb_l0,
        "returned_cb_legacy": cb_legacy,
        "verdict":         "PASS" if (
            status == "check_error" and cb_l0 is None and cb_legacy is None
        ) else "FAIL",
    }))

    _write(events, "evidence_p3s4_invariant_preservation.jsonl")


async def main():
    print("Phase 3 Step 4 (B3-prep) evidence capture beginning...")
    await evidence_cb_state_capture()
    await evidence_invariant_preservation()
    print("\nPhase 3 Step 4 evidence capture complete.")


if __name__ == "__main__":
    asyncio.run(main())
