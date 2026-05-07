"""
scripts/phase3_step3_evidence.py
Phase 3 Step 3 (B2) — L0 reconciliation-status ingestion evidence.

Captures two artifacts:
  - evidence_p3s3_recon_status_ingestion.jsonl
        Per-recon-state fixture coverage: cycle_event["reconciliation_status"]
        carries the live ReconciliationStatus value across CLEAN / STALE /
        UNKNOWN / DIVERGENT, plus the safe-default fallback on non-L0 read
        failure, plus confirmation that the v1 placeholder string "phase3"
        never appears at v2.

  - evidence_p3s3_invariant_preservation.jsonl
        Council-mandated byte-identical proof: across all 4 recon states,
        non-reconciliation supervisor decision fields (kernel_hash_status,
        cb_state_consistency, supervisor_unhealthy, soft_mode_trigger_count,
        schema_version, event) are byte-identical for equivalent fixtures.
        Plus: CYCLE_LOG_SCHEMA_VERSION = 2; HISTORY block append-only
        (v1 verbatim, v2 appended); recon called BEFORE kernel_hash /
        cb_coherence in cycle order; LayerZeroViolation / RedisACLViolation
        propagate uncaught from the recon read.
"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

# Fields whose values are determined by per-call timing or by the recon
# state itself — explicitly excluded from byte-identical comparison.
TIMING_FIELDS  = {"loop_latency_ms", "ts"}
VARYING_FIELDS = {"reconciliation_status"}
NON_RECON_DECISION_FIELDS = {
    "schema_version", "event", "kernel_hash_status", "cb_state_consistency",
    "supervisor_unhealthy", "soft_mode_trigger_count",
}


def _stamp(rec: dict) -> dict:
    rec.setdefault("ts", datetime.now(timezone.utc).isoformat())
    return rec


def _write(records: list, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, default=str, sort_keys=True) + "\n")
    print(f"  -> wrote {path} ({len(records)} events)")


async def _capture_cycle_events(recon_mock, redis_get_return=None):
    """Drive supervise() through ~one tick and capture all L0_CYCLE log
    payloads. Returns the list of parsed cycle_event dicts.

    Mocks: redis (set/get), kernel_hash → "ok", cb_coherence → "ok",
           recon_last_status (caller-provided), tick = 0.01s.
    """
    from governance import l0_supervisor

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


# ── Evidence 1: per-recon-state fixture coverage ───────────────────────

async def evidence_recon_status_ingestion():
    print("\n[1/2] Per-recon-state cycle_event coverage...")
    from governance.reconciliation import ReconciliationStatus

    fixtures = [
        ("recon_clean",     ReconciliationStatus.CLEAN,     "clean"),
        ("recon_stale",     ReconciliationStatus.STALE,     "stale"),
        ("recon_unknown",   ReconciliationStatus.UNKNOWN,   "unknown"),
        ("recon_divergent", ReconciliationStatus.DIVERGENT, "divergent"),
    ]

    events = []
    for fixture_name, status_enum, expected_str in fixtures:
        cycles = await _capture_cycle_events(
            AsyncMock(return_value=status_enum),
        )
        if not cycles:
            events.append(_stamp({
                "fixture":  fixture_name,
                "expected_recon_str": expected_str,
                "captured_cycles":    0,
                "verdict":  "FAIL",
                "reason":   "no L0_CYCLE log events captured",
            }))
            continue
        actual_strs = sorted({c["reconciliation_status"] for c in cycles})
        all_match = all(c["reconciliation_status"] == expected_str
                        for c in cycles)
        # Sanity: ensure the v1 placeholder "phase3" never leaked through
        no_placeholder = all(c["reconciliation_status"] != "phase3"
                             for c in cycles)
        events.append(_stamp({
            "fixture":  fixture_name,
            "expected_recon_str":   expected_str,
            "actual_recon_strs":    actual_strs,
            "captured_cycles":      len(cycles),
            "v1_placeholder_absent": no_placeholder,
            "verdict":  "PASS" if (all_match and no_placeholder) else "FAIL",
        }))

    # Safe-default fallback on non-L0 read failure
    cycles = await _capture_cycle_events(
        AsyncMock(side_effect=RuntimeError("recon backend down")),
    )
    fallback_ok = bool(cycles) and all(
        c["reconciliation_status"] == "unknown" for c in cycles
    )
    events.append(_stamp({
        "fixture":  "recon_runtime_error_fallback",
        "expected_recon_str":  "unknown",
        "actual_recon_strs":   sorted({c["reconciliation_status"] for c in cycles}),
        "captured_cycles":     len(cycles),
        "verdict":             "PASS" if fallback_ok else "FAIL",
    }))

    _write(events, "evidence_p3s3_recon_status_ingestion.jsonl")


# ── Evidence 2: invariant preservation ─────────────────────────────────

async def evidence_invariant_preservation():
    print("\n[2/2] Invariant-preservation checks (incl. byte-identical proof)...")
    from governance import l0_supervisor
    from governance.reconciliation import ReconciliationStatus
    from governance.exceptions import LayerZeroViolation
    from governance.redis_acl import RedisACLViolation

    events = []

    # --- Council-mandated byte-identical proof -------------------------
    # Capture one cycle for each of the four recon states using identical
    # mocks for all other inputs. Strip timing-sensitive fields and the
    # known-varying reconciliation_status field. Whatever remains MUST be
    # byte-identical across all four captures.
    states = [
        ("clean",     ReconciliationStatus.CLEAN),
        ("stale",     ReconciliationStatus.STALE),
        ("unknown",   ReconciliationStatus.UNKNOWN),
        ("divergent", ReconciliationStatus.DIVERGENT),
    ]
    representative_event_per_state: dict[str, dict] = {}
    for label, status in states:
        cycles = await _capture_cycle_events(AsyncMock(return_value=status))
        if cycles:
            # Use the FIRST captured cycle for stability.
            ev = cycles[0]
            stripped = {
                k: ev[k] for k in ev
                if k not in TIMING_FIELDS and k not in VARYING_FIELDS
            }
            representative_event_per_state[label] = stripped

    if len(representative_event_per_state) < len(states):
        events.append(_stamp({
            "check": "byte_identical_non_recon_fields_across_recon_states",
            "captured_states": sorted(representative_event_per_state.keys()),
            "verdict": "FAIL",
            "reason":  "did not capture a cycle for every recon state",
        }))
    else:
        # Compare every pair — must produce identical canonical JSON.
        canonical = {
            label: json.dumps(ev, sort_keys=True, separators=(",", ":"))
            for label, ev in representative_event_per_state.items()
        }
        all_identical = len(set(canonical.values())) == 1
        events.append(_stamp({
            "check": "byte_identical_non_recon_fields_across_recon_states",
            "compared_states":  sorted(canonical.keys()),
            "compared_fields":  sorted(NON_RECON_DECISION_FIELDS),
            "excluded_fields":  sorted(TIMING_FIELDS | VARYING_FIELDS),
            "canonical_jsons_distinct": len(set(canonical.values())),
            "canonical_sample": next(iter(canonical.values()))
                                if canonical else None,
            "verdict":          "PASS" if all_identical else "FAIL",
        }))

    # CYCLE_LOG_SCHEMA_VERSION monotonic bump 1 → 2
    events.append(_stamp({
        "check":         "cycle_log_schema_version_bumped_to_two",
        "current_value": l0_supervisor.CYCLE_LOG_SCHEMA_VERSION,
        "is_int":        isinstance(l0_supervisor.CYCLE_LOG_SCHEMA_VERSION, int),
        "ge_two":        l0_supervisor.CYCLE_LOG_SCHEMA_VERSION >= 2,
        "verdict":       "PASS" if (
            isinstance(l0_supervisor.CYCLE_LOG_SCHEMA_VERSION, int)
            and l0_supervisor.CYCLE_LOG_SCHEMA_VERSION == 2
        ) else "FAIL",
    }))

    # HISTORY block: v1 entry verbatim AND v2 entry present
    import inspect
    src = inspect.getsource(l0_supervisor)
    history_checks = {
        "v1_header_present":       "v1 (Phase 2, original):" in src,
        "v2_header_present":       "v2 (Phase 3 Step 3" in src,
        "v1_placeholder_described":
            "carried the literal placeholder string \"phase3\"" in src,
        "v2_live_source_described":
            "governance.reconciliation.last_status()" in src,
        "append_only_disclaimer":
            "append-only, never edit prior entries" in src,
    }
    events.append(_stamp({
        "check":   "history_block_v1_verbatim_v2_appended",
        **history_checks,
        "verdict": "PASS" if all(history_checks.values()) else "FAIL",
    }))

    # v1 placeholder string "phase3" must NOT appear in any cycle_event
    # at v2. Sample the four recon states again for the audit trail.
    placeholder_seen = False
    for label, status in states:
        cycles = await _capture_cycle_events(AsyncMock(return_value=status))
        for c in cycles:
            if c.get("reconciliation_status") == "phase3":
                placeholder_seen = True
                break
        if placeholder_seen:
            break
    events.append(_stamp({
        "check":              "v1_placeholder_phase3_string_never_emitted_at_v2",
        "placeholder_seen":   placeholder_seen,
        "verdict":            "PASS" if not placeholder_seen else "FAIL",
    }))

    # Recon called BEFORE kernel_hash / cb_coherence in the cycle
    call_order: list[str] = []

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
    correct_order = (
        len(call_order) >= 3
        and call_order[0] == "recon"
        and call_order[1] == "kernel_hash"
        and call_order[2] == "cb_coherence"
    )
    events.append(_stamp({
        "check":           "recon_called_before_kernel_hash_and_cb_coherence",
        "call_order":      call_order[:6],   # first six to keep it short
        "verdict":         "PASS" if correct_order else "FAIL",
    }))

    # LayerZeroViolation propagates from the recon read
    propagated_l0 = False
    try:
        with patch.object(l0_supervisor, "redis") as fake_redis, \
             patch.object(l0_supervisor, "_check_kernel_hash",
                          new=AsyncMock(return_value="ok")), \
             patch.object(l0_supervisor, "_check_cb_coherence",
                          new=AsyncMock(return_value="ok")), \
             patch.object(l0_supervisor, "recon_last_status",
                          new=AsyncMock(side_effect=LayerZeroViolation(
                              reason="evidence-synthetic recon L0",
                              source_module="evidence",
                          ))), \
             patch.object(l0_supervisor, "SUPERVISOR_TICK_SECONDS", 0.01):
            fake_redis.set = AsyncMock(return_value=True)
            fake_redis.get = AsyncMock(return_value=None)
            await l0_supervisor.supervise()
    except LayerZeroViolation as exc:
        propagated_l0 = "evidence-synthetic recon L0" in exc.reason
    events.append(_stamp({
        "check":      "layer_zero_violation_from_recon_propagates",
        "propagated": propagated_l0,
        "verdict":    "PASS" if propagated_l0 else "FAIL",
    }))

    # RedisACLViolation propagates from the recon read
    propagated_acl = False
    try:
        with patch.object(l0_supervisor, "redis") as fake_redis, \
             patch.object(l0_supervisor, "_check_kernel_hash",
                          new=AsyncMock(return_value="ok")), \
             patch.object(l0_supervisor, "_check_cb_coherence",
                          new=AsyncMock(return_value="ok")), \
             patch.object(l0_supervisor, "recon_last_status",
                          new=AsyncMock(side_effect=RedisACLViolation(
                              caller="evidence.synthetic",
                              attempted_op="get",
                              attempted_key="l1:recon:last",
                              allowed_prefixes=frozenset({"l2:"}),
                          ))), \
             patch.object(l0_supervisor, "SUPERVISOR_TICK_SECONDS", 0.01):
            fake_redis.set = AsyncMock(return_value=True)
            fake_redis.get = AsyncMock(return_value=None)
            await l0_supervisor.supervise()
    except RedisACLViolation:
        propagated_acl = True
    events.append(_stamp({
        "check":      "redis_acl_violation_from_recon_propagates",
        "propagated": propagated_acl,
        "verdict":    "PASS" if propagated_acl else "FAIL",
    }))

    _write(events, "evidence_p3s3_invariant_preservation.jsonl")


async def main():
    print("Phase 3 Step 3 (B2) evidence capture beginning...")
    await evidence_recon_status_ingestion()
    await evidence_invariant_preservation()
    print("\nPhase 3 Step 3 evidence capture complete.")


if __name__ == "__main__":
    asyncio.run(main())
