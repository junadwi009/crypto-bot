"""
governance/l0_supervisor.py
Independent Layer-0 supervisor task.

Runs in its own asyncio task, separate from trading_loop. Survives
trading-loop crashes. Sole owner of:
  - l0:supervisor_unhealthy
  - l0:bot_paused
  - l0:soft_mode_triggers
  - l0:supervisor_alive_at         (heartbeat)
  - l0:circuit_breaker_tripped     (after Phase-3 CB extraction)

After this module lands, no other code path may write those keys directly.
The Phase-2 test suite includes a meta-test that fails if new direct
writes appear elsewhere.

Closes P2-R1: provides resume_authority_check() that any /resume path
must consult before clearing bot_paused. While supervisor_unhealthy=1,
resume is refused regardless of operator action.

Soft-mode discipline (constraint from Phase 1):
  L0_SUPERVISOR_HARD_EXIT controls whether L0 violations call os._exit(2).
  Default is "false" during Phase-2 stabilization. Promotion to "true"
  is gated on 7 days clean operation OR explicit operator review of all
  triggers. End-of-Phase-2 deadline applies.

Propagation discipline (Council reminder):
  Every async boundary in this module re-raises LayerZeroViolation and
  RedisACLViolation explicitly before any broad except.
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

from governance import safety_kernel as L0
from governance.exceptions import LayerZeroViolation
from governance.redis_acl import acl, RedisACLViolation
# Phase 3 Step 3 (B2): live reconciliation-status ingestion into cycle log.
# Observational only — does NOT influence supervisor decision logic.
from governance.reconciliation import last_status as recon_last_status

log = logging.getLogger("l0_supervisor")

# Acquire ACL-bound client at import time. Raises RedisACLViolation if
# this module is missing from CALLER_PREFIX_RULES — boot-fail by design.
redis = acl.for_module(__name__)

# Soft-mode flag (Phase-2 transitional). See Phase-1 council mandate.
L0_SUPERVISOR_HARD_EXIT = os.getenv("L0_SUPERVISOR_HARD_EXIT", "false").lower() == "true"

# Loop cadence
SUPERVISOR_TICK_SECONDS = 15
HEARTBEAT_TTL_SECONDS = 60         # supervisor_alive_at expires if loop wedged
SOFT_TRIGGER_WINDOW_SECONDS = 3600
SOFT_TRIGGER_ALERT_THRESHOLD = 3   # alert SEV-0 at 3+/hour

# Structured cycle log schema. Versioned for future ingestion compatibility.
#
# CYCLE_LOG_SCHEMA_VERSION HISTORY (append-only, never edit prior entries)
#
# v1 (Phase 2, original):
#   {schema_version, event, kernel_hash_status, cb_state_consistency,
#    supervisor_unhealthy, soft_mode_trigger_count, reconciliation_status,
#    loop_latency_ms, ts}
#   reconciliation_status carried the literal placeholder string "phase3"
#   pending Phase-3 wiring.
#
# v2 (Phase 3 Step 3, 2026-05-07):
#   Same key set. reconciliation_status now carries the live value from
#   governance.reconciliation.last_status() — one of the
#   ReconciliationStatus enum values ("clean" / "stale" / "unknown" /
#   "divergent"). On non-L0 read failure, falls back to "unknown" with a
#   structured error log. Placeholder string "phase3" never appears at v2.
#   All other fields' semantics are unchanged.
#
# v3 (Phase 3 Step 4, 2026-05-07):
#   v2 keys plus:
#     cb_state_l0      (bool | None) — raw value of l0:circuit_breaker_tripped
#                                       returned by _check_cb_coherence().
#                                       None when the key is unset (preserves
#                                       distinction from explicit false).
#     cb_state_legacy  (bool | None) — raw value of the legacy unprefixed
#                                       circuit_breaker_tripped key (read via
#                                       raw redis client during the transition
#                                       window). None when unset.
#   Existing cb_state_consistency derivation is unchanged. No new write
#   surface is introduced — supervisor only reads. Preparation for the
#   eventual CB-extraction (B3); not the extraction itself.
#
# Future bumps append below with rationale. NEVER mutate prior entries.
CYCLE_LOG_SCHEMA_VERSION = 3


# ─────────────────────────────────────────────────────────────────────────
# Public surface — used by main, /resume handler, telegram handlers.
# ─────────────────────────────────────────────────────────────────────────

async def resume_authority_check() -> tuple[bool, str]:
    """Closes P2-R1.

    Any code path that wants to clear bot_paused (Telegram /resume,
    dashboard resume button, internal recovery) MUST call this first.
    Returns (allowed, reason).

    While l0:supervisor_unhealthy is set, resume is refused. Only the
    supervisor itself may clear that flag, and only after a clean cycle
    streak (see _maybe_clear_unhealthy).
    """
    try:
        unhealthy = await redis.get("l0:supervisor_unhealthy")
    except RedisACLViolation:
        raise
    except LayerZeroViolation:
        raise
    except Exception as e:
        # Fail closed: if we can't verify health state, assume unhealthy.
        log.error("resume_authority_check: unable to read l0:supervisor_unhealthy: %s", e)
        return False, "supervisor_state_unreadable"

    if unhealthy:
        return False, "l0_supervisor_unhealthy"
    return True, "ok"


async def is_paused() -> bool:
    """Read l0:bot_paused via ACL-bound client."""
    val = await redis.get("l0:bot_paused")
    return bool(val)


async def request_pause(reason: str, source: str) -> None:
    """Sets l0:bot_paused. Owner-validated by ACL — only supervisor and
    transitional main may call this path. Audit row written by orchestrator
    or audit_trail consumer; this method only sets state."""
    await redis.set("l0:bot_paused", "1")
    log.warning("L0 pause requested by %s | reason=%s", source, reason)


async def on_layer_zero_violation(violation: LayerZeroViolation,
                                   source_loop: str) -> None:
    """Phase-2 supervisor boundary handler.

    Called from main.py loop excepts on L0 violation. Owns the consequences:
      - sets l0:supervisor_unhealthy (blocks /resume)
      - sets l0:bot_paused
      - increments l0:soft_mode_triggers
      - alerts SEV-1 (or SEV-0 at threshold)
      - in HARD mode: os._exit(2)
    """
    log.critical(
        "L0 VIOLATION at supervisor boundary | source_loop=%s reason=%s "
        "module=%s recoverable=%s context=%s hard_exit=%s",
        source_loop, violation.reason, violation.source_module,
        violation.recoverable, violation.context_json(),
        L0_SUPERVISOR_HARD_EXIT,
    )

    # Persist safety state (always, regardless of mode)
    try:
        await redis.set("l0:supervisor_unhealthy", "1")
        await redis.set("l0:bot_paused", "1")
    except RedisACLViolation:
        raise
    except LayerZeroViolation:
        raise
    except Exception as e:
        log.critical("L0 supervisor: cannot persist pause state: %s", e)

    # Soft-mode trigger counter
    try:
        count = await redis.incr("l0:soft_mode_triggers")
        if count == 1:
            await redis.expire("l0:soft_mode_triggers", SOFT_TRIGGER_WINDOW_SECONDS)
        if int(count) >= SOFT_TRIGGER_ALERT_THRESHOLD:
            log.critical(
                "SEV0 L0 soft-mode trigger #%d in %ds — operator must intervene",
                count, SOFT_TRIGGER_WINDOW_SECONDS,
            )
    except RedisACLViolation:
        raise
    except LayerZeroViolation:
        raise
    except Exception:
        pass

    # External alert — best effort, never fatal
    try:
        from notifications.telegram_bot import telegram
        await telegram.send(
            f"L0 VIOLATION ({source_loop})\n\n"
            f"Reason: {violation.reason}\n"
            f"Module: {violation.source_module}\n"
            f"Recoverable: {violation.recoverable}\n"
            f"Bot is PAUSED. Resume blocked until supervisor clears health."
        )
    except RedisACLViolation:
        raise
    except LayerZeroViolation:
        raise
    except Exception as e:
        log.error("L0 supervisor: telegram alert failed: %s", e)

    if L0_SUPERVISOR_HARD_EXIT:
        log.critical("L0: HARD EXIT requested — calling os._exit(2)")
        os._exit(2)


# ─────────────────────────────────────────────────────────────────────────
# Background loop
# ─────────────────────────────────────────────────────────────────────────

async def supervise() -> None:
    """Independent supervisor loop. Runs as its own asyncio task in main.py.

    Each cycle:
      - heartbeat to l0:supervisor_alive_at (with TTL — wedged loop visible)
      - emits structured cycle log
      - checks kernel hash hasn't drifted at runtime
      - validates CB-vs-pause coherence
      - opportunistically clears l0:supervisor_unhealthy after clean streak
      - SEV-1 if any check fails

    LayerZeroViolation propagates uncaught — main.py's outer task supervisor
    catches and applies hard/soft mode. RedisACLViolation propagates the
    same way (constraint: ACL violations are governance-boundary errors).
    """
    log.info("L0 supervisor started | tick=%ds hard_exit=%s",
             SUPERVISOR_TICK_SECONDS, L0_SUPERVISOR_HARD_EXIT)
    clean_streak = 0
    CLEAN_STREAK_TO_CLEAR_UNHEALTHY = 20    # ~5 minutes at 15s tick

    while True:
        cycle_start = time.monotonic()

        # Phase 3 Step 3 (B2) — Reconciliation-status ingestion.
        # Council mandate: dedicated step before cycle_event construction;
        # do NOT interleave with kernel-hash, CB-coherence, or health checks.
        # Observational only — value is recorded in the structured log but
        # does not influence the supervisor's clean-streak decision logic
        # (see is_clean determination below — it considers ONLY kernel_hash
        # and cb_state_consistency, not reconciliation_status).
        #
        # Propagation contract (Council-locked):
        #   LayerZeroViolation → re-raise (governance-boundary error)
        #   RedisACLViolation  → re-raise (governance-boundary error)
        #   any other exception → structured log + safe default "unknown"
        try:
            recon_status_value = await recon_last_status()
            recon_status_str = (
                recon_status_value.value
                if hasattr(recon_status_value, "value")
                else str(recon_status_value)
            )
        except RedisACLViolation:
            raise
        except LayerZeroViolation:
            raise
        except Exception as e:
            log.error(
                "L0 supervisor: recon_last_status read failed (%s) — "
                "falling back to safe default 'unknown'", e,
            )
            recon_status_str = "unknown"

        cycle_event: dict = {
            "schema_version":           CYCLE_LOG_SCHEMA_VERSION,
            "event":                    "l0_supervisor_cycle",
            "kernel_hash_status":       "unknown",
            "cb_state_consistency":     "unknown",
            # Phase 3 Step 4: raw CB state. None = key unset (distinct
            # from explicit False).
            "cb_state_l0":              None,
            "cb_state_legacy":          None,
            "supervisor_unhealthy":     "unknown",
            "soft_mode_trigger_count":  -1,
            "reconciliation_status":    recon_status_str,
            "loop_latency_ms":          -1,
            "ts":                       time.time(),
        }

        try:
            # 1. Heartbeat — MUST happen first so a wedge is detectable
            await redis.set("l0:supervisor_alive_at",
                            datetime.now(timezone.utc).isoformat(),
                            ex=HEARTBEAT_TTL_SECONDS)

            # 2. Kernel hash drift check — kernel module may not have been
            # tampered with at runtime; safety_kernel.KERNEL_HASH was set
            # at import. Re-compare with file-on-disk.
            cycle_event["kernel_hash_status"] = await _check_kernel_hash()

            # 3. CB-vs-pause coherence check.
            # Phase 3 Step 4: _check_cb_coherence now returns a 3-tuple so
            # the raw CB state values can be recorded in the cycle log
            # alongside the derived coherence verdict. The verdict-derivation
            # logic itself is unchanged — only the return shape is widened.
            cb_consistency, cb_state_l0, cb_state_legacy = await _check_cb_coherence()
            cycle_event["cb_state_consistency"] = cb_consistency
            cycle_event["cb_state_l0"]          = cb_state_l0
            cycle_event["cb_state_legacy"]      = cb_state_legacy

            # 4. Health flag state
            unhealthy = await redis.get("l0:supervisor_unhealthy")
            cycle_event["supervisor_unhealthy"] = bool(unhealthy)

            # 5. Soft-mode trigger count
            try:
                trig = await redis.get("l0:soft_mode_triggers")
                cycle_event["soft_mode_trigger_count"] = int(trig or 0)
            except (TypeError, ValueError):
                cycle_event["soft_mode_trigger_count"] = 0

            # 6. Determine clean cycle
            is_clean = (
                cycle_event["kernel_hash_status"] == "ok"
                and cycle_event["cb_state_consistency"] == "ok"
            )
            if is_clean:
                clean_streak += 1
            else:
                clean_streak = 0
                log.error("L0 cycle NOT clean: %s", json.dumps(cycle_event, sort_keys=True))

            # 7. Opportunistic unhealthy-clear after sustained clean streak
            if (cycle_event["supervisor_unhealthy"] is True
                    and clean_streak >= CLEAN_STREAK_TO_CLEAR_UNHEALTHY):
                await _clear_supervisor_unhealthy(clean_streak)
                clean_streak = 0   # reset to require fresh streak before next clear

        except RedisACLViolation:
            raise
        except LayerZeroViolation:
            raise
        except asyncio.CancelledError:
            log.info("L0 supervisor cancelled")
            return
        except Exception as e:
            log.error("L0 supervisor cycle error: %s", e, exc_info=True)
            cycle_event["kernel_hash_status"] = "cycle_error"

        cycle_event["loop_latency_ms"] = int((time.monotonic() - cycle_start) * 1000)
        log.info("L0_CYCLE %s", json.dumps(cycle_event, sort_keys=True))

        try:
            await asyncio.sleep(SUPERVISOR_TICK_SECONDS)
        except asyncio.CancelledError:
            return


# ─────────────────────────────────────────────────────────────────────────
# Internal checks
# ─────────────────────────────────────────────────────────────────────────

async def _check_kernel_hash() -> str:
    """Verify safety_kernel module hash hasn't drifted at runtime.
    The hash was computed at import and frozen by _ImmutableKernelModule.
    Re-reading the file should yield the same value. Drift indicates
    tampering or a botched redeploy.
    """
    import hashlib
    try:
        with open(L0.__file__, "rb") as f:
            current = hashlib.sha256(f.read()).hexdigest()
    except Exception as e:
        log.error("kernel hash check: cannot read kernel file: %s", e)
        return "unreadable"
    if current != L0.KERNEL_HASH:
        log.critical(
            "KERNEL HASH DRIFT detected | recorded=%s current=%s",
            L0.KERNEL_HASH[:16], current[:16],
        )
        return "DRIFT"
    return "ok"


async def _check_cb_coherence() -> tuple[str, bool | None, bool | None]:
    """If circuit_breaker_tripped is set, bot_paused must also be set.
    Inverse is allowed (operator pause without CB trip).

    Phase 3 Step 4 (B3-prep): returns a 3-tuple
        (coherence_status, cb_state_l0, cb_state_legacy)
    so the supervisor cycle log can record raw CB state alongside the
    derived coherence verdict.

    cb_state_l0      raw bool of l0:circuit_breaker_tripped, or None when
                     the key is unset (preserves the absent-vs-explicit-false
                     distinction the Council mandated).
    cb_state_legacy  raw bool of legacy unprefixed circuit_breaker_tripped,
                     or None when unset.

    No new write surface is introduced. The pre-existing REPAIR path
    (writing l0:bot_paused when CB is tripped but pause flag is unset)
    is preserved verbatim — the only behavior delta is that BOTH raw
    values are now read every cycle (previously the legacy key was read
    only when the l0 key was falsy). Each key is still read at most once
    per cycle — no double reads.
    """
    try:
        cb_l0_raw = await redis.get("l0:circuit_breaker_tripped")
        # Legacy CB still writes to non-prefixed key during transition.
        # Read via raw client is acceptable here because we are the
        # supervisor and we are explicitly watching legacy state.
        from utils.redis_client import redis as _raw
        cb_legacy_raw = await _raw.get("circuit_breaker_tripped")

        # Preserve None vs explicit-false distinction (Council-locked).
        cb_state_l0 = None if cb_l0_raw is None else bool(cb_l0_raw)
        cb_state_legacy = None if cb_legacy_raw is None else bool(cb_legacy_raw)

        cb_set = bool(cb_l0_raw or cb_legacy_raw)
        paused = bool(await redis.get("l0:bot_paused"))
        if cb_set and not paused:
            log.critical("CB tripped but bot_paused unset — coherence violation")
            await redis.set("l0:bot_paused", "1")
            return ("REPAIRED_paused_set_to_match_cb", cb_state_l0, cb_state_legacy)
        return ("ok", cb_state_l0, cb_state_legacy)
    except RedisACLViolation:
        raise
    except LayerZeroViolation:
        raise
    except Exception as e:
        log.error("cb coherence check failed: %s", e)
        return ("check_error", None, None)


async def _clear_supervisor_unhealthy(clean_streak: int) -> None:
    """Clear l0:supervisor_unhealthy after sustained clean streak.

    This is the ONLY code path that clears the flag. Operator-driven
    /resume cannot clear it; only a continuous run of healthy supervisor
    cycles. Audit-logged for incident review.
    """
    log.warning(
        "L0 supervisor: clearing supervisor_unhealthy after %d clean cycles",
        clean_streak,
    )
    try:
        await redis.delete("l0:supervisor_unhealthy")
        # Audit trail (when audit_trail table exists from Phase-2 migration)
        try:
            from database.client import db
            await db.log_event(
                event_type="l0_supervisor_unhealthy_cleared",
                severity="warning",
                message=f"supervisor_unhealthy auto-cleared after {clean_streak} clean cycles",
                data={"clean_streak": clean_streak},
            )
        except RedisACLViolation:
            raise
        except LayerZeroViolation:
            raise
        except Exception as e:
            log.error("audit write failed during unhealthy clear: %s", e)
    except RedisACLViolation:
        raise
    except LayerZeroViolation:
        raise
