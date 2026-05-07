"""
scripts/phase2_5_r1_evidence.py
Phase 2.5 R1 — durable ACL event persistence evidence.

Captures:
  1. evidence_r1_persist_before_raise.jsonl
       — proves call order: persist → log → raise
  2. evidence_r1_persistence_failure_no_swallow.jsonl
       — proves DB-down does not suppress the violation
  3. evidence_r1_security_events_row_shape.jsonl
       — sample of the security_events row written

Run:
    SESSION_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(48))') \
    PYTHONIOENCODING=utf-8 \
    python -m scripts.phase2_5_r1_evidence
"""

from __future__ import annotations
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

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

async def evidence_persist_before_raise():
    print("\n[1/3] Capturing R1 'persist BEFORE raise' evidence...")
    from governance.redis_acl import acl, RedisACLViolation

    events = []
    call_order = []

    def trace_persist(payload):
        call_order.append({
            "step":   "persist",
            "ts":     time.time(),
            "caller": payload["caller"],
            "key":    payload["attempted_key"],
        })
        events.append(_stamp({
            "stage": "persist_invoked",
            "payload": payload,
        }))

    original_critical = logging.getLogger("redis_acl").critical

    def trace_log(msg, *args, **kwargs):
        if "SECURITY " in str(msg) and "PERSIST_FAILED" not in str(msg):
            call_order.append({"step": "log", "ts": time.time()})
        original_critical(msg, *args, **kwargs)

    with patch("governance.redis_acl._persist_security_event", new=trace_persist), \
         patch("governance.redis_acl._raw_redis"), \
         patch.object(logging.getLogger("redis_acl"), "critical", side_effect=trace_log):

        sg_client = acl.for_module("engine.signal_generator")
        events.append(_stamp({"stage": "client_acquired",
                               "caller": sg_client.caller}))
        try:
            await sg_client.set("l0:bot_paused", "1")
        except RedisACLViolation as v:
            call_order.append({"step": "raise", "ts": time.time(),
                               "type": "RedisACLViolation"})
            events.append(_stamp({
                "stage":            "violation_raised_after_persist_and_log",
                "is_baseexception": isinstance(v, BaseException),
                "is_exception":     isinstance(v, Exception),
                "caller":           v.caller,
                "attempted_key":    v.attempted_key,
            }))

    events.append(_stamp({
        "stage":      "call_order_summary",
        "order":      [step["step"] for step in call_order],
        "expected":   ["persist", "log", "raise"],
        "verdict":    "PASS" if [s["step"] for s in call_order] == ["persist", "log", "raise"]
                      else "FAIL",
    }))
    _write(events, "evidence_r1_persist_before_raise.jsonl")


# ─────────────────────────────────────────────────────────────────────────

async def evidence_persistence_failure_no_swallow():
    print("\n[2/3] Capturing R1 'DB-down does not swallow violation' evidence...")
    from governance.redis_acl import acl, RedisACLViolation

    events = []

    fake_db = MagicMock()
    fake_db._get.return_value.table.return_value.insert.return_value.execute.side_effect = (
        ConnectionError("simulated supabase outage")
    )

    bypassed_correctly = True
    raised_type = None

    with patch.dict("sys.modules", {"database.client": MagicMock(db=fake_db)}), \
         patch("governance.redis_acl._raw_redis"):
        sg_client = acl.for_module("engine.signal_generator")
        try:
            try:
                await sg_client.set("l0:bot_paused", "1")
            except Exception as e:
                bypassed_correctly = False
                raised_type = type(e).__name__
        except RedisACLViolation as v:
            raised_type = "RedisACLViolation"
            events.append(_stamp({
                "stage":              "violation_propagated_despite_db_failure",
                "is_baseexception":   isinstance(v, BaseException),
                "is_exception":       isinstance(v, Exception),
                "caller":             v.caller,
                "attempted_key":      v.attempted_key,
            }))

    events.append(_stamp({
        "stage":   "summary",
        "raised_type":         raised_type,
        "bypassed_except_exception": bypassed_correctly,
        "verdict":  "PASS" if bypassed_correctly and raised_type == "RedisACLViolation"
                   else "FAIL",
    }))
    _write(events, "evidence_r1_persistence_failure_no_swallow.jsonl")


# ─────────────────────────────────────────────────────────────────────────

async def evidence_security_events_row_shape():
    print("\n[3/3] Capturing R1 sample security_events row shape...")
    from governance.redis_acl import acl, RedisACLViolation

    captured_rows = []

    def fake_insert(payload):
        captured_rows.append(payload)
        return MagicMock(execute=MagicMock(return_value=None))

    fake_db = MagicMock()
    fake_db._get.return_value.table.return_value.insert.side_effect = fake_insert

    with patch.dict("sys.modules", {"database.client": MagicMock(db=fake_db)}), \
         patch("governance.redis_acl._raw_redis"):
        # Trigger violations from multiple callers + ops
        sg = acl.for_module("engine.signal_generator")
        recon = acl.for_module("governance.reconciliation")

        for op_target in [
            ("set", "l0:bot_paused", sg),
            ("get", "l0:supervisor_unhealthy", sg),
            ("delete", "l0:circuit_breaker_tripped", sg),
            ("set", "l0:bot_paused", recon),    # recon can't write l0 either
        ]:
            op, key, client = op_target
            try:
                if op == "set":
                    await client.set(key, "1")
                elif op == "get":
                    await client.get(key)
                elif op == "delete":
                    await client.delete(key)
            except RedisACLViolation:
                pass

        # Bootstrap-fail also persists
        try:
            acl.for_module("attacker.unknown.module")
        except RedisACLViolation:
            pass

    annotated = []
    for i, row in enumerate(captured_rows):
        annotated.append({
            "row_index":      i,
            "event_type":     row.get("event_type"),
            "severity":       row.get("severity"),
            "schema_version": row.get("schema_version"),
            "caller":         row.get("caller"),
            "event_time":     row.get("event_time"),
            "payload_keys":   sorted(row.get("payload", {}).keys()),
            "attempted_op":   row.get("payload", {}).get("attempted_op"),
            "attempted_key":  row.get("payload", {}).get("attempted_key"),
        })

    _write(annotated, "evidence_r1_security_events_row_shape.jsonl")
    print(f"  ({len(captured_rows)} rows would have been written to security_events)")


# ─────────────────────────────────────────────────────────────────────────

async def main():
    print("Phase 2.5 R1 evidence capture beginning...")
    await evidence_persist_before_raise()
    await evidence_persistence_failure_no_swallow()
    await evidence_security_events_row_shape()
    print("\nR1 evidence capture complete.")


if __name__ == "__main__":
    asyncio.run(main())
