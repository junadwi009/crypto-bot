"""
governance/reconciliation.py
Daily verification that DB state matches Bybit reality.

Status semantics (Council mandate, locked):
  CLEAN     — bytes-for-bytes match between DB open trades and Bybit positions
  DIVERGENT — verification ran successfully and found a mismatch
  STALE     — last successful run > 25h ago
  UNKNOWN   — verification integrity itself untrusted (timeout, malformed
              payload, missing data, scheduler issue, lock overlap)

Governance collapse (Council mandate, asserted at module load):
  CLEAN     -> normal
  STALE     -> restricted        (last comparison succeeded but aging)
  DIVERGENT -> frozen
  UNKNOWN   -> frozen            (strictly worse than stale)

Single-flight execution (Council mandate, locked):
  Acquired via Redis SET NX EX with a UUID token.
  Released via release_lock(lock_key, lock_value) — Lua-guarded; only the
  acquiring invocation can release. Overlap = SEV-1 + UNKNOWN, no work
  proceeds.

Verifier-must-fail-suspicious (Council mandate, locked):
  status = UNKNOWN at function top. Only explicit branches upgrade.
  No fall-through to CLEAN.

Phase 2.5 R2 — Orphan-lock telemetry:
  Every invocation generates a scheduler_invocation_id. Lock value is
  structured JSON {token, started_at, scheduler_invocation_id} — not
  raw token. On overlap:
    - parse existing payload; malformed -> emit invalid event + UNKNOWN
    - compute lock_age_seconds from persisted started_at (NOT process start)
    - if lock_age > TTL -> emit orphaned-lock event (in addition to overlap)
    - emit overlap event with full metadata
  No auto-clear, no lock stealing, no forced unlock — human review only.
  Release semantics unchanged: Lua compare-and-delete with stored value.
"""

from __future__ import annotations
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from enum import Enum

from config.settings import settings
from database.client import db
from governance.exceptions import LayerZeroViolation
from governance.redis_acl import acl, RedisACLViolation

log = logging.getLogger("reconciliation")

# ACL-bound client at import (boot-fails if module missing from registry)
redis = acl.for_module(__name__)

# Schema version for R2 telemetry events. Monotonic.
RECON_EVENT_SCHEMA_VERSION = 1


# ── Status enum and governance mapping ────────────────────────────────────

class ReconciliationStatus(str, Enum):
    CLEAN     = "clean"
    DIVERGENT = "divergent"
    STALE     = "stale"
    UNKNOWN   = "unknown"


# Locked governance collapse table. Module-level asserts enforce the
# invariant that UNKNOWN and DIVERGENT can never collapse to "normal"
# — protecting against accidental softening during operational pressure.
RECON_TO_GOVERNANCE: dict[ReconciliationStatus, str] = {
    ReconciliationStatus.CLEAN:     "normal",
    ReconciliationStatus.DIVERGENT: "frozen",
    ReconciliationStatus.STALE:     "restricted",
    ReconciliationStatus.UNKNOWN:   "frozen",   # strictly worse than stale
}

assert RECON_TO_GOVERNANCE[ReconciliationStatus.UNKNOWN] != "normal", \
    "L0: UNKNOWN must not collapse to 'normal' — would soften governance under uncertainty"
assert RECON_TO_GOVERNANCE[ReconciliationStatus.DIVERGENT] != "normal", \
    "L0: DIVERGENT must not collapse to 'normal'"
assert RECON_TO_GOVERNANCE[ReconciliationStatus.STALE] != "normal", \
    "L0: STALE must not collapse to 'normal' — verification is aging"


# ── Single-flight lock contract ───────────────────────────────────────────

RECON_LOCK_KEY = "l1:reconciliation_active"
RECON_LOCK_TTL_SECONDS = 600     # 10 minutes — generous; longer than any legit run
RECON_STATUS_KEY = "l1:reconciliation_status"
RECON_LAST_RUN_KEY = "l1:reconciliation_last_run_at"
RECON_STALE_THRESHOLD_SECONDS = 25 * 3600


async def last_status() -> ReconciliationStatus:
    """Read current status. Returns UNKNOWN if no run has happened yet OR
    if last successful run is older than the stale threshold."""
    try:
        raw = await redis.get(RECON_STATUS_KEY)
    except RedisACLViolation:
        raise
    except LayerZeroViolation:
        raise
    except Exception:
        return ReconciliationStatus.UNKNOWN

    if not raw:
        return ReconciliationStatus.UNKNOWN

    try:
        status = ReconciliationStatus(raw if isinstance(raw, str) else raw.decode())
    except (ValueError, AttributeError):
        return ReconciliationStatus.UNKNOWN

    # Promote CLEAN to STALE if last successful run is too old
    if status == ReconciliationStatus.CLEAN:
        try:
            last_run_raw = await redis.get(RECON_LAST_RUN_KEY)
            if last_run_raw:
                last_run = float(last_run_raw if isinstance(last_run_raw, str)
                                 else last_run_raw.decode())
                if (time.time() - last_run) > RECON_STALE_THRESHOLD_SECONDS:
                    return ReconciliationStatus.STALE
        except (TypeError, ValueError, AttributeError):
            return ReconciliationStatus.UNKNOWN
    return status


