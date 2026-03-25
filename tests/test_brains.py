"""
tests/test_brains.py
Unit test untuk Claude brains — fokus pada parsing response
dan fallback behavior saat API error.
Jalankan: pytest tests/test_brains.py -v
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── HaikuBrain tests ──────────────────────────────────────────────────────────

class TestHaikuBrain:

    def test_parse_valid_json(self):
        from brains.haiku_brain import HaikuBrain
        brain  = HaikuBrain.__new__(HaikuBrain)
        raw    = '{"action":"buy","confidence":0.72,"reason":"RSI oversold MACD bullish"}'
        result = brain._parse(raw)

        assert result["action"]     == "buy"
        assert result["confidence"] == 0.72
        assert result["source"]     == "haiku"

    def test_parse_json_with_markdown_fences(self):
        from brains.haiku_brain import HaikuBrain
        brain  = HaikuBrain.__new__(HaikuBrain)
        raw    = '```json\n{"action":"sell","confidence":0.65,"reason":"overbought"}\n```'
        result = brain._parse(raw)

        assert result["action"]     == "sell"
        assert result["confidence"] == 0.65

    def test_parse_invalid_action_defaults_to_hold(self):
        from brains.haiku_brain import HaikuBrain
        brain  = HaikuBrain.__new__(HaikuBrain)
        raw    = '{"action":"YOLO","confidence":0.9,"reason":"test"}'
        result = brain._parse(raw)

        assert result["action"] == "hold"

    def test_parse_confidence_clamped_to_range(self):
        from brains.haiku_brain import HaikuBrain
        brain = HaikuBrain.__new__(HaikuBrain)

        too_high = brain._parse('{"action":"buy","confidence":1.5,"reason":"test"}')
        assert too_high["confidence"] == 1.0

        too_low  = brain._parse('{"action":"buy","confidence":-0.5,"reason":"test"}')
        assert too_low["confidence"] == 0.0

    def test_parse_malformed_json_returns_hold(self):
        from brains.haiku_brain import HaikuBrain
        brain  = HaikuBrain.__new__(HaikuBrain)
        result = brain._parse("this is not json at all")

        assert result["action"]     == "hold"
        assert result["confidence"] == 0.0
        assert result["reason"]     == "parse_error"

    @pytest.mark.asyncio
    async def test_fallback_to_rule_signal_on_api_error(self):
        from brains.haiku_brain import HaikuBrain
        brain = HaikuBrain.__new__(HaikuBrain)

        rule_signal = {"action": "buy", "confidence": 0.55,
                       "reason": "RSI oversold", "source": "rule_based"}
        mock_params = MagicMock()
        mock_params.atr_no_trade_threshold = 0.8

        brain._client = MagicMock()
        brain._client.messages.create = MagicMock(
            side_effect=Exception("API error")
        )

        with patch("brains.haiku_brain.db") as mock_db:
            mock_db.get_strategy_params = AsyncMock(return_value=mock_params)
            result = await brain.validate("BTC/USDT", rule_signal, {})

        assert result == rule_signal  # Falls back to rule signal


# ── SonnetBrain tests ─────────────────────────────────────────────────────────

class TestSonnetBrain:

    def test_parse_valid_response(self):
        from brains.sonnet_brain import SonnetBrain
        brain = SonnetBrain.__new__(SonnetBrain)
        raw   = ('{"action":"buy","confidence":0.82,"reason":"Strong alignment",'
                 '"risk_reward":2.1,"timeframe_alignment":"strong"}')
        result = brain._parse(raw)

        assert result["action"]              == "buy"
        assert result["confidence"]          == 0.82
        assert result["risk_reward"]         == 2.1
        assert result["timeframe_alignment"] == "strong"
        assert result["source"]              == "sonnet"

    def test_low_risk_reward_forces_hold(self):
        from brains.sonnet_brain import SonnetBrain
        brain = SonnetBrain.__new__(SonnetBrain)
        raw   = ('{"action":"buy","confidence":0.80,"reason":"Good signal",'
                 '"risk_reward":1.2,"timeframe_alignment":"moderate"}')
        result = brain._parse(raw)

        # R/R < 1.5 should force hold
        assert result["action"]     == "hold"
        assert result["confidence"] == 0.0
        assert "risk_reward_too_low" in result["reason"]

    def test_zero_risk_reward_skips_check(self):
        from brains.sonnet_brain import SonnetBrain
        brain  = SonnetBrain.__new__(SonnetBrain)
        raw    = ('{"action":"buy","confidence":0.75,"reason":"test",'
                  '"risk_reward":0.0,"timeframe_alignment":"strong"}')
        result = brain._parse(raw)

        # R/R = 0 means not calculated — should not force hold
        assert result["action"] == "buy"

    def test_parse_malformed_returns_hold(self):
        from brains.sonnet_brain import SonnetBrain
        brain  = SonnetBrain.__new__(SonnetBrain)
        result = brain._parse("{bad json}")

        assert result["action"]     == "hold"
        assert result["confidence"] == 0.0


# ── OpusBrain tests ───────────────────────────────────────────────────────────

class TestOpusBrain:

    def test_parse_valid_evaluation(self):
        from brains.opus_brain import OpusBrain
        brain = OpusBrain.__new__(OpusBrain)
        raw   = '''{
            "summary": {
                "win_rate": 0.63,
                "total_pnl": 12.50,
                "max_drawdown": 0.08,
                "total_trades": 24,
                "sharpe_ratio": 1.24,
                "assessment": "Strong week with consistent signals."
            },
            "auto_updated": [],
            "action_required": [
                {
                    "priority": "P1",
                    "title": "Reduce SOL stop loss",
                    "problem": "SOL stop loss too tight",
                    "steps": []
                }
            ],
            "params_to_update": {
                "SOL/USDT": {"stop_loss_pct": 3.0}
            },
            "news_weights_update": {},
            "pair_recommendations": [],
            "token_cost": 0.0042
        }'''
        result = brain._parse(raw)

        assert result["summary"]["win_rate"]          == 0.63
        assert len(result["action_required"])         == 1
        assert result["action_required"][0]["priority"] == "P1"
        assert result["params_to_update"]["SOL/USDT"]["stop_loss_pct"] == 3.0

    def test_parse_malformed_returns_empty_structure(self):
        from brains.opus_brain import OpusBrain
        brain  = OpusBrain.__new__(OpusBrain)
        result = brain._parse("not valid json ...")

        assert result["summary"]           == {}
        assert result["action_required"]   == []
        assert result["params_to_update"]  == {}

    @pytest.mark.asyncio
    async def test_apply_params_only_numeric_positive(self):
        from brains.opus_brain import OpusBrain
        brain = OpusBrain.__new__(OpusBrain)

        updates = {
            "BTC/USDT": {
                "stop_loss_pct":    2.8,    # valid
                "rsi_period":       14,     # valid
                "invalid_str":      "abc",  # should be skipped
                "negative_val":    -1.0,    # should be skipped
            }
        }

        with patch("brains.opus_brain.db") as mock_db:
            mock_db.update_strategy_params = AsyncMock()
            applied = await brain._apply_param_updates(updates)

        call_args = mock_db.update_strategy_params.call_args[0]
        applied_params = call_args[1]

        assert "stop_loss_pct" in applied_params
        assert "rsi_period"    in applied_params
        assert "invalid_str"   not in applied_params
        assert "negative_val"  not in applied_params


# ── CreditMonitor tests ───────────────────────────────────────────────────────

class TestCreditMonitor:

    @pytest.mark.asyncio
    async def test_mode_normal_when_balance_high(self):
        from brains.credit_monitor import CreditMonitor
        mon = CreditMonitor.__new__(CreditMonitor)

        with patch("brains.credit_monitor.redis") as mock_redis, \
             patch("brains.credit_monitor.settings") as mock_settings:
            mock_settings.CREDIT_WARNING  = 15.0
            mock_settings.CREDIT_TOPUP    = 8.0
            mock_settings.CREDIT_CRITICAL = 3.0
            mock_redis.set = AsyncMock()

            await mon._adjust_claude_mode(balance=25.0)

            mock_redis.set.assert_called_with("claude_mode", "normal")

    @pytest.mark.asyncio
    async def test_mode_critical_on_low_balance(self):
        from brains.credit_monitor import CreditMonitor
        mon = CreditMonitor.__new__(CreditMonitor)

        with patch("brains.credit_monitor.redis") as mock_redis, \
             patch("brains.credit_monitor.settings") as mock_settings:
            mock_settings.CREDIT_WARNING  = 15.0
            mock_settings.CREDIT_TOPUP    = 8.0
            mock_settings.CREDIT_CRITICAL = 3.0
            mock_redis.set = AsyncMock()

            await mon._adjust_claude_mode(balance=2.5)

            mock_redis.set.assert_called_with("claude_mode", "haiku_only")

    @pytest.mark.asyncio
    async def test_model_not_allowed_when_off(self):
        from brains.credit_monitor import CreditMonitor
        mon = CreditMonitor.__new__(CreditMonitor)

        with patch("brains.credit_monitor.redis") as mock_redis:
            mock_redis.get = AsyncMock(return_value="off")
            result = await mon.is_model_allowed("haiku")

        assert result is False

    @pytest.mark.asyncio
    async def test_haiku_allowed_in_haiku_only_mode(self):
        from brains.credit_monitor import CreditMonitor
        mon = CreditMonitor.__new__(CreditMonitor)

        with patch("brains.credit_monitor.redis") as mock_redis:
            mock_redis.get = AsyncMock(return_value="haiku_only")

            haiku_ok  = await mon.is_model_allowed("haiku")
            sonnet_ok = await mon.is_model_allowed("sonnet")

        assert haiku_ok  is True
        assert sonnet_ok is False
