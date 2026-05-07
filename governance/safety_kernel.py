"""
governance/safety_kernel.py
Layer-0 Hard Safety Kernel.

This module is the single source of truth for L0 invariants. Every L0 reader
must re-validate against these constants on read; the constants themselves
must not be mutated at runtime.

Constants below mirror the previously-scattered values in config/settings.py
EXACTLY as of KERNEL_VERSION 1.0.0. No risk-profile changes are introduced
by this migration; only enforcement.

Mutation contract:
  - Constants are immutable after module init (`_freeze_module()`).
  - Any attempt to assign to a kernel attribute raises AttributeError.
  - Module hash is computed at import and logged at boot via log_kernel_boot().
  - If the source file changes between deployments, KERNEL_VERSION must
    be bumped and the change must be reviewed.

Usage pattern:
    from governance.safety_kernel import (
        MAX_RISK_PER_TRADE, ABSOLUTE_SIZE_CAP_PCT,
        validate_position_multiplier, cap_news_factor,
    )
    multiplier = validate_position_multiplier(value, source="position_manager")
"""

from __future__ import annotations
import hashlib
import logging
import sys
from types import ModuleType
from typing import Any, Final

from governance.exceptions import LayerZeroViolation

log = logging.getLogger("safety_kernel")

# ── KERNEL VERSION ────────────────────────────────────────────────────────
# Bump on any change to constants below. Every redeploy logs the hash;
# unexpected hash drift without a version bump is a SEV-0.
KERNEL_VERSION: Final[str] = "1.0.0"

# ── L0 INVARIANTS — DO NOT EDIT WITHOUT KERNEL_VERSION BUMP ───────────────
# Per-trade risk envelope
MAX_RISK_PER_TRADE:     Final[float] = 0.02        # mirrors settings.MAX_RISK_PER_TRADE
ABSOLUTE_SIZE_CAP_PCT:  Final[float] = 0.05        # mirrors order_guard hardcoded 5%
ABSOLUTE_MIN_ORDER_USD: Final[float] = 5.0         # mirrors order_guard hardcoded $5

# Portfolio risk envelope
MAX_DAILY_DRAWDOWN:     Final[float] = 0.15        # mirrors settings.MAX_DAILY_DRAWDOWN
MAX_POSITIONS_TOTAL:    Final[int]   = 3           # mirrors order_guard hardcoded 3
MAX_POSITIONS_PER_PAIR: Final[int]   = 2           # mirrors order_guard hardcoded 2
CAPITAL_FLOOR_USD:      Final[float] = 150.0      # mirrors order_guard hardcoded 150

# Throughput
MAX_ORDERS_PER_MIN:     Final[int]   = 3           # mirrors settings.MAX_ORDERS_PER_MIN

# News amplification — Council mandate M5: news may REDUCE size, never INCREASE
ABSOLUTE_NEWS_AMP_CAP:  Final[float] = 1.0
NEWS_FACTOR_SAFE_DEFAULT: Final[float] = 0.5       # default when executor unreachable

# Strategy parameter bounds — tighter than Opus's own bounds (which were 0.3–2.0).
# Opus may propose within its bounds; L0 clamps to these on read.
POSITION_MULTIPLIER_MIN: Final[float] = 0.3
POSITION_MULTIPLIER_MAX: Final[float] = 1.5

# ── VALIDATION HELPERS ────────────────────────────────────────────────────
# Every L0 reader MUST call the appropriate validator. Raising at boundary
# is the contract that makes invariants real.

