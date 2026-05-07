"""
Phase-1 integration tests for governance/safety_kernel.

Covers:
  - Constants are immutable (mutation blocked at module level)
  - Constants mirror prior settings.py values exactly (no risk-profile drift)
  - Validators raise LayerZeroViolation on out-of-bounds inputs
  - News factor cap enforced (cannot raise size above 1.0)
  - Module hash is computed and reproducible
"""

from __future__ import annotations
import hashlib
import pytest

from governance import safety_kernel as L0
from governance.exceptions import LayerZeroViolation


# ── Constants mirror prior values ─────────────────────────────────────────

def test_max_risk_per_trade_unchanged():
    """Council mandate: enforcement migration introduces NO risk-profile change."""
    assert L0.MAX_RISK_PER_TRADE == 0.02

def test_max_daily_drawdown_unchanged():
    assert L0.MAX_DAILY_DRAWDOWN == 0.15

def test_size_cap_unchanged():
    assert L0.ABSOLUTE_SIZE_CAP_PCT == 0.05

def test_min_order_unchanged():
    assert L0.ABSOLUTE_MIN_ORDER_USD == 5.0

def test_capital_floor_unchanged():
    assert L0.CAPITAL_FLOOR_USD == 150.0

def test_max_positions_unchanged():
    assert L0.MAX_POSITIONS_TOTAL == 3
    assert L0.MAX_POSITIONS_PER_PAIR == 2

def test_max_orders_per_min_unchanged():
    assert L0.MAX_ORDERS_PER_MIN == 3

def test_news_amp_cap_is_one():
    """Council mandate M5: news may reduce, never amplify."""
    assert L0.ABSOLUTE_NEWS_AMP_CAP == 1.0

def test_news_safe_default():
    """When executor unreachable, default to reduce (safe direction)."""
    assert L0.NEWS_FACTOR_SAFE_DEFAULT == 0.5


# ── Mutation block ────────────────────────────────────────────────────────

def test_constant_mutation_blocked():
    """Direct assignment to a kernel constant must raise AttributeError."""
    with pytest.raises(AttributeError, match="L0-immutable"):
        L0.MAX_RISK_PER_TRADE = 0.10

def test_constant_deletion_blocked():
    with pytest.raises(AttributeError, match="L0-immutable"):
        del L0.MAX_RISK_PER_TRADE

def test_new_attribute_addition_blocked():
    """Adding a new public attribute is also blocked (kernel surface is fixed)."""
    with pytest.raises(AttributeError):
        L0.NEW_BACKDOOR = 999


# ── Position multiplier validator ─────────────────────────────────────────

def test_validate_position_multiplier_in_bounds():
    assert L0.validate_position_multiplier(1.0, source="test") == 1.0
    assert L0.validate_position_multiplier(0.3, source="test") == 0.3
    assert L0.validate_position_multiplier(1.5, source="test") == 1.5

def test_validate_position_multiplier_above_max_raises():
    with pytest.raises(LayerZeroViolation) as exc:
        L0.validate_position_multiplier(2.0, source="test")
    assert "outside L0 bounds" in exc.value.reason
    assert exc.value.source_module == "test"
    assert exc.value.layer == "L0"
    assert exc.value.recoverable is False

def test_validate_position_multiplier_below_min_raises():
    with pytest.raises(LayerZeroViolation):
        L0.validate_position_multiplier(0.1, source="test")

def test_validate_position_multiplier_non_numeric_raises():
    with pytest.raises(LayerZeroViolation):
        L0.validate_position_multiplier("not a number", source="test")

def test_validate_position_multiplier_none_raises():
    with pytest.raises(LayerZeroViolation):
        L0.validate_position_multiplier(None, source="test")


# ── Size validator ────────────────────────────────────────────────────────

def test_validate_size_within_cap():
    L0.validate_size_against_capital(amount_usd=10.0, capital=1000.0, source="test")  # 1% — OK

def test_validate_size_at_cap():
    L0.validate_size_against_capital(amount_usd=50.0, capital=1000.0, source="test")  # 5% — OK

def test_validate_size_above_cap_raises():
    with pytest.raises(LayerZeroViolation) as exc:
        L0.validate_size_against_capital(amount_usd=60.0, capital=1000.0, source="test")
    assert "exceeds L0 cap" in exc.value.reason

def test_validate_size_zero_capital_raises():
    with pytest.raises(LayerZeroViolation):
        L0.validate_size_against_capital(amount_usd=10.0, capital=0.0, source="test")


# ── News factor cap ───────────────────────────────────────────────────────

def test_news_factor_below_cap_passes():
    assert L0.cap_news_factor(0.5, source="test") == 0.5
    assert L0.cap_news_factor(0.7, source="test") == 0.7
    assert L0.cap_news_factor(1.0, source="test") == 1.0

def test_news_factor_above_cap_clamped():
    """Council mandate M5: news cannot raise size above 1.0."""
    assert L0.cap_news_factor(1.3, source="test") == 1.0
    assert L0.cap_news_factor(2.0, source="test") == 1.0
    assert L0.cap_news_factor(99.0, source="test") == 1.0

def test_news_factor_negative_clamped_to_zero():
    assert L0.cap_news_factor(-0.5, source="test") == 0.0

def test_news_factor_non_numeric_safe_default():
    assert L0.cap_news_factor("garbage", source="test") == 0.5
    assert L0.cap_news_factor(None, source="test") == 0.5

def test_news_factor_nan_safe_default():
    assert L0.cap_news_factor(float("nan"), source="test") == 0.5


# ── Module hash ───────────────────────────────────────────────────────────

def test_kernel_hash_is_sha256():
    assert len(L0.KERNEL_HASH) == 64
    assert all(c in "0123456789abcdef" for c in L0.KERNEL_HASH)

def test_kernel_hash_matches_file():
    """Hash logged at boot must match the actual source file."""
    import governance.safety_kernel as mod
    with open(mod.__file__, "rb") as f:
        expected = hashlib.sha256(f.read()).hexdigest()
    assert L0.KERNEL_HASH == expected


# ── LayerZeroViolation type contract ──────────────────────────────────────

def test_violation_inherits_base_exception_not_exception():
    """Critical: LayerZeroViolation must NOT be a subclass of Exception,
    so legacy 'except Exception:' blocks cannot suppress it."""
    assert issubclass(LayerZeroViolation, BaseException)
    assert not issubclass(LayerZeroViolation, Exception)

def test_violation_bypasses_except_exception():
    """Verify directly: the propagation guarantee depends on this."""
    caught_by_exception = False
    try:
        try:
            raise LayerZeroViolation(reason="test", source_module="t")
        except Exception:
            caught_by_exception = True
    except LayerZeroViolation:
        pass
    assert not caught_by_exception, \
        "LayerZeroViolation was caught by 'except Exception' — propagation guarantee broken"

def test_violation_str_is_single_line():
    v = LayerZeroViolation(
        reason="multi\nline\nreason\nshould\nflatten",
        source_module="m",
    )
    assert "\n" not in str(v)

def test_violation_context_json_serializable():
    v = LayerZeroViolation(
        reason="ctx test",
        source_module="m",
        context={"value": 1.5, "min": 0.3, "max": 1.5},
    )
    js = v.context_json()
    assert "value" in js
    assert "1.5" in js
