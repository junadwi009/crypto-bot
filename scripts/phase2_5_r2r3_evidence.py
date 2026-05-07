"""
scripts/phase2_5_r2r3_evidence.py

Generates Phase 2.5 evidence for R2 and R3:
  - evidence_r2_orphan_lock.jsonl
      overlap event + orphaned-lock event + UNKNOWN returned +
      confirmation lock still existed afterward + invocation IDs present
  - evidence_r3_counterfactual_hash.jsonl
      identical decisions => identical hashes,
      veto mutation => different hash,
      persisted row contains hash

Run:
    SESSION_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(48))') \
    PYTHONIOENCODING=utf-8 \
    python -m scripts.phase2_5_r2r3_evidence
"""

from __future__ import annotations
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock, MagicMock

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)


def _stamp(rec: dict) -> dict:
    rec.setdefault("ts", datetime.now(timezone.utc).isoformat())
    return rec


def _write(records: list, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, default=str, sort_keys=True) + "\n")
    print(f"  -> wrote {path} ({len(records)} events)")


# ─────────────────────────────────────────────────────────────────────────
# R2 — orphan-lock telemetry evidence
# ─────────────────────────────────────────────────────────────────────────

def _build_existing_lock(age_seconds: float, token: str, invocation_id: str) -> str:
    started_at = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    return json.dumps({
        "token": token, "started_at": started_at,
        "scheduler_invocation_id": invocation_id,
    }, sort_keys=True, separators=(",", ":"))


async def evidence_r2_orphan_lock():
    print("\n[R2] Capturing orphan-lock telemetry evidence...")
    from governance import reconciliation as recon

    events = []

    # Existing orphaned lock — 1000 seconds past TTL (TTL=600)
    existing_lock_value = _build_existing_lock(
        age_seconds=recon.RECON_LOCK_TTL_SECONDS + 1000.0,
        token="ORPHAN_TOKEN_PRIOR",
        invocation_id="ORPHAN_INV_PRIOR",
    )

    # Track all redis interactions
    captured_critical = []
    delete_called = {"n": 0}
    release_called = {"n": 0}

    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(return_value=False)   # NX failed
    fake_redis.get = AsyncMock(return_value=existing_lock_value)
    async def fake_delete(*args, **kwargs):
        delete_called["n"] += 1
        return 1
    async def fake_release(*args, **kwargs):
        release_called["n"] += 1
        return True
    fake_redis.delete = AsyncMock(side_effect=fake_delete)
    fake_redis.release_lock = AsyncMock(side_effect=fake_release)

    # Capture critical log lines
    log = logging.getLogger("reconciliation")
    original_critical = log.critical

    def trace_critical(msg, *args, **kwargs):
        rendered = msg % args if args else msg
        if "SEV1 " in rendered:
            payload = json.loads(rendered.split("SEV1 ", 1)[1])
            captured_critical.append(payload)
        original_critical(msg, *args, **kwargs)

    fake_settings = MagicMock()
    fake_settings.PAPER_TRADE = True
    fake_tg = MagicMock()
    fake_tg.send = AsyncMock()

    with patch("governance.reconciliation.settings", fake_settings), \
         patch.object(recon, "redis", fake_redis), \
         patch("notifications.telegram_bot.telegram", fake_tg), \
         patch.object(log, "critical", side_effect=trace_critical):
        result = await recon.reconcile()

    events.append(_stamp({
        "stage":   "scenario",
        "setup":   "existing lock 1000s past TTL, valid JSON payload",
        "expected_events": ["reconciliation_overlap", "reconciliation_orphaned_lock"],
        "expected_status": "unknown",
        "expected_no_clear": True,
    }))

    # Record each captured SEV-1 event
    for ev in captured_critical:
        events.append(_stamp({
            "stage": f"sev1_event_{ev['event']}",
            "event": ev,
        }))

    events.append(_stamp({
        "stage":            "result",
        "returned_status":  result.value,
        "expected_status":  "unknown",
        "verdict_status":   "PASS" if result == recon.ReconciliationStatus.UNKNOWN else "FAIL",
    }))

    # Orphan-lock contract: NO auto-clear, NO lock stealing, NO forced unlock
    events.append(_stamp({
        "stage":                "no_force_clear_check",
        "delete_called_count":  delete_called["n"],
        "release_called_count": release_called["n"],
        "expected_delete":      0,
        "expected_release":     0,
        "verdict_no_clear":     "PASS" if delete_called["n"] == 0 and release_called["n"] == 0
                                else "FAIL",
    }))

    # Verify both required events were emitted
    overlap_seen = any(e["event"] == "reconciliation_overlap" for e in captured_critical)
    orphan_seen = any(e["event"] == "reconciliation_orphaned_lock" for e in captured_critical)
    events.append(_stamp({
        "stage":                "events_emitted_check",
        "overlap_event_seen":   overlap_seen,
        "orphan_event_seen":    orphan_seen,
        "verdict_events":       "PASS" if overlap_seen and orphan_seen else "FAIL",
    }))

    # Verify invocation IDs present
    overlap_event = next((e for e in captured_critical
                          if e["event"] == "reconciliation_overlap"), None)
    if overlap_event:
        events.append(_stamp({
            "stage":                       "metadata_check",
            "this_invocation_id_present":  "scheduler_invocation_id" in overlap_event
                                            and len(overlap_event["scheduler_invocation_id"]) == 32,
            "previous_invocation_id":      overlap_event.get("previous_invocation_id"),
            "previous_token":              overlap_event.get("previous_lock_token"),
            "lock_age_seconds":            overlap_event.get("lock_age_seconds"),
            "lock_ttl_seconds":            overlap_event.get("lock_ttl_seconds"),
            "lock_age_exceeds_ttl":        overlap_event.get("lock_age_seconds", 0)
                                            > overlap_event.get("lock_ttl_seconds", 0),
        }))

    _write(events, "evidence_r2_orphan_lock.jsonl")