# ── R2: Lock payload helpers ──────────────────────────────────────────────

def _build_lock_value(token: str, started_at_iso: str, invocation_id: str) -> str:
    """Build the structured JSON lock value. Canonical encoding so the
    SAME object always serializes to the SAME bytes — required by the
    Lua compare-and-delete release path which uses string equality."""
    return json.dumps(
        {"token": token, "started_at": started_at_iso,
         "scheduler_invocation_id": invocation_id},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )


def _parse_lock_payload(raw) -> dict | None:
    """Parse a stored lock value. Returns None if malformed.

    R2 contract: malformed payload is treated as untrusted state — caller
    must NOT auto-clear, and must emit a `reconciliation_lock_payload_invalid`
    event. We accept str or bytes input.
    """
    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except Exception:
            return None
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    # Required fields
    for key in ("token", "started_at", "scheduler_invocation_id"):
        if key not in parsed or not isinstance(parsed[key], str):
            return None
    return parsed


def _compute_lock_age_seconds(started_at_iso: str) -> float | None:
    """Compute age of a held lock from its persisted timestamp.

    R2 contract: age MUST derive from persisted started_at, NOT from local
    process start. Returns None if the timestamp is unparseable.
    """
    try:
        started = datetime.fromisoformat(started_at_iso)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0.0, (now - started).total_seconds())
    except Exception:
        return None


# ── Main reconciliation entry ─────────────────────────────────────────────

