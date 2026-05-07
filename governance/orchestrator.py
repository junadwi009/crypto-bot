"""
governance/orchestrator.py
Observe-only orchestrator (Phase 2).

Records EVERY pipeline invocation as an immutable orchestrator_decisions row.
In Phase 2, final_action == requested_action (passthrough) but the row also
records the COUNTERFACTUAL — what enforcement mode would have done. The
14-day observe-only review uses these rows to evaluate veto false-positive
rate before Phase 3 enables enforcement.

Constraint summary (locked):
  6.  layer_inputs JSON includes 6 required keys
  7.  review queries committed beside migrations with SCHEMA_VERSION
  12. decision_time captured ONCE at evaluate() entry, propagated
  13. LAYER_INPUTS_SCHEMA_VERSION monotonic; never reused
  18. decision row written for EVERY invocation (including holds)

Council reminder:
  observe-only is non-negotiable until 14-day veto review completes.
  ORCHESTRATOR_OBSERVE_ONLY is read at module import; no runtime flip.

Propagation:
  Every async boundary re-raises LayerZeroViolation and RedisACLViolation
  before any broad except.
"""

from __future__ import annotations
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from governance import safety_kernel as L0
from governance.exceptions import LayerZeroViolation
from governance.redis_acl import acl, RedisACLViolation
from governance.reconciliation import (
    ReconciliationStatus, RECON_TO_GOVERNANCE, last_status as recon_last_status,
)

log = logging.getLogger("orchestrator")


# ─────────────────────────────────────────────────────────────────
# R3 — Counterfactual determinism hash
# ─────────────────────────────────────────────────────────────────
#
# Identity hash of an enforcement decision. Hash of the canonical JSON
# representation of {action, size_scale, mode, vetoes}. The same inputs
# MUST always produce the same hash; different vetoes MUST produce a
# different hash.
#
# Used by Phase-3 review queries to:
#   - confirm replay determinism (decision IDs match across replays)
#   - detect when veto computation logic changes silently
#   - cluster decisions by enforcement-equivalent outcome
#
# Canonicalization rules (locked):
#   sort_keys=True, separators=(",", ":"), ensure_ascii=False, UTF-8
# Stable list ordering preserved — vetoes list NOT sorted by hash function.
# The orchestrator's _compute_counterfactual produces vetoes in deterministic
# order; preserving that order is part of the replay invariant.