def validate_position_multiplier(value: Any, source: str = "unknown") -> float:
    """Re-validate position_multiplier on read. Raise on out-of-bounds."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise LayerZeroViolation(
            reason=f"position_multiplier not numeric: {value!r}",
            source_module=source,
            recoverable=False,
            context={"value": repr(value)},
        )
    if not (POSITION_MULTIPLIER_MIN <= v <= POSITION_MULTIPLIER_MAX):
        raise LayerZeroViolation(
            reason=(
                f"position_multiplier {v:.4f} outside L0 bounds "
                f"[{POSITION_MULTIPLIER_MIN}, {POSITION_MULTIPLIER_MAX}]"
            ),
            source_module=source,
            recoverable=False,
            context={
                "value": v,
                "min": POSITION_MULTIPLIER_MIN,
                "max": POSITION_MULTIPLIER_MAX,
            },
        )
    return v


def validate_size_against_capital(amount_usd: float, capital: float, source: str = "unknown") -> None:
    """Raise if computed position size exceeds the absolute cap."""
    try:
        amt = float(amount_usd)
        cap = float(capital)
    except (TypeError, ValueError):
        raise LayerZeroViolation(
            reason=f"non-numeric size or capital: amount={amount_usd!r} capital={capital!r}",
            source_module=source,
            recoverable=False,
        )
    if cap <= 0:
        raise LayerZeroViolation(
            reason=f"capital not positive: {cap}",
            source_module=source,
            recoverable=False,
            context={"capital": cap},
        )
    pct = amt / cap
    if pct > ABSOLUTE_SIZE_CAP_PCT:
        raise LayerZeroViolation(
            reason=(
                f"position size ${amt:.2f} = {pct:.4f} of capital ${cap:.2f} "
                f"exceeds L0 cap {ABSOLUTE_SIZE_CAP_PCT}"
            ),
            source_module=source,
            recoverable=True,   # this is recoverable — caller can clamp
            context={"amount_usd": amt, "capital": cap, "pct": pct},
        )


def cap_news_factor(value: Any, source: str = "unknown") -> float:
    """
    Clamp news factor to L0 cap. News may REDUCE size (returning < 1.0)
    but may NEVER raise size above ABSOLUTE_NEWS_AMP_CAP.
    Returns NEWS_FACTOR_SAFE_DEFAULT on parse failure.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        log.warning(
            "L0: news_factor non-numeric from %s (%r) — using safe default %.2f",
            source, value, NEWS_FACTOR_SAFE_DEFAULT,
        )
        return NEWS_FACTOR_SAFE_DEFAULT
    if v != v:  # NaN
        return NEWS_FACTOR_SAFE_DEFAULT
    if v > ABSOLUTE_NEWS_AMP_CAP:
        log.warning(
            "L0: news_factor %.4f from %s clamped to cap %.4f",
            v, source, ABSOLUTE_NEWS_AMP_CAP,
        )
        return ABSOLUTE_NEWS_AMP_CAP
    if v < 0.0:
        return 0.0
    return v


# ── KERNEL HASH ───────────────────────────────────────────────────────────
def _compute_module_hash() -> str:
    """SHA-256 of this source file. Logged at boot to detect tampering."""
    try:
        with open(__file__, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception as e:
        log.error("safety_kernel: cannot compute self-hash: %s", e)
        return "unknown"


KERNEL_HASH: Final[str] = _compute_module_hash()


def log_kernel_boot() -> None:
    """Call once at boot. Logs version, hash, and headline invariants."""
    log.info(
        "L0 SAFETY KERNEL loaded | version=%s hash=%s "
        "max_risk=%.4f max_dd=%.4f size_cap=%.4f news_cap=%.4f "
        "max_positions=%d capital_floor=$%.0f",
        KERNEL_VERSION, KERNEL_HASH[:16],
        MAX_RISK_PER_TRADE, MAX_DAILY_DRAWDOWN,
        ABSOLUTE_SIZE_CAP_PCT, ABSOLUTE_NEWS_AMP_CAP,
        MAX_POSITIONS_TOTAL, CAPITAL_FLOOR_USD,
    )


# ── MUTATION BLOCK ────────────────────────────────────────────────────────
# Replace this module in sys.modules with an immutable variant. After this
# point, `safety_kernel.MAX_RISK_PER_TRADE = 0.10` raises AttributeError.
class _ImmutableKernelModule(ModuleType):
    """ModuleType subclass that refuses mutation of public attributes."""
    _frozen: bool = False

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False) and not name.startswith("_"):
            raise AttributeError(
                f"safety_kernel.{name} is L0-immutable; "
                f"changes require KERNEL_VERSION bump and redeploy"
            )
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError(
                f"safety_kernel.{name} is L0-immutable; cannot delete"
            )
        super().__delattr__(name)


def _freeze_module() -> None:
    """Replace the live module object with an immutable subclass instance."""
    current = sys.modules[__name__]
    if isinstance(current, _ImmutableKernelModule):
        return
    new_mod = _ImmutableKernelModule(__name__)
    new_mod.__dict__.update(current.__dict__)
    new_mod.__dict__["_frozen"] = True
    sys.modules[__name__] = new_mod


_freeze_module()
