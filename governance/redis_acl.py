"""
governance/redis_acl.py
Layer-authority ACL substrate for Redis state.

Authority derives from caller IDENTITY (module name), not from caller
ASSERTION (a layer= parameter). Modules obtain a bound client at import
time via acl.for_module(__name__); that client refuses any write whose
key prefix is not declared in CALLER_PREFIX_RULES for that module.

Build order inside this file (per Council direction):
  1. Authority declaration table     (CALLER_PREFIX_RULES)
  2. RedisACLViolation(BaseException)
  3. Module-bound client acquisition (_ACL.for_module)
  4. Prefix enforcement              (RedisACLClient._key_allowed)
  5. Structured CRITICAL event       (RedisACLClient._emit_violation)
  6. Read-path enforcement           (get/set/delete/incr/expire)
  7. Narrow eval surface             (release_lock; eval is private)
  8. Legacy fallback                 (intentionally absent in v1)

Surface minimization (constraint 15):
The RedisACLClient exposes EXACTLY these methods:
  set, get, delete, incr, expire, release_lock
No __getattr__ forwarding to the underlying client. Adding a method
requires explicit code change + tests + structured-logging coverage.

Eval restriction (constraint 16):
_eval is private. Only release_lock(lock_key, lock_token) is public,
and it uses a module-private Lua script literal. Future Lua needs
get their own narrow public method, NEVER generic eval exposure.

R1 — Durable ACL event persistence (Phase 2.5):
Every violation is persisted to security_events table BEFORE the
violation is raised. Persistence failure logs CRITICAL but does NOT
suppress the raise. The original RedisACLViolation propagates as
BaseException regardless of DB availability.

Order of operations on every violation:
  1. _persist_security_event(payload)   — durable sink
  2. log.critical(structured event)     — log stream
  3. raise RedisACLViolation(...)       — BaseException propagation
"""

from __future__ import annotations
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Iterable

from utils.redis_client import redis as _raw_redis

log = logging.getLogger("redis_acl")

# Schema version for structured ACL events. Monotonic; never reused.
# v1 (Phase 2, 2026-05-07):
#   {schema_version, event, caller, attempted_op, attempted_key,
#    allowed_prefixes, ts}
EVENT_SCHEMA_VERSION = 1


# ── R1: Durable ACL event persistence ─────────────────────────────────────
#
# Persists every violation to the security_events table BEFORE the
# RedisACLViolation propagates. Persistence failure does NOT suppress
# the raise — failure emits CRITICAL log, then control returns to the
# caller which proceeds to raise.
#
# Note: this helper is intentionally synchronous. The supabase python
# client's .execute() is sync regardless of context. Wrapping in async
# would only mask that; sync clarifies the actual semantic.

def _persist_security_event(payload: dict) -> None:
    """Insert a security_events row.

    R1 contract:
      - persistence happens BEFORE the caller raises RedisACLViolation
      - persistence failure logs CRITICAL but does NOT raise
      - caller continues to its raise step regardless of outcome here

    The supabase write is wrapped in a broad try/except. We catch
    `Exception` — NOT `BaseException` — so RedisACLViolation cannot
    enter this function (it's the thing being persisted). Catching
    Exception preserves the propagation guarantee for any L0/ACL
    violation that might bubble through DB internals.
    """
    try:
        from database.client import db
        ts_iso = datetime.fromtimestamp(
            payload.get("ts", time.time()), tz=timezone.utc,
        ).isoformat()
        db._get().table("security_events").insert({
            "event_time":     ts_iso,
            "event_type":     payload.get("event", "redis_acl_violation"),
            "severity":       "critical",
            "schema_version": int(payload.get("schema_version", EVENT_SCHEMA_VERSION)),
            "caller":         str(payload.get("caller", "unknown")),
            "payload":        payload,
        }).execute()
    except Exception as e:
        # R1 contract: persistence failure must emit CRITICAL but
        # MUST NOT suppress the original violation. We log here and
        # return; the caller proceeds to raise.
        log.critical(
            "SECURITY_EVENT_PERSIST_FAILED %s",
            json.dumps({
                "schema_version": EVENT_SCHEMA_VERSION,
                "event":          "security_event_persist_failed",
                "persist_error":  str(e),
                "original_event": payload,
                "ts":             time.time(),
            }, default=str, sort_keys=True),
        )


# ── 1. Authority declaration table ────────────────────────────────────────
#
# Each module that needs Redis WRITE authority must appear here with the
# set of key prefixes it may write. Read authority follows the same table:
# a module may only read keys whose prefix it could write. (This is stricter
# than necessary today but prevents cross-layer information leakage as the
# system grows. Phase 3 may relax read rules with a separate read-allowlist
# if operational need emerges.)
#
# After Phase-2 supervisor lands, l0:* keys are owned EXCLUSIVELY by
# governance.l0_supervisor. main.py's authority shrinks accordingly
# (constraint 17).