async def reconcile() -> ReconciliationStatus:
    """Run a reconciliation cycle.

    Verifier-must-fail-suspicious: status starts as UNKNOWN, only explicit
    branches upgrade.

    Single-flight: SET NX EX, refuse if already held.
    Owner-validated release via release_lock with the lock VALUE (the
    full JSON payload we wrote at acquire — Lua compares stored value
    against this exactly).

    R2: every invocation has a scheduler_invocation_id; lock value is
    structured JSON; overlap parses existing payload; orphaned locks
    (age > TTL) emit a second SEV-1 event and return UNKNOWN without
    auto-clearing.
    """
    status = ReconciliationStatus.UNKNOWN   # terminal default

    # R2: scheduler_invocation_id — distinguishes scheduler invocations
    # from each other. Logged on every event of this invocation.
    scheduler_invocation_id = uuid.uuid4().hex
    lock_token = uuid.uuid4().hex
    started_at_iso = datetime.now(timezone.utc).isoformat()
    lock_value = _build_lock_value(lock_token, started_at_iso, scheduler_invocation_id)

    # --- Acquire single-flight lock ---
    try:
        acquired = await redis.set(
            RECON_LOCK_KEY, lock_value, nx=True, ex=RECON_LOCK_TTL_SECONDS,
        )
    except RedisACLViolation:
        raise
    except LayerZeroViolation:
        raise
    except Exception as e:
        log.error("reconciliation: cannot acquire lock: %s", e)
        await _persist_status(ReconciliationStatus.UNKNOWN, reason="lock_acquire_error")
        return ReconciliationStatus.UNKNOWN

    if not acquired:
        # Another invocation is still running — or its lock is orphaned.
        # R2: parse the existing lock payload; on malformed, do NOT clear.
        try:
            existing_raw = await redis.get(RECON_LOCK_KEY)
        except RedisACLViolation:
            raise
        except LayerZeroViolation:
            raise
        except Exception:
            existing_raw = None

        existing = _parse_lock_payload(existing_raw)

        if existing is None:
            # Malformed payload — emit invalid event, return UNKNOWN, DO NOT clear
            log.critical("SEV1 %s", json.dumps({
                "event":                   "reconciliation_lock_payload_invalid",
                "schema_version":          RECON_EVENT_SCHEMA_VERSION,
                "scheduler_invocation_id": scheduler_invocation_id,
                "this_invocation_token":   lock_token,
                "raw_value_truncated":     str(existing_raw)[:200] if existing_raw else None,
                "ts":                      time.time(),
            }, sort_keys=True))
            try:
                from notifications.telegram_bot import telegram
                await telegram.send(
                    f"SEV-1 reconciliation lock payload INVALID\n"
                    f"invocation_id: {scheduler_invocation_id}\n"
                    f"raw_truncated: {str(existing_raw)[:80]}\n"
                    f"NOT auto-cleared. Human review required."
                )
            except RedisACLViolation:
                raise
            except LayerZeroViolation:
                raise
            except Exception:
                pass
            await _persist_status(ReconciliationStatus.UNKNOWN,
                                  reason="lock_payload_invalid")
            return ReconciliationStatus.UNKNOWN

        # Compute lock age from persisted started_at
        lock_age = _compute_lock_age_seconds(existing.get("started_at", ""))
        lock_age_safe = lock_age if lock_age is not None else -1.0

        # --- Always emit overlap event with full R2 metadata ---
        log.critical("SEV1 %s", json.dumps({
            "event":                   "reconciliation_overlap",
            "schema_version":          RECON_EVENT_SCHEMA_VERSION,
            "scheduler_invocation_id": scheduler_invocation_id,
            "previous_lock_token":     existing.get("token"),
            "previous_invocation_id":  existing.get("scheduler_invocation_id"),
            "this_invocation_token":   lock_token,
            "lock_age_seconds":        lock_age_safe,
            "lock_ttl_seconds":        RECON_LOCK_TTL_SECONDS,
            "ts":                      time.time(),
        }, sort_keys=True))

        # --- If orphaned, emit SECOND critical event before returning ---
        # No auto-clear, no lock stealing, no forced unlock — human review only.
        if lock_age is not None and lock_age > RECON_LOCK_TTL_SECONDS:
            log.critical("SEV1 %s", json.dumps({
                "event":                   "reconciliation_orphaned_lock",
                "schema_version":          RECON_EVENT_SCHEMA_VERSION,
                "scheduler_invocation_id": scheduler_invocation_id,
                "orphaned_lock_token":     existing.get("token"),
                "orphaned_invocation_id":  existing.get("scheduler_invocation_id"),
                "lock_age_seconds":        lock_age,
                "lock_ttl_seconds":        RECON_LOCK_TTL_SECONDS,
                "ts":                      time.time(),
            }, sort_keys=True))

        # SEV-1 telegram (best effort)
        try:
            from notifications.telegram_bot import telegram
            orph_note = " (ORPHANED)" if (lock_age is not None
                                            and lock_age > RECON_LOCK_TTL_SECONDS) \
                                       else ""
            await telegram.send(
                f"SEV-1 reconciliation overlap{orph_note}\n"
                f"invocation_id: {scheduler_invocation_id}\n"
                f"prior_token: {existing.get('token')}\n"
                f"lock_age: {lock_age_safe:.1f}s / TTL {RECON_LOCK_TTL_SECONDS}s\n"
                f"NOT auto-cleared. Human review required."
            )
        except RedisACLViolation:
            raise
        except LayerZeroViolation:
            raise
        except Exception:
            pass

        await _persist_status(ReconciliationStatus.UNKNOWN, reason="overlap_with_prior_run")
        return ReconciliationStatus.UNKNOWN

    # --- Body, with owner-validated release in finally ---
    log.info("L1_RECON_CYCLE %s", json.dumps({
        "event":                   "reconciliation_cycle_started",
        "schema_version":          RECON_EVENT_SCHEMA_VERSION,
        "scheduler_invocation_id": scheduler_invocation_id,
        "lock_token":              lock_token,
        "ts":                      time.time(),
    }, sort_keys=True))
    try:
        status = await _do_reconcile_body()
    except RedisACLViolation:
        await redis.release_lock(RECON_LOCK_KEY, lock_value)
        raise
    except LayerZeroViolation:
        await redis.release_lock(RECON_LOCK_KEY, lock_value)
        raise
    except asyncio.TimeoutError:
        log.error("reconciliation: timeout during body")
        status = ReconciliationStatus.UNKNOWN
    except Exception as e:
        log.error("reconciliation: body raised: %s", e, exc_info=True)
        status = ReconciliationStatus.UNKNOWN
    finally:
        try:
            # Pass the FULL lock_value as the comparison token.
            # The ACL Lua script compares stored value to this argument
            # via string equality; since we wrote the canonical JSON, it
            # matches. Only this invocation can release.
            await redis.release_lock(RECON_LOCK_KEY, lock_value)
        except RedisACLViolation:
            raise
        except LayerZeroViolation:
            raise
        except Exception as e:
            log.error("reconciliation: lock release failed: %s", e)

    await _persist_status(status, reason="body_complete")
    return status


# ── Reconciliation body (status determination) ────────────────────────────