# ─────────────────────────────────────────────────────────────────────────
# R3 — counterfactual determinism hash evidence
# ─────────────────────────────────────────────────────────────────────────

async def evidence_r3_counterfactual_hash():
    print("\n[R3] Capturing counterfactual_hash determinism evidence...")
    from governance import orchestrator as orch_module
    from governance.orchestrator import (
        Orchestrator, compute_counterfactual_hash,
    )
    from governance.reconciliation import ReconciliationStatus

    events = []
    decisions_a = []
    decisions_b = []

    async def capture_a(decision):
        decisions_a.append(decision)

    async def capture_b(decision):
        decisions_b.append(decision)

    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)

    # Scenario 1: identical inputs twice -> identical hashes
    o1 = Orchestrator()
    with patch.object(o1, "_persist_decision", new=capture_a), \
         patch.object(orch_module, "redis", fake_redis), \
         patch("governance.orchestrator.recon_last_status",
               new=AsyncMock(return_value=ReconciliationStatus.CLEAN)):
        for _ in range(2):
            await o1.evaluate(
                pair="BTC/USDT", requested_action="buy",
                pipeline_state={"stage_reached": "rule"},
                proposed_size_usd=10.0,
            )

    h1, h2 = decisions_a[0].counterfactual_hash, decisions_a[1].counterfactual_hash
    events.append(_stamp({
        "stage":            "scenario_1_identical_inputs",
        "evaluation_count": 2,
        "decision_id_1":    str(decisions_a[0].decision_id),
        "decision_id_2":    str(decisions_a[1].decision_id),
        "hash_1":           h1,
        "hash_2":           h2,
        "decision_ids_distinct": str(decisions_a[0].decision_id) != str(decisions_a[1].decision_id),
        "hashes_match":     h1 == h2,
        "verdict":          "PASS" if h1 == h2 else "FAIL",
    }))

    # Scenario 2: vetoes change between calls -> hash differs
    o2 = Orchestrator()
    decisions_b.clear()
    with patch.object(o2, "_persist_decision", new=capture_b), \
         patch.object(orch_module, "redis", fake_redis):

        with patch("governance.orchestrator.recon_last_status",
                   new=AsyncMock(return_value=ReconciliationStatus.CLEAN)):
            await o2.evaluate(pair="ETH/USDT", requested_action="buy",
                              pipeline_state={"stage_reached": "rule"},
                              proposed_size_usd=10.0)

        with patch("governance.orchestrator.recon_last_status",
                   new=AsyncMock(return_value=ReconciliationStatus.UNKNOWN)):
            await o2.evaluate(pair="ETH/USDT", requested_action="buy",
                              pipeline_state={"stage_reached": "rule"},
                              proposed_size_usd=10.0)

    hA, hB = decisions_b[0].counterfactual_hash, decisions_b[1].counterfactual_hash
    events.append(_stamp({
        "stage":              "scenario_2_veto_mutation",
        "decision_a_vetoes":  list(decisions_b[0].layer_vetoes),
        "decision_b_vetoes":  list(decisions_b[1].layer_vetoes),
        "hash_a":             hA,
        "hash_b":             hB,
        "hashes_differ":      hA != hB,
        "verdict":            "PASS" if hA != hB else "FAIL",
    }))

    # Scenario 3: hash persists on row (to_db output)
    row = decisions_a[0].to_db()
    events.append(_stamp({
        "stage":                       "scenario_3_persisted_row_contains_hash",
        "row_has_counterfactual_hash": "counterfactual_hash" in row,
        "row_hash_value":              row.get("counterfactual_hash"),
        "hash_is_sha256":              len(row.get("counterfactual_hash", "")) == 64,
        "verdict":                     "PASS" if (
            "counterfactual_hash" in row and len(row["counterfactual_hash"]) == 64
        ) else "FAIL",
    }))

    # Scenario 4: hash function is pure (vetoes-list ordering preserved)
    h_ab = compute_counterfactual_hash("hold", 0.0, "frozen", ["A", "B"])
    h_ba = compute_counterfactual_hash("hold", 0.0, "frozen", ["B", "A"])
    events.append(_stamp({
        "stage":              "scenario_4_list_ordering_preserved",
        "hash_AB":            h_ab,
        "hash_BA":            h_ba,
        "ordering_matters":   h_ab != h_ba,
        "verdict":            "PASS" if h_ab != h_ba else "FAIL",
    }))

    # Scenario 5: canonical encoding match (no whitespace, sorted keys)
    import hashlib as _h
    expected = _h.sha256(json.dumps({
        "action":"buy","mode":"normal","size_scale":1.0,"vetoes":[],
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    actual = compute_counterfactual_hash("buy", 1.0, "normal", [])
    events.append(_stamp({
        "stage":   "scenario_5_canonical_encoding",
        "expected_canonical_hash": expected,
        "actual_hash":             actual,
        "verdict":                 "PASS" if expected == actual else "FAIL",
    }))

    _write(events, "evidence_r3_counterfactual_hash.jsonl")


# ─────────────────────────────────────────────────────────────────────────

async def main():
    print("Phase 2.5 R2 + R3 evidence capture beginning...")
    await evidence_r2_orphan_lock()
    await evidence_r3_counterfactual_hash()
    print("\nR2 + R3 evidence capture complete.")


if __name__ == "__main__":
    asyncio.run(main())