def compute_counterfactual_hash(
    action: str, size_scale: float, mode: str, vetoes: list[str],
) -> str:
    """SHA-256 of the canonical JSON of the four counterfactual fields.

    Replay invariant: same inputs -> same hash, byte for byte, across
    runs and across processes. Different vetoes -> different hash.
    """
    payload = json.dumps(
        {
            "action":     action,
            "size_scale": float(size_scale),
            "mode":       mode,
            "vetoes":     list(vetoes),  # preserve list ordering as emitted
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

# ACL-bound client (boot-fails if module not in CALLER_PREFIX_RULES)
redis = acl.for_module(__name__)

# ─────────────────────────────────────────────────────────────────
# Versioning (Council constraint 13: monotonic, never reused)
# ─────────────────────────────────────────────────────────────────
ORCHESTRATOR_VERSION = "0.2.0-observe"

# LAYER_INPUTS_SCHEMA_VERSION HISTORY (append-only, never edit prior entries)
#
# v1 (Phase 2, 2026-05-07):
#   {schema_version, kernel_hash, kernel_version, orchestrator_version,
#    governance_mode, reconciliation_raw, reconciliation_implication,
#    rule_signal, haiku_signal, sonnet_signal, guard_state,
#    computed_size, computed_sl, computed_tp, stage_reached, pair}
#
# Future bumps append below with rationale. NEVER mutate prior entries.
LAYER_INPUTS_SCHEMA_VERSION = 1

# Observe-only flag — Phase 3 promotion is the ONLY way this flips.
ORCHESTRATOR_OBSERVE_ONLY = os.getenv("ORCHESTRATOR_OBSERVE_ONLY", "true").lower() != "false"

# Static governance-mode key. Phase 2 keeps it always "normal"; Phase 3
# wiring activates dynamic mode transitions inside the orchestrator.
GOVERNANCE_MODE_KEY = "l1:governance_mode"
DEFAULT_GOVERNANCE_MODE = "normal"


# ─────────────────────────────────────────────────────────────────
# Decision record
# ─────────────────────────────────────────────────────────────────

class OrchestratorDecision:
    """In-memory representation of a row destined for orchestrator_decisions.

    R3: counterfactual_hash is the SHA-256 of canonical JSON of the four
    enforcement fields (action, size_scale, mode, vetoes). Persisted on
    every row.
    """

    __slots__ = (
        "decision_id", "decision_time", "pair", "requested_action",
        "final_action", "size_usd", "size_scale",
        "observe_only_passthrough",
        "counterfactual_action", "counterfactual_size_usd",
        "counterfactual_size_scale", "counterfactual_mode",
        "counterfactual_hash",   # R3
        "layer_vetoes", "layer_inputs", "governance_mode", "explanation",
    )

    def __init__(self, **kwargs):
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))

    def to_db(self) -> dict:
        return {
            "decision_id":              str(self.decision_id),
            "decision_time":            self.decision_time.isoformat()
                                          if isinstance(self.decision_time, datetime)
                                          else self.decision_time,
            "pair":                     self.pair,
            "requested_action":         self.requested_action,
            "final_action":             self.final_action,
            "size_usd":                 round(float(self.size_usd or 0), 4),
            "size_scale":               round(float(self.size_scale or 1.0), 3),
            "observe_only_passthrough": bool(self.observe_only_passthrough),
            "counterfactual_action":    self.counterfactual_action,
            "counterfactual_size_usd":  round(float(self.counterfactual_size_usd or 0), 4),
            "counterfactual_size_scale": round(float(self.counterfactual_size_scale or 1.0), 3),
            "counterfactual_mode":      self.counterfactual_mode,
            "counterfactual_hash":      self.counterfactual_hash,   # R3
            "layer_vetoes":             list(self.layer_vetoes or []),
            "layer_inputs":             self.layer_inputs,
            "governance_mode":          self.governance_mode,
            "explanation":              self.explanation,
        }


# ─────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────