CALLER_PREFIX_RULES: dict[str, frozenset[str]] = {
    # Layer 0 supervisor — sole owner of l0:* state
    "governance.l0_supervisor":  frozenset({"l0:"}),

    # Layer 3 orchestrator — writes l1:* (recon-derived) and reads l2:*
    "governance.orchestrator":   frozenset({"l1:", "l2:"}),

    # Reconciliation worker — writes l1:* (recon status, locks)
    "governance.reconciliation": frozenset({"l1:"}),

    # Layer 2 signal pipeline — writes l2:* (news flags, model state)
    "engine.signal_generator":   frozenset({"l2:"}),

    # Legacy CB writer — transitional. Will be retired during Phase 3
    # cleanup when CB logic migrates fully into the L0 supervisor.
    "engine.circuit_breaker":    frozenset({"l0:"}),

    # Main supervisor boundary — bootstrap-time only. Mutations during
    # runtime should go through the L0 supervisor, not main.
    "main":                      frozenset({"l0:", "l1:"}),

    # Test harnesses — explicitly declared so tests can exercise paths
    # without bypassing the ACL contract entirely.
    "tests.governance.test_redis_acl": frozenset({"l0:", "l1:", "l2:"}),
}


# ── 2. Exception type ─────────────────────────────────────────────────────
#
# RedisACLViolation inherits from BaseException so legacy `except Exception:`
# blocks cannot suppress it. Same propagation contract as LayerZeroViolation.

class RedisACLViolation(BaseException):
    """Raised on any unauthorized Redis access attempt.

    BaseException by design — preserves propagation guarantees.
    Carries structured fields for forensic analysis. __str__ remains
    single-line and log-safe.
    """

    def __init__(
        self,
        caller: str,
        attempted_op: str,
        attempted_key: str,
        allowed_prefixes: Iterable[str],
    ):
        # Constraint 3: __str__ must remain single-line. Flatten ALL stored
        # fields here so __str__ cannot accidentally produce multiline output
        # via attribute interpolation, even if attempted_key contains \n.
        self.caller = " ".join(str(caller).split())
        self.attempted_op = " ".join(str(attempted_op).split())
        self.attempted_key = " ".join(str(attempted_key).split())
        self.allowed_prefixes = sorted(allowed_prefixes)
        msg = (f"caller={self.caller} op={self.attempted_op} "
               f"key={self.attempted_key} allowed={self.allowed_prefixes}")
        super().__init__(" ".join(msg.split()))

    def __str__(self) -> str:
        return (f"[ACL] caller={self.caller} op={self.attempted_op} "
                f"key={self.attempted_key} allowed={self.allowed_prefixes}")


# ── 7. Module-private Lua scripts (declared early so client can reference) ─
#
# These are NOT exposed via _eval to callers. Each is wrapped in a narrow
# public method on RedisACLClient. New Lua scripts get their own wrappers,
# never a generic eval pathway.

_RECONCILIATION_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


# ── 4-7. Module-bound client ──────────────────────────────────────────────

