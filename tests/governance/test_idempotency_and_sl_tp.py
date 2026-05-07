"""
Phase-1 tests for exchange-side idempotency + SL/TP attachment.

Covers:
  - build_order_link_id is deterministic for same (pair, side, minute)
  - build_order_link_id changes across minutes
  - place_market_order accepts and forwards stop_loss / take_profit / orderLinkId
  - duplicate Bybit retCode is treated as idempotent success
  - paper-mode synthetic fills include orderLinkId echo
"""

from __future__ import annotations
from unittest.mock import patch, MagicMock, AsyncMock
import pytest

from exchange import bybit_client as bc
from exchange.bybit_client import build_order_link_id


# ── Idempotency key generation ────────────────────────────────────────────

def test_link_id_deterministic_same_minute():
    a = build_order_link_id("BTC/USDT", "buy", signal_epoch_minute=29000000)
    b = build_order_link_id("BTC/USDT", "buy", signal_epoch_minute=29000000)
    assert a == b
    assert a.startswith("cb_")
    assert len(a) == 11   # cb_ + 8 hex

def test_link_id_changes_across_minutes():
    a = build_order_link_id("BTC/USDT", "buy", signal_epoch_minute=29000000)
    b = build_order_link_id("BTC/USDT", "buy", signal_epoch_minute=29000001)
    assert a != b

def test_link_id_changes_across_pairs():
    a = build_order_link_id("BTC/USDT", "buy", signal_epoch_minute=29000000)
    b = build_order_link_id("ETH/USDT", "buy", signal_epoch_minute=29000000)
    assert a != b

def test_link_id_changes_across_sides():
    a = build_order_link_id("BTC/USDT", "buy", signal_epoch_minute=29000000)
    b = build_order_link_id("BTC/USDT", "sell", signal_epoch_minute=29000000)
    assert a != b

def test_link_id_case_insensitive_side():
    a = build_order_link_id("BTC/USDT", "Buy", signal_epoch_minute=29000000)
    b = build_order_link_id("BTC/USDT", "buy", signal_epoch_minute=29000000)
    assert a == b


# ── place_market_order paper mode ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_paper_order_includes_link_id_and_sl_tp():
    """In paper mode, synthetic fill must echo orderLinkId, stopLoss, takeProfit."""
    with patch.object(bc.settings, "PAPER_TRADE", True), \
         patch.object(bc.bybit, "get_price", new=AsyncMock(return_value=100.0)):
        result = await bc.bybit.place_market_order(
            symbol="BTC/USDT",
            side="Buy",
            qty=0.001,
            order_link_id="cb_test1234",
            stop_loss=98.0,
            take_profit=104.0,
        )
    assert result["is_paper"] is True
    assert result["orderLinkId"] == "cb_test1234"
    assert result["stopLoss"] == 98.0
    assert result["takeProfit"] == 104.0
    assert result["price"] == 100.0


@pytest.mark.asyncio
async def test_paper_order_auto_generates_link_id_if_none():
    with patch.object(bc.settings, "PAPER_TRADE", True), \
         patch.object(bc.bybit, "get_price", new=AsyncMock(return_value=100.0)):
        result = await bc.bybit.place_market_order(
            symbol="BTC/USDT", side="Buy", qty=0.001,
        )
    assert result["orderLinkId"].startswith("cb_")


# ── Live mode: forwards SL/TP to Bybit ────────────────────────────────────

@pytest.mark.asyncio
async def test_live_order_forwards_sl_tp_and_link_id_to_bybit():
    fake_http = MagicMock()
    fake_http.place_order.return_value = {
        "retCode": 0,
        "result":  {"orderId": "12345", "orderLinkId": "cb_xyz"},
        "retMsg":  "OK",
    }
    with patch.object(bc.settings, "PAPER_TRADE", False), \
         patch.object(bc.bybit, "_rest", return_value=fake_http):
        await bc.bybit.place_market_order(
            symbol="BTC/USDT", side="Buy", qty=0.001,
            order_link_id="cb_test1234",
            stop_loss=99.0, take_profit=105.0,
        )

    args, kwargs = fake_http.place_order.call_args
    assert kwargs["orderLinkId"] == "cb_test1234"
    assert kwargs["stopLoss"] == "99.0"
    assert kwargs["takeProfit"] == "105.0"
    assert kwargs["tpslMode"] == "Full"
    assert kwargs["category"] == "spot"
    assert kwargs["orderType"] == "Market"


# ── Idempotency: duplicate retCode treated as success ─────────────────────

@pytest.mark.asyncio
async def test_duplicate_link_id_returns_existing_order():
    fake_http = MagicMock()
    fake_http.place_order.return_value = {
        "retCode": 10005,    # Bybit duplicate orderLinkId code
        "result":  {},
        "retMsg":  "duplicate orderLinkId",
    }
    fake_http.get_open_orders.return_value = {
        "retCode": 0,
        "result":  {"list": [{"orderId": "ORIGINAL", "orderLinkId": "cb_dup",
                              "side": "Buy", "qty": "0.001"}]},
    }

    with patch.object(bc.settings, "PAPER_TRADE", False), \
         patch.object(bc.bybit, "_rest", return_value=fake_http):
        result = await bc.bybit.place_market_order(
            symbol="BTC/USDT", side="Buy", qty=0.001,
            order_link_id="cb_dup",
        )

    # Idempotent recovery: returns the existing order, not a new one
    assert result["orderId"] == "ORIGINAL"


@pytest.mark.asyncio
async def test_duplicate_link_id_unable_to_fetch_raises():
    """If duplicate AND we can't find the original, surface as error."""
    fake_http = MagicMock()
    fake_http.place_order.return_value = {
        "retCode": 110072,
        "result":  {},
        "retMsg":  "Order link ID already exists",
    }
    fake_http.get_open_orders.return_value = {"retCode": 0, "result": {"list": []}}
    fake_http.get_order_history.return_value = {"retCode": 0, "result": {"list": []}}

    with patch.object(bc.settings, "PAPER_TRADE", False), \
         patch.object(bc.bybit, "_rest", return_value=fake_http):
        with pytest.raises(RuntimeError, match="Duplicate"):
            await bc.bybit.place_market_order(
                symbol="BTC/USDT", side="Buy", qty=0.001,
                order_link_id="cb_orphan",
            )


# ── order_guard rejects sell on spot ──────────────────────────────────────

@pytest.mark.asyncio
async def test_order_guard_rejects_sell_on_spot():
    from engine import order_guard as og
    approved, reason = await og.order_guard.approve(
        pair="BTC/USDT", side="sell", amount_usd=10.0, capital=1000.0,
    )
    assert approved is False
    assert reason == "spot_cannot_short"


@pytest.mark.asyncio
async def test_order_guard_accepts_buy_when_state_ok():
    """Buy passes through to other checks (mocked here)."""
    from engine import order_guard as og
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)
    fake_redis.incr = AsyncMock(return_value=1)
    fake_redis.expire = AsyncMock(return_value=True)
    with patch.object(og, "redis", fake_redis), \
         patch.object(og.db, "get_open_trades", new=AsyncMock(return_value=[])):
        approved, reason = await og.order_guard.approve(
            pair="BTC/USDT", side="buy", amount_usd=10.0, capital=1000.0,
        )
    assert approved is True
    assert reason == "approved"