class Orchestrator:

    def __init__(self):
        self.observe_only = ORCHESTRATOR_OBSERVE_ONLY
        log.info(
            "Orchestrator initialized | observe_only=%s version=%s schema=v%d",
            self.observe_only, ORCHESTRATOR_VERSION, LAYER_INPUTS_SCHEMA_VERSION,
        )

    async def evaluate(
        self,
        pair: str,
        requested_action: str,
        pipeline_state: dict,
        proposed_size_usd: float = 0.0,
        proposed_sl: float | None = None,
        proposed_tp: float | None = None,
    ) -> OrchestratorDecision:
        """Compute decision row + persist. In observe-only mode, returns a
        passthrough decision but still records counterfactual veto state.

        Constraint 12: single decision_time captured here and propagated.
        """
        # ── single clock read for entire decision lifecycle ──
        decision_time = datetime.now(timezone.utc)

        # ── snapshot inputs (schema v1) ──
        try:
            layer_inputs = await self._snapshot_inputs(
                pair=pair,
                pipeline_state=pipeline_state,
                proposed_size_usd=proposed_size_usd,
                proposed_sl=proposed_sl,
                proposed_tp=proposed_tp,
            )
        except RedisACLViolation:
            raise
        except LayerZeroViolation:
            raise
        except Exception as e:
            log.error("orchestrator: snapshot_inputs failed: %s", e, exc_info=True)
            # Still record a row — the audit trail is not conditional.
            layer_inputs = {
                "schema_version":             LAYER_INPUTS_SCHEMA_VERSION,
                "kernel_hash":                L0.KERNEL_HASH[:16],
                "kernel_version":             L0.KERNEL_VERSION,
                "orchestrator_version":       ORCHESTRATOR_VERSION,
                "governance_mode":            DEFAULT_GOVERNANCE_MODE,
                "reconciliation_raw":         ReconciliationStatus.UNKNOWN.value,
                "reconciliation_implication": RECON_TO_GOVERNANCE[ReconciliationStatus.UNKNOWN],
                "snapshot_error":             str(e),
            }

        # ── compute counterfactual (what enforcement WOULD do) ──
        counterfactual = self._compute_counterfactual(
            requested_action=requested_action,
            proposed_size_usd=proposed_size_usd,
            layer_inputs=layer_inputs,
        )

        # ── actual outcome (in observe-only, == requested) ──
        if self.observe_only:
            actual_action = requested_action
            actual_size = proposed_size_usd
            actual_scale = 1.0
        else:
            actual_action = counterfactual["action"]
            actual_size = counterfactual["size_usd"]
            actual_scale = counterfactual["size_scale"]

        # R3: counterfactual identity hash. Computed from the four
        # enforcement fields so it is deterministic across runs.
        cf_hash = compute_counterfactual_hash(
            action     = counterfactual["action"],
            size_scale = counterfactual["size_scale"],
            mode       = counterfactual["mode"],
            vetoes     = counterfactual["vetoes"],
        )

        decision = OrchestratorDecision(
            decision_id              = uuid.uuid4(),
            decision_time            = decision_time,
            pair                     = pair,
            requested_action         = requested_action,
            final_action             = actual_action,
            size_usd                 = actual_size,
            size_scale               = actual_scale,
            observe_only_passthrough = self.observe_only,
            counterfactual_action    = counterfactual["action"],
            counterfactual_size_usd  = counterfactual["size_usd"],
            counterfactual_size_scale= counterfactual["size_scale"],
            counterfactual_mode      = counterfactual["mode"],
            counterfactual_hash      = cf_hash,           # R3
            layer_vetoes             = counterfactual["vetoes"],
            layer_inputs             = layer_inputs,
            governance_mode          = layer_inputs.get("governance_mode", DEFAULT_GOVERNANCE_MODE),
            explanation              = self._explain(counterfactual, self.observe_only),
        )

        await self._persist_decision(decision)
        return decision

    # ─────────────────────────────────────────────────────────────
    async def _snapshot_inputs(
        self,
        pair: str,
        pipeline_state: dict,
        proposed_size_usd: float,
        proposed_sl: float | None,
        proposed_tp: float | None,
    ) -> dict:
        """Snapshot all inputs needed for replay. Schema v1.

        Required keys (Council mandate constraint 6):
          schema_version, kernel_hash, kernel_version, orchestrator_version,
          governance_mode, reconciliation_raw, reconciliation_implication.
        """
        recon_raw = await recon_last_status()
        recon_implication = RECON_TO_GOVERNANCE[recon_raw]

        try:
            mode = await redis.get(GOVERNANCE_MODE_KEY)
            mode = mode if mode else DEFAULT_GOVERNANCE_MODE
        except RedisACLViolation:
            raise
        except LayerZeroViolation:
            raise
        except Exception:
            mode = DEFAULT_GOVERNANCE_MODE

        return {
            "schema_version":             LAYER_INPUTS_SCHEMA_VERSION,
            "kernel_hash":                L0.KERNEL_HASH[:16],
            "kernel_version":             L0.KERNEL_VERSION,
            "orchestrator_version":       ORCHESTRATOR_VERSION,
            "governance_mode":            mode if isinstance(mode, str) else (
                                              mode.decode() if hasattr(mode, "decode")
                                              else DEFAULT_GOVERNANCE_MODE
                                          ),
            "reconciliation_raw":         recon_raw.value,
            "reconciliation_implication": recon_implication,
            # Pipeline snapshot
            "pair":          pair,
            "stage_reached": pipeline_state.get("stage_reached", "unknown"),
            "rule_signal":   pipeline_state.get("rule_result"),
            "haiku_signal":  pipeline_state.get("haiku_result"),
            "sonnet_signal": pipeline_state.get("sonnet_result"),
            "guard_state":   pipeline_state.get("guard_state"),
            "computed_size": float(proposed_size_usd or 0),
            "computed_sl":   proposed_sl,
            "computed_tp":   proposed_tp,
        }

    # ─────────────────────────────────────────────────────────────
    def _compute_counterfactual(
        self,
        requested_action: str,
        proposed_size_usd: float,
        layer_inputs: dict,
    ) -> dict:
        """Compute what enforcement mode WOULD do.

        Phase 2 vetoes (read-only — these would fire if observe_only=false):
          - reconciliation_unknown   → mode = frozen   (no new entries)
          - reconciliation_divergent → mode = frozen
          - reconciliation_stale     → mode = restricted (size × 0.5)

        Veto-list ordering is deterministic. Ordering is part of the R3
        hash identity (constraint: stable list ordering preserved).

        Returns dict with action, size_usd, size_scale, mode, vetoes.
        """
        vetoes: list[str] = []
        mode = layer_inputs.get("governance_mode", DEFAULT_GOVERNANCE_MODE)
        action = requested_action
        size = float(proposed_size_usd or 0)
        scale = 1.0

        recon_implication = layer_inputs.get("reconciliation_implication", "frozen")

        if recon_implication == "frozen":
            vetoes.append(f"recon_{layer_inputs.get('reconciliation_raw', 'unknown')}")
            mode = "frozen"
            action = "hold"
            size = 0.0
            scale = 0.0

        elif recon_implication == "restricted":
            vetoes.append(f"recon_{layer_inputs.get('reconciliation_raw', 'stale')}")
            mode = "restricted"
            scale = 0.5
            size = size * scale

        # Holds skip the rest of the counterfactual; their action is hold by definition.
        if requested_action == "hold":
            return {
                "action": "hold", "size_usd": 0.0, "size_scale": 0.0,
                "mode": mode, "vetoes": vetoes,
            }

        return {
            "action":     action,
            "size_usd":   round(size, 4),
            "size_scale": round(scale, 3),
            "mode":       mode,
            "vetoes":     vetoes,
        }

    # ─────────────────────────────────────────────────────────────
    @staticmethod
    def _explain(counterfactual: dict, observe_only: bool) -> str:
        prefix = "[observe-only] " if observe_only else ""
        if counterfactual["vetoes"]:
            return (f"{prefix}counterfactual={counterfactual['action']} "
                    f"mode={counterfactual['mode']} "
                    f"vetoes={','.join(counterfactual['vetoes'])} "
                    f"scale={counterfactual['size_scale']}")
        return (f"{prefix}counterfactual={counterfactual['action']} "
                f"mode={counterfactual['mode']}")

    # ─────────────────────────────────────────────────────────────
    async def _persist_decision(self, decision: OrchestratorDecision) -> None:
        """Insert the decision row. Append-only via RLS — UPDATE/DELETE blocked.

        If the DB write fails, log the row content as structured event so
        the audit trail is at least visible in logs even when DB is down.
        Decision rows are operationally critical; we cannot let DB outage
        silently lose them.
        """
        row = decision.to_db()
        try:
            from database.client import db
            db._get().table("orchestrator_decisions").insert(row).execute()
        except RedisACLViolation:
            raise
        except LayerZeroViolation:
            raise
        except Exception as e:
            log.error(
                "ORCHESTRATOR_DECISION_DB_WRITE_FAILED %s",
                json.dumps({"error": str(e), "row": row}, default=str, sort_keys=True),
            )
            return
        # Always emit a structured trace line for the row (mirrors the DB
        # row in logs — useful when DB is healthy too, for tail -f workflows).
        log.info(
            "ORCHESTRATOR_DECISION %s",
            json.dumps({
                "decision_id":   str(decision.decision_id),
                "pair":          decision.pair,
                "final_action":  decision.final_action,
                "counterfactual_action": decision.counterfactual_action,
                "vetoes":        decision.layer_vetoes,
                "mode":          decision.counterfactual_mode,
                "observe_only":  decision.observe_only_passthrough,
            }, sort_keys=True),
        )


orchestrator = Orchestrator()