class RedisACLClient:
    """A Redis client bound to a specific module's authority declaration.

    Acquired only via acl.for_module(__name__). Refuses writes outside
    the module's declared prefixes; emits a structured CRITICAL event
    on every denial AND raises RedisACLViolation.

    Method surface is intentionally narrow:
      set, get, delete, incr, expire, release_lock
    """

    def __init__(self, caller: str, allowed_prefixes: frozenset[str]):
        self._caller = caller
        self._allowed = allowed_prefixes

    @property
    def caller(self) -> str:
        return self._caller

    @property
    def allowed_prefixes(self) -> frozenset[str]:
        return self._allowed

    # ── Prefix enforcement (4) ────────────────────────────────────────
    def _key_allowed(self, key: str) -> bool:
        if not isinstance(key, str):
            return False
        return any(key.startswith(p) for p in self._allowed)

    # ── Structured event emission (5) — R1 persistence + log ──────────
    def _emit_violation(self, op: str, key: str) -> None:
        """Persist + emit single-line CRITICAL JSON event before raise.

        R1 order of operations:
          1. _persist_security_event(event)   — durable sink
          2. log.critical(structured event)   — log stream
        Caller then raises RedisACLViolation.

        Persistence failure does NOT suppress the log or the eventual
        raise. The structured log is still emitted even when DB is down.
        """
        event = {
            "schema_version":   EVENT_SCHEMA_VERSION,
            "event":            "redis_acl_violation",
            "caller":           self._caller,
            "attempted_op":     op,
            "attempted_key":    str(key),
            "allowed_prefixes": sorted(self._allowed),
            "ts":               time.time(),
        }
        # R1: persistence BEFORE log + raise. _persist_security_event
        # itself never raises — failures are logged inside.
        _persist_security_event(event)
        # Single-line JSON; safe for ingestion
        log.critical("SECURITY %s", json.dumps(event, sort_keys=True))

    def _check_or_raise(self, op: str, key: str) -> None:
        if not self._key_allowed(key):
            self._emit_violation(op, key)
            raise RedisACLViolation(
                caller=self._caller,
                attempted_op=op,
                attempted_key=str(key),
                allowed_prefixes=self._allowed,
            )

    # ── Read-path enforcement (6): same severity as writes ────────────
    async def get(self, key: str) -> Any:
        self._check_or_raise("get", key)
        return await _raw_redis.get(key)

    # ── Write methods (6) ─────────────────────────────────────────────
    async def set(self, key: str, value: Any, **kwargs: Any) -> Any:
        self._check_or_raise("set", key)
        return await _raw_redis.set(key, value, **kwargs)

    async def delete(self, *keys: str) -> int:
        for key in keys:
            self._check_or_raise("delete", key)
        if not keys:
            return 0
        return await _raw_redis.delete(*keys)

    async def incr(self, key: str) -> int:
        self._check_or_raise("incr", key)
        return await _raw_redis.incr(key)

    async def expire(self, key: str, seconds: int) -> Any:
        self._check_or_raise("expire", key)
        return await _raw_redis.expire(key, seconds)

    # ── Narrow eval surface (7) — NEVER expose generic eval ───────────
    async def _eval(self, script: str, num_keys: int, *args: Any) -> Any:
        """PRIVATE. Only consumed by purpose-specific wrappers below.
        Key in args[0] is ACL-checked exactly like other operations."""
        if num_keys < 1:
            # Lua without keys = unrestricted execution — refuse
            self._emit_violation("eval", "<no-key>")
            raise RedisACLViolation(
                caller=self._caller, attempted_op="eval",
                attempted_key="<no-key>",
                allowed_prefixes=self._allowed,
            )
        self._check_or_raise("eval", str(args[0]))
        return await _raw_redis.eval(script, num_keys, *args)

    async def release_lock(self, lock_key: str, lock_token: str) -> bool:
        """Release a SET NX EX lock owned by the caller.

        The Lua script is a module constant — the caller cannot inject
        arbitrary Lua. Only the holder of the matching token can release.
        """
        result = await self._eval(
            _RECONCILIATION_RELEASE_SCRIPT, 1, lock_key, lock_token,
        )
        return bool(result)


# ── 3. Module-bound client acquisition ────────────────────────────────────

class _ACL:
    """ACL registry. Sole entry point for obtaining a bound client.

    acl.for_module(__name__) returns a RedisACLClient pinned to the
    caller's declared prefixes. If the caller is not declared in
    CALLER_PREFIX_RULES, this raises RedisACLViolation at import time
    — boot-fail behavior is intentional (constraint 5).
    """

    def for_module(self, module_name: str) -> RedisACLClient:
        if module_name not in CALLER_PREFIX_RULES:
            # Boot-fail path. Undeclared modules cannot operate.
            # R1: persist BEFORE log BEFORE raise. Bootstrap violations
            # are forensically critical — operator needs to know which
            # module attempted to register without authority declaration.
            event = {
                "schema_version":   EVENT_SCHEMA_VERSION,
                "event":            "redis_acl_violation",
                "caller":           module_name,
                "attempted_op":     "bootstrap",
                "attempted_key":    "<acl_registration>",
                "allowed_prefixes": [],
                "ts":               time.time(),
            }
            _persist_security_event(event)
            log.critical("SECURITY %s", json.dumps(event, sort_keys=True))
            raise RedisACLViolation(
                caller=module_name,
                attempted_op="bootstrap",
                attempted_key="<acl_registration>",
                allowed_prefixes=frozenset(),
            )
        return RedisACLClient(
            caller=module_name,
            allowed_prefixes=CALLER_PREFIX_RULES[module_name],
        )


acl = _ACL()


# ── 8. Legacy fallback handling ───────────────────────────────────────────
#
# Intentionally NOT IMPLEMENTED in v1. Legacy modules continue to use
# utils.redis_client.redis directly until Phase 3 cleanup migrates them.
# A meta-test (tests/governance/test_acl_no_new_raw_clients.py) flags any
# NEW raw-client adoption introduced after Phase 2.
