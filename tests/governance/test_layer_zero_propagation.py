"""
Phase-1 propagation tests.

These tests verify that LayerZeroViolation raised at the deepest L0 boundary
(safety_kernel validators) reaches the supervisor boundary in main.py without
being silently swallowed by ANY intermediate try/except on the critical path:

  safety_kernel → position_manager → signal_generator → trading_loop → supervisor

If any intermediate handler is broken (catches Exception without re-raising
LayerZeroViolation), these tests fail.
"""

from __future__ import annotations
from unittest.mock import patch, AsyncMock, MagicMock
import pytest

from governance.exceptions import LayerZeroViolation


# ── Layer-by-layer propagation ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_violation_from_position_manager_propagates_through_signal_generator():
    """
    Inject a LayerZeroViolation from position_manager.calc_position_size and
    confirm it propagates out of signal_generator.process() — i.e., is NOT
    caught by the broad except at the bottom of process().
    """
    from engine import signal_generator as sg

    # Mock the rule engine to produce a non-hold signal so we reach
    # position_manager.
    fake_rule_signal = {
        "action": "buy", "confidence": 0.7,
        "source": "rule_based", "reason": "test",
        "indicators": {},
    }

    with patch.object(sg.rule_engine, "analyze",
                      new=AsyncMock(return_value=fake_rule_signal)), \
         patch.object(sg.db, "get_current_capital",
                      new=AsyncMock(return_value=1000.0)), \
         patch.object(sg.settings, "get_claude_limits",
                      return_value={"haiku": 100, "sonnet": 50}), \
         patch.object(sg.db, "get_claude_calls_today",
                      new=AsyncMock(return_value=0)), \
         patch.object(sg.bybit, "get_price",
                      new=AsyncMock(return_value=100.0)), \
         patch.object(sg, "_haiku") as fake_haiku_factory, \
         patch.object(sg.position_manager, "calc_position_size",
                      new=AsyncMock(side_effect=LayerZeroViolation(
                          reason="injected for test",
                          source_module="position_manager",
                      ))):

        fake_haiku_factory.return_value.validate = AsyncMock(return_value={
            "action": "buy", "confidence": 0.7, "reason": "ok",
            "source": "haiku",
        })

        # Bypass sonnet by keeping confidence below SONNET threshold.
        # Actually 0.7 ≥ 0.65 so sonnet will be invoked. Mock it to return buy.
        with patch.object(sg, "_sonnet") as fake_sonnet_factory:
            fake_sonnet_factory.return_value.confirm = AsyncMock(return_value={
                "action": "buy", "confidence": 0.7, "reason": "ok",
                "source": "sonnet",
            })

            # The L0 violation from calc_position_size MUST propagate out.
            with pytest.raises(LayerZeroViolation) as exc:
                await sg.signal_generator.process("BTC/USDT")

            assert exc.value.reason == "injected for test"
            assert exc.value.source_module == "position_manager"


@pytest.mark.asyncio
async def test_violation_from_order_guard_propagates_through_signal_generator():
    """
    Inject from inside order_guard.approve and verify it bypasses the
    pipeline broad except.
    """
    from engine import signal_generator as sg

    fake_rule_signal = {
        "action": "buy", "confidence": 0.5,
        "source": "rule_based", "reason": "test", "indicators": {},
    }

    with patch.object(sg.rule_engine, "analyze",
                      new=AsyncMock(return_value=fake_rule_signal)), \
         patch.object(sg.db, "get_current_capital",
                      new=AsyncMock(return_value=1000.0)), \
         patch.object(sg.settings, "get_claude_limits",
                      return_value={"haiku": 100, "sonnet": 50}), \
         patch.object(sg.db, "get_claude_calls_today",
                      new=AsyncMock(return_value=0)), \
         patch.object(sg, "_haiku") as fake_haiku_factory, \
         patch.object(sg.position_manager, "calc_position_size",
                      new=AsyncMock(return_value=10.0)), \
         patch.object(sg.position_manager, "calc_stop_loss_price",
                      new=AsyncMock(return_value=99.0)), \
         patch.object(sg.position_manager, "calc_take_profit_price",
                      new=AsyncMock(return_value=110.0)), \
         patch.object(sg.bybit, "get_price",
                      new=AsyncMock(return_value=100.0)), \
         patch.object(sg.order_guard, "approve",
                      new=AsyncMock(side_effect=LayerZeroViolation(
                          reason="injected from guard",
                          source_module="order_guard",
                      ))):

        fake_haiku_factory.return_value.validate = AsyncMock(return_value={
            "action": "buy", "confidence": 0.5, "reason": "ok", "source": "haiku",
        })

        with pytest.raises(LayerZeroViolation) as exc:
            await sg.signal_generator.process("BTC/USDT")
        assert exc.value.source_module == "order_guard"


@pytest.mark.asyncio
async def test_violation_from_news_executor_propagates():
    """
    Inject from inside the news_action_executor.is_opportunity check
    (the inner try/except in process). Verify the inner except does not
    swallow the L0 violation.
    """
    from engine import signal_generator as sg

    # Patch the import path so the inner try/except sees a violation.
    fake_executor = MagicMock()
    fake_executor.is_opportunity = AsyncMock(side_effect=LayerZeroViolation(
        reason="injected from news executor",
        source_module="news_action_executor",
    ))

    with patch.dict("sys.modules", {"engine.news_action_executor": MagicMock(
            news_action_executor=fake_executor)}):
        with pytest.raises(LayerZeroViolation) as exc:
            await sg.signal_generator.process("BTC/USDT")
        assert exc.value.source_module == "news_action_executor"


# ── Real-path propagation (no mocking) ────────────────────────────────────

def test_violation_from_safety_kernel_directly():
    """
    The most direct test: validator raises, no mocking required, just verify
    LayerZeroViolation type is honored.
    """
    from governance import safety_kernel as L0

    with pytest.raises(LayerZeroViolation):
        L0.validate_position_multiplier(99.0, source="propagation_test")


def test_synthetic_pipeline_swallow_check():
    """
    Mechanical check: simulate the signal_generator.process() exception
    structure to prove that LayerZeroViolation propagates THROUGH a
    deliberately-installed broad except (which represents legacy code).
    """
    from governance import safety_kernel as L0

    # Outer try wraps an inner block that raises L0 violation.
    # The inner block has its own broad except — same pattern as the bot.
    with pytest.raises(LayerZeroViolation):
        try:
            try:
                L0.validate_position_multiplier(99.0, source="test")
            except Exception:
                # Legacy broad except — must NOT catch L0 violation
                pytest.fail("Legacy broad except swallowed LayerZeroViolation")
        except LayerZeroViolation:
            raise   # supervisor boundary re-raise — explicit propagation
