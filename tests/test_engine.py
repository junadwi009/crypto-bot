"""
tests/test_engine.py
Unit test untuk rule-based engine, order guard, circuit breaker,
dan position manager.
Jalankan: pytest tests/test_engine.py -v
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_indicators():
    """Indikator teknikal contoh untuk testing."""
    return {
        "symbol":       "BTC/USDT",
        "interval":     "15",
        "price":        84200.0,
        "price_change": 0.8,
        "rsi":          29.5,        # Oversold
        "macd":         120.5,
        "macd_signal":  80.2,
        "macd_hist":    40.3,        # Bullish
        "bb_upper":     86000.0,
        "bb_lower":     82000.0,
        "bb_mid":       84000.0,
        "atr":          850.0,
        "atr_pct":      1.01,        # Above threshold
        "volume":       1250.5,
        "volume_ratio": 1.45,        # Good volume
    }

@pytest.fixture
def neutral_indicators():
    """Indikator sideways / no signal."""
    return {
        "symbol":       "ETH/USDT",
        "interval":     "15",
        "price":        1920.0,
        "price_change": 0.1,
        "rsi":          52.0,        # Neutral
        "macd":         5.0,
        "macd_signal":  6.0,
        "macd_hist":   -1.0,
        "bb_upper":     1950.0,
        "bb_lower":     1890.0,
        "bb_mid":       1920.0,
        "atr":          15.0,
        "atr_pct":      0.78,        # Below threshold
        "volume":       500.0,
        "volume_ratio": 0.85,
    }

@pytest.fixture
def overbought_indicators():
    """Indikator overbought — sinyal sell."""
    return {
        "symbol":       "SOL/USDT",
        "interval":     "15",
        "price":        180.0,
        "price_change": 3.5,
        "rsi":          76.0,        # Overbought
        "macd":        -2.5,
        "macd_signal":  1.0,
        "macd_hist":   -3.5,        # Bearish
        "bb_upper":     178.5,
        "bb_lower":     165.0,
        "bb_mid":       171.5,
        "atr":          2.8,
        "atr_pct":      1.56,
        "volume":       8200.0,
        "volume_ratio": 1.75,
    }


# ── RuleBasedEngine tests ─────────────────────────────────────────────────────

class TestRuleBasedEngine:

    @pytest.mark.asyncio
    async def test_buy_signal_on_oversold_rsi(self, sample_indicators):
        from engine.rule_based import RuleBasedEngine

        engine = RuleBasedEngine()
        mock_params = MagicMock()
        mock_params.rsi_oversold             = 32
        mock_params.rsi_overbought           = 71
        mock_params.atr_no_trade_threshold   = 0.8

        with patch("engine.rule_based.db") as mock_db, \
             patch("engine.rule_based.market_data") as mock_md:
            mock_db.get_strategy_params = AsyncMock(return_value=mock_params)
            mock_md.get_indicators      = AsyncMock(return_value=sample_indicators)

            result = await engine.analyze("BTC/USDT")

        assert result["action"] == "buy"
        assert result["confidence"] >= 0.40
        assert "RSI oversold" in result["reason"]

    @pytest.mark.asyncio
    async def test_hold_on_low_atr(self, neutral_indicators):
        from engine.rule_based import RuleBasedEngine

        engine = RuleBasedEngine()
        mock_params = MagicMock()
        mock_params.rsi_oversold           = 32
        mock_params.rsi_overbought         = 71
        mock_params.atr_no_trade_threshold = 0.8

        with patch("engine.rule_based.db") as mock_db, \
             patch("engine.rule_based.market_data") as mock_md:
            mock_db.get_strategy_params = AsyncMock(return_value=mock_params)
            mock_md.get_indicators      = AsyncMock(return_value=neutral_indicators)

            result = await engine.analyze("ETH/USDT")

        assert result["action"] == "hold"
        assert "atr_low" in result["reason"]

    @pytest.mark.asyncio
    async def test_sell_signal_on_overbought(self, overbought_indicators):
        from engine.rule_based import RuleBasedEngine

        engine = RuleBasedEngine()
        mock_params = MagicMock()
        mock_params.rsi_oversold           = 32
        mock_params.rsi_overbought         = 71
        mock_params.atr_no_trade_threshold = 0.8

        with patch("engine.rule_based.db") as mock_db, \
             patch("engine.rule_based.market_data") as mock_md:
            mock_db.get_strategy_params = AsyncMock(return_value=mock_params)
            mock_md.get_indicators      = AsyncMock(return_value=overbought_indicators)

            result = await engine.analyze("SOL/USDT")

        assert result["action"] == "sell"
        assert result["confidence"] >= 0.40

    def test_signal_helper_returns_correct_structure(self):
        from engine.rule_based import RuleBasedEngine
        result = RuleBasedEngine._signal("buy", 0.75, "rule_based", "test reason")
        assert result["action"]     == "buy"
        assert result["confidence"] == 0.75
        assert result["source"]     == "rule_based"
        assert result["reason"]     == "test reason"


# ── OrderGuard tests ──────────────────────────────────────────────────────────

class TestOrderGuard:

    @pytest.mark.asyncio
    async def test_approve_valid_order(self):
        from engine.order_guard import OrderGuard
        guard = OrderGuard()

        with patch("engine.order_guard.redis") as mock_redis, \
             patch("engine.order_guard.db") as mock_db:
            mock_redis.get    = AsyncMock(return_value=None)
            mock_redis.incr   = AsyncMock(return_value=1)
            mock_redis.expire = AsyncMock()
            mock_db.get_open_trades = AsyncMock(return_value=[])

            ok, reason = await guard.approve("BTC/USDT", "buy", 20.0, 500.0)

        assert ok     is True
        assert reason == "approved"

    @pytest.mark.asyncio
    async def test_reject_when_bot_paused(self):
        from engine.order_guard import OrderGuard
        guard = OrderGuard()

        with patch("engine.order_guard.redis") as mock_redis:
            mock_redis.get = AsyncMock(return_value="1")  # bot_paused = true

            ok, reason = await guard.approve("BTC/USDT", "buy", 20.0, 500.0)

        assert ok     is False
        assert reason == "bot_paused"

    @pytest.mark.asyncio
    async def test_reject_position_too_large(self):
        from engine.order_guard import OrderGuard
        guard = OrderGuard()

        with patch("engine.order_guard.redis") as mock_redis, \
             patch("engine.order_guard.db") as mock_db:
            mock_redis.get    = AsyncMock(return_value=None)
            mock_redis.incr   = AsyncMock(return_value=1)
            mock_redis.expire = AsyncMock()
            mock_db.get_open_trades = AsyncMock(return_value=[])

            # $100 order with $500 capital = 20% — exceeds 5% max
            ok, reason = await guard.approve("BTC/USDT", "buy", 100.0, 500.0)

        assert ok     is False
        assert "size_too_large" in reason

    @pytest.mark.asyncio
    async def test_reject_below_capital_floor(self):
        from engine.order_guard import OrderGuard
        guard = OrderGuard()

        with patch("engine.order_guard.redis") as mock_redis, \
             patch("engine.order_guard.db") as mock_db:
            mock_redis.get    = AsyncMock(return_value=None)
            mock_redis.incr   = AsyncMock(return_value=1)
            mock_redis.expire = AsyncMock()
            mock_db.get_open_trades = AsyncMock(return_value=[])

            ok, reason = await guard.approve("BTC/USDT", "buy", 5.0, 100.0)

        assert ok     is False
        assert "capital_below_floor" in reason


# ── CircuitBreaker tests ──────────────────────────────────────────────────────

class TestCircuitBreaker:

    @pytest.mark.asyncio
    async def test_trips_on_excessive_drawdown(self):
        from engine.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker()

        with patch("engine.circuit_breaker.redis") as mock_redis, \
             patch("engine.circuit_breaker.db") as mock_db:
            mock_redis.get    = AsyncMock(return_value=None)
            mock_redis.set    = AsyncMock()
            mock_redis.setex  = AsyncMock()
            mock_redis.delete = AsyncMock()
            mock_db.log_event = AsyncMock()

            # 20% drawdown — exceeds 15% threshold
            await cb.check(capital_now=800.0, capital_start_of_day=1000.0)

            mock_redis.set.assert_any_call("circuit_breaker_tripped", "1")

    @pytest.mark.asyncio
    async def test_does_not_trip_on_small_drawdown(self):
        from engine.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker()

        with patch("engine.circuit_breaker.redis") as mock_redis, \
             patch("engine.circuit_breaker.db") as mock_db:
            mock_redis.get    = AsyncMock(return_value=None)
            mock_redis.set    = AsyncMock()
            mock_db.log_event = AsyncMock()

            # 5% drawdown — below 15% threshold
            await cb.check(capital_now=950.0, capital_start_of_day=1000.0)

            # Should NOT set circuit breaker
            for call in mock_redis.set.call_args_list:
                assert "circuit_breaker" not in str(call)

    @pytest.mark.asyncio
    async def test_is_tripped_returns_false_when_clear(self):
        from engine.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker()

        with patch("engine.circuit_breaker.redis") as mock_redis:
            mock_redis.get = AsyncMock(return_value=None)
            result = await cb.is_tripped()

        assert result is False


# ── PositionManager tests ─────────────────────────────────────────────────────

class TestPositionManager:

    @pytest.mark.asyncio
    async def test_position_size_within_bounds(self):
        from engine.position_manager import PositionManager
        pm = PositionManager()

        mock_params = MagicMock()
        mock_params.position_multiplier = 1.0

        with patch("engine.position_manager.db") as mock_db:
            mock_db.get_current_capital   = AsyncMock(return_value=500.0)
            mock_db.get_strategy_params   = AsyncMock(return_value=mock_params)

            size = await pm.calc_position_size("BTC/USDT", confidence=0.75)

        # 2% of 500 = $10, within $5–$25 range
        assert 5.0 <= size <= 25.0

    @pytest.mark.asyncio
    async def test_stop_loss_below_entry_for_buy(self):
        from engine.position_manager import PositionManager
        pm = PositionManager()

        mock_params = MagicMock()
        mock_params.stop_loss_pct = 2.2

        with patch("engine.position_manager.db") as mock_db:
            mock_db.get_strategy_params = AsyncMock(return_value=mock_params)
            sl = await pm.calc_stop_loss_price("BTC/USDT", 84200.0, "buy")

        assert sl < 84200.0
        assert sl == pytest.approx(84200.0 * (1 - 0.022), rel=1e-4)

    @pytest.mark.asyncio
    async def test_take_profit_above_entry_for_buy(self):
        from engine.position_manager import PositionManager
        pm = PositionManager()

        mock_params = MagicMock()
        mock_params.take_profit_pct = 4.5

        with patch("engine.position_manager.db") as mock_db:
            mock_db.get_strategy_params = AsyncMock(return_value=mock_params)
            tp = await pm.calc_take_profit_price("BTC/USDT", 84200.0, "buy")

        assert tp > 84200.0
        assert tp == pytest.approx(84200.0 * (1 + 0.045), rel=1e-4)
