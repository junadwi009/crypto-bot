"""
Phase-2 test for signal_generator → orchestrator wiring.

Council constraint 18: every pipeline invocation produces an
orchestrator_decisions row, INCLUDING:
  - rule-hold
  - haiku-reject
  - guard-reject
  - exception path

Without this, the Phase-3 promotion dataset is incomplete.
"""

from __future__ import annotations
from unittest.mock import patch, AsyncMock, MagicMock
import pytest

from engine import signal_generator as sg


@pytest.mark.asyncio
async def test_rule_hold_writes_decision_row():
    """Rule-engine hold must still produce a decision row."""
    captured = []

    async def capture_evaluate(**kwargs):
        captured.append(kwargs)
        # Return a fake decision so process() can continue
        return MagicMock(final_action="hold", layer_vetoes=[], size_usd=0.0)

    fake_rule = AsyncMock(return_value={
        "action": "hold", "confidence": 0.0,
        "source": "rule_based", "reason": "atr_low",
        "indicators": {},
    })

    with patch.object(sg.rule_engine, "analyze", new=fake_rule), \
         patch.object(sg.orchestrator, "evaluate", new=capture_evaluate):
        await sg.signal_generator.process("BTC/USDT")

    assert len(captured) == 1
    assert captured[0]["pair"] == "BTC/USDT"
    assert captured[0]["requested_action"] == "hold"
    assert captured[0]["pipeline_state"]["stage_reached"] == "rule"
    assert captured[0]["pipeline_state"]["rule_result"]["action"] == "hold"


@pytest.mark.asyncio
async def test_guard_reject_writes_decision_row():
    """When guard rejects, decision row must still be written."""
    captured = []

    async def capture_evaluate(**kwargs):
        captured.append(kwargs)
        return MagicMock(final_action="hold", layer_vetoes=[], size_usd=0.0)

    fake_rule = AsyncMock(return_value={
        "action": "buy", "confidence": 0.7,
        "source": "rule_based", "reason": "RSI oversold",
        "indicators": {},
    })

    with patch.object(sg.rule_engine, "analyze", new=fake_rule), \
         patch.object(sg.db, "get_current_capital",
                      new=AsyncMock(return_value=1000.0)), \
         patch.object(sg.settings, "get_claude_limits",
                      return_value={"haiku": 100, "sonnet": 50}), \
         patch.object(sg.db, "get_claude_calls_today",
                      new=AsyncMock(return_value=0)), \
         patch.object(sg, "_haiku") as fake_haiku_factory, \
         patch.object(sg, "_sonnet") as fake_sonnet_factory, \
         patch.object(sg.bybit, "get_price",
                      new=AsyncMock(return_value=100.0)), \
         patch.object(sg.position_manager, "calc_position_size",
                      new=AsyncMock(return_value=10.0)), \
         patch.object(sg.position_manager, "calc_stop_loss_price",
                      new=AsyncMock(return_value=99.0)), \
         patch.object(sg.position_manager, "calc_take_profit_price",
                      new=AsyncMock(return_value=110.0)), \
         patch.object(sg.order_guard, "approve",
                      new=AsyncMock(return_value=(False, "rate_limit_4_orders"))), \
         patch.object(sg.orchestrator, "evaluate", new=capture_evaluate):

        fake_haiku_factory.return_value.validate = AsyncMock(return_value={
            "action": "buy", "confidence": 0.7, "reason": "ok", "source": "haiku",
        })
        fake_sonnet_factory.return_value.confirm = AsyncMock(return_value={
            "action": "buy", "confidence": 0.7, "reason": "ok", "source": "sonnet",
        })

        await sg.signal_generator.process("BTC/USDT")

    assert len(captured) == 1
    assert captured[0]["requested_action"] == "hold"
    assert captured[0]["pipeline_state"]["stage_reached"] == "guard"
    assert captured[0]["pipeline_state"]["guard_state"]["approved"] is False


@pytest.mark.asyncio
async def test_haiku_hold_writes_decision_row():
    captured = []

    async def capture_evaluate(**kwargs):
        captured.append(kwargs)
        return MagicMock(final_action="hold", layer_vetoes=[], size_usd=0.0)

    fake_rule = AsyncMock(return_value={
        "action": "buy", "confidence": 0.6, "source": "rule_based",
        "reason": "test", "indicators": {},
    })

    with patch.object(sg.rule_engine, "analyze", new=fake_rule), \
         patch.object(sg.db, "get_current_capital", new=AsyncMock(return_value=1000.0)), \
         patch.object(sg.settings, "get_claude_limits",
                      return_value={"haiku": 100, "sonnet": 50}), \
         patch.object(sg.db, "get_claude_calls_today", new=AsyncMock(return_value=0)), \
         patch.object(sg, "_haiku") as fake_haiku_factory, \
         patch.object(sg.orchestrator, "evaluate", new=capture_evaluate):

        fake_haiku_factory.return_value.validate = AsyncMock(return_value={
            "action": "hold", "confidence": 0.0, "reason": "rule too weak",
            "source": "haiku",
        })

        await sg.signal_generator.process("BTC/USDT")

    assert len(captured) == 1
    assert captured[0]["pipeline_state"]["stage_reached"] == "haiku"


@pytest.mark.asyncio
async def test_exception_path_attempts_to_record():
    """When an exception occurs in the pipeline, attempt to record before
    propagating. The audit row may fail to write but the attempt is what
    matters for the unconditional-coverage contract."""
    record_calls = []

    async def capture_evaluate(**kwargs):
        record_calls.append(kwargs)
        return MagicMock(final_action="hold", layer_vetoes=[], size_usd=0.0)

    fake_rule = AsyncMock(side_effect=ValueError("synthetic rule failure"))

    with patch.object(sg.rule_engine, "analyze", new=fake_rule), \
         patch.object(sg.orchestrator, "evaluate", new=capture_evaluate):
        await sg.signal_generator.process("BTC/USDT")

    # Even on rule_engine exception, a decision-record attempt was made
    assert len(record_calls) == 1
    assert record_calls[0]["pipeline_state"]["stage_reached"].startswith("exception:")