async def _do_reconcile_body() -> ReconciliationStatus:
    """Compare DB open trades to Bybit positions/open-orders.

    Verifier-must-fail-suspicious: every uncertain branch returns UNKNOWN
    explicitly. CLEAN is reachable only when both data sources returned
    valid payloads AND comparison found zero divergence.
    """
    # In paper mode, reconciliation is a no-op CLEAN — there is no
    # exchange-side state to compare. The check still RUNS so we know
    # the substrate is functioning.
    if settings.PAPER_TRADE:
        log.info("reconciliation: paper mode — substrate exercised, returning CLEAN")
        return ReconciliationStatus.CLEAN

    # Live mode — fetch both sides
    bybit_state = await _fetch_bybit_state()
    if bybit_state is None:
        log.error("reconciliation: bybit fetch returned None — UNKNOWN")
        return ReconciliationStatus.UNKNOWN
    if not _validate_bybit_payload(bybit_state):
        log.error("reconciliation: bybit payload malformed — UNKNOWN")
        return ReconciliationStatus.UNKNOWN

    db_state = await _fetch_db_state()
    if db_state is None:
        log.error("reconciliation: db fetch returned None — UNKNOWN")
        return ReconciliationStatus.UNKNOWN

    diff = _compare(bybit_state, db_state)
    if diff:
        await _on_divergence(diff)
        return ReconciliationStatus.DIVERGENT

    return ReconciliationStatus.CLEAN


async def _fetch_bybit_state() -> list[dict] | None:
    """Fetch Bybit open positions + open orders. Returns None on any
    fetch failure — caller treats None as UNKNOWN."""
    try:
        from exchange.bybit_client import bybit
        # Spot uses get_open_orders + balances; futures would use positions.
        # Phase-2 covers spot. Wrap with a timeout so a hanging Bybit doesn't
        # wedge the reconciliation forever.
        async with asyncio.timeout(30):
            orders = await bybit.get_open_orders()
        return orders or []
    except RedisACLViolation:
        raise
    except LayerZeroViolation:
        raise
    except Exception as e:
        log.error("_fetch_bybit_state failed: %s", e)
        return None


def _validate_bybit_payload(payload) -> bool:
    """Sanity-check the Bybit payload shape. Empty list is valid (means
    no open orders). Malformed = anything that isn't list-of-dicts."""
    if not isinstance(payload, list):
        return False
    for item in payload:
        if not isinstance(item, dict):
            return False
    return True


async def _fetch_db_state() -> list[dict] | None:
    try:
        return await db.get_open_trades(is_paper=False)
    except RedisACLViolation:
        raise
    except LayerZeroViolation:
        raise
    except Exception as e:
        log.error("_fetch_db_state failed: %s", e)
        return None


def _compare(bybit_state: list[dict], db_state: list[dict]) -> dict | None:
    """Return divergence dict if mismatch, None if matched."""
    bybit_ids = {str(o.get("orderId")) for o in bybit_state if o.get("orderId")}
    db_ids = {str(t.get("bybit_order_id")) for t in db_state if t.get("bybit_order_id")}

    only_in_bybit = bybit_ids - db_ids
    only_in_db = db_ids - bybit_ids

    if only_in_bybit or only_in_db:
        return {
            "only_in_bybit_count": len(only_in_bybit),
            "only_in_db_count":    len(only_in_db),
            "only_in_bybit":       sorted(only_in_bybit)[:10],
            "only_in_db":          sorted(only_in_db)[:10],
        }
    return None


async def _on_divergence(diff: dict) -> None:
    log.critical("SEV1 %s", json.dumps({
        "event":          "reconciliation_divergence",
        "schema_version": 1,
        "diff":           diff,
        "ts":             time.time(),
    }, sort_keys=True))

    # Set bot_paused via supervisor delegation (supervisor owns that key
    # after Phase-2 lands; reconciliation only requests).
    try:
        from governance.l0_supervisor import request_pause
        await request_pause(reason="reconciliation_divergence", source="reconciliation")
    except RedisACLViolation:
        raise
    except LayerZeroViolation:
        raise
    except Exception as e:
        log.error("could not request pause via supervisor: %s", e)

    try:
        from notifications.telegram_bot import telegram
        await telegram.send(
            f"SEV-1 reconciliation DIVERGENCE\n"
            f"Only on Bybit: {diff['only_in_bybit_count']}\n"
            f"Only in DB:    {diff['only_in_db_count']}\n\n"
            f"Bot paused. Manual investigation required."
        )
    except RedisACLViolation:
        raise
    except LayerZeroViolation:
        raise
    except Exception:
        pass


async def _persist_status(status: ReconciliationStatus, reason: str) -> None:
    """Write status + last-run timestamp to Redis."""
    try:
        await redis.set(RECON_STATUS_KEY, status.value)
        if status == ReconciliationStatus.CLEAN:
            # Only update last-clean-run timestamp on CLEAN; STALE/UNKNOWN
            # don't reset the aging clock.
            await redis.set(RECON_LAST_RUN_KEY, str(time.time()))
        log.info("reconciliation: status=%s reason=%s", status.value, reason)
    except RedisACLViolation:
        raise
    except LayerZeroViolation:
        raise
    except Exception as e:
        log.error("could not persist reconciliation status: %s", e)
