"""
governance/exceptions.py
Layer-0 exception types.

LayerZeroViolation inherits from BaseException — NOT Exception — by design.
A standard `except Exception:` block must NOT catch this. Only the supervisor
boundary in main.py is permitted to catch and handle it.

If you find yourself wanting to catch LayerZeroViolation in application code,
you are wrong. The intended behaviors are:
  - re-raise (explicit, propagation-preserving)
  - let it propagate uncaught (default)
  - catch ONLY at the supervisor boundary

The exception object stringifies to a single line so it is safe to log via
standard formatters without leaking multiline payloads into structured sinks.
Forensic context lives in `.context` (a dict) — log it as JSON if needed.
"""

from __future__ import annotations
import json
from typing import Any


class LayerZeroViolation(BaseException):
    """
    Raised when a Layer-0 invariant is violated or unverifiable.

    Fields:
      reason        — human-readable cause (single line)
      layer         — always "L0"
      source_module — caller's module name (e.g., "engine.position_manager")
      recoverable   — whether the system may attempt continuation after pause
                      (True = pause + alert; False = pause + alert + escalate)
      context       — optional forensic payload (dict). Not included in __str__.

    Inherits from BaseException so that `except Exception:` blocks in
    legacy code cannot accidentally swallow Layer-0 violations.
    """

    def __init__(
        self,
        reason: str,
        layer: str = "L0",
        source_module: str = "unknown",
        recoverable: bool = True,
        context: dict[str, Any] | None = None,
    ):
        # Single-line safe summary; sub-newlines flattened to spaces.
        clean_reason = " ".join(str(reason).split())
        super().__init__(clean_reason)
        self.reason: str = clean_reason
        self.layer: str = layer
        self.source_module: str = source_module
        self.recoverable: bool = recoverable
        self.context: dict[str, Any] = context or {}

    def __str__(self) -> str:
        return (
            f"[{self.layer}] {self.source_module}: {self.reason} "
            f"(recoverable={self.recoverable})"
        )

    def __repr__(self) -> str:
        return (
            f"LayerZeroViolation(reason={self.reason!r}, "
            f"layer={self.layer!r}, source_module={self.source_module!r}, "
            f"recoverable={self.recoverable!r})"
        )

    def context_json(self) -> str:
        """Return forensic context as a single-line JSON string."""
        try:
            return json.dumps(self.context, default=str, sort_keys=True)
        except Exception:
            return "{}"
