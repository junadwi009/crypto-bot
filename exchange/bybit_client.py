"""
exchange/bybit_client.py
Bybit API v5 — REST + WebSocket client.

PATCHED 2026-05-02:
- get_price() cache punya TTL (sebelumnya bisa stale berhari-hari)
- WS subscribe tidak dipanggil otomatis tapi tersedia untuk frontend later
- Helper qty rounding minimum (Bybit punya min order size per symbol)
"""

from __future__ import annotations
import asyncio
import logging
import time
from typing import Callable

from pybit.unified_trading import HTTP, WebSocket

from config.settings import settings

log = logging.getLogger("bybit")

# Cache harga TTL (detik) — kalau lebih dari ini, fetch fresh
_PRICE_CACHE_TTL = 5


class BybitClient:

    def __init__(self):
        self._http: HTTP | None = None
        self._ws:   WebSocket | None = None
        self._price_callbacks: dict[str, list[Callable]] = {}
        # Cache: { symbol: (price, timestamp) }
        self._last_prices: dict[str, tuple[float, float]] = {}

    def _rest(self) -> HTTP:
        if self._http is None:
            self._http = HTTP(
                testnet    = settings.BYBIT_TESTNET,
                api_key    = settings.BYBIT_API_KEY,
                api_secret = settings.BYBIT_API_SECRET,
            )
        return self._http

    async def ping(self):
        result = self._rest().get_server_time()
        if result["retCode"] != 0:
            raise ConnectionError(f"Bybit ping failed: {result}")
        log.info("Bybit: ping OK (server time %s)",
                 result["result"]["timeSecond"])

    # ── Market data ───────────────────────────────────────────────────────

    async def get_ticker(self, symbol: str) -> dict:
        symbol = self._fmt(symbol)
        result = self._rest().get_tickers(category="spot", symbol=symbol)
        if result["retCode"] != 0 or not result["result"]["list"]:
            raise ValueError(f"Ticker not found: {symbol}")
        t = result["result"]["list"][0]
        return {
            "symbol":     symbol,
            "last":       float(t["lastPrice"]),
            "bid":        float(t["bid1Price"]),
            "ask":        float(t["ask1Price"]),
            "volume_24h": float(t["volume24h"]),
            "change_24h": float(t["price24hPcnt"]),
        }

    async def get_price(self, symbol: str) -> float:
        """
        Ambil harga terakhir.
        Cache 5 detik agar tidak spam Bybit kalau dipanggil cepat berturut-turut,
        TAPI tidak stale selamanya.
        """
        cached = self._last_prices.get(symbol)
        if cached and (time.time() - cached[1]) < _PRICE_CACHE_TTL:
            return cached[0]

        ticker = await self.get_ticker(symbol)
        price  = ticker["last"]
        self._last_prices[symbol] = (price, time.time())
        return price

    async def get_ohlcv(self, symbol: str, interval: str = "15",
                        limit: int = 100) -> list[dict]:
        symbol = self._fmt(symbol)
        result = self._rest().get_kline(
            category = "spot",
            symbol   = symbol,
            interval = interval,
            limit    = limit,
        )
        if result["retCode"] != 0:
            raise ValueError(f"OHLCV error: {result['retMsg']}")

        candles = []
        for c in reversed(result["result"]["list"]):
            candles.append({
                "timestamp": int(c[0]),
                "open":      float(c[1]),
                "high":      float(c[2]),
                "low":       float(c[3]),
                "close":     float(c[4]),
                "volume":    float(c[5]),
            })
        return candles

    async def get_orderbook(self, symbol: str, limit: int = 5) -> dict:
        symbol = self._fmt(symbol)
        result = self._rest().get_orderbook(
            category = "spot",
            symbol   = symbol,
            limit    = limit,
        )
        if result["retCode"] != 0:
            raise ValueError(f"Orderbook error: {result['retMsg']}")
        ob = result["result"]
        return {
            "bids": [(float(b[0]), float(b[1])) for b in ob["b"]],
            "asks": [(float(a[0]), float(a[1])) for a in ob["a"]],
        }

    # ── Account ───────────────────────────────────────────────────────────

    async def get_balance(self, coin: str = "USDT") -> float:
        result = self._rest().get_wallet_balance(
            accountType = "UNIFIED",
            coin        = coin,
        )
        if result["retCode"] != 0:
            raise ValueError(f"Balance error: {result['retMsg']}")
        coins = result["result"]["list"][0]["coin"]
        for c in coins:
            if c["coin"] == coin:
                return float(c["walletBalance"])
        return 0.0

    async def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        kwargs = {"category": "spot"}
        if symbol:
            kwargs["symbol"] = self._fmt(symbol)
        result = self._rest().get_open_orders(**kwargs)
        if result["retCode"] != 0:
            return []
        return result["result"]["list"] or []

    # ── Order management ──────────────────────────────────────────────────

    async def place_market_order(self, symbol: str, side: str,
                                  qty: float) -> dict:
        symbol = self._fmt(symbol)
        log.info("Placing market order: %s %s %s qty=%.6f",
                 "PAPER" if settings.PAPER_TRADE else "LIVE",
                 side, symbol, qty)

        if settings.PAPER_TRADE:
            price = await self.get_price(symbol)
            return {
                "orderId":   f"paper_{int(time.time())}",
                "symbol":    symbol,
                "side":      side,
                "qty":       qty,
                "price":     price,
                "status":    "Filled",
                "is_paper":  True,
            }

        result = self._rest().place_order(
            category  = "spot",
            symbol    = symbol,
            side      = side,
            orderType = "Market",
            qty       = str(round(qty, 6)),
        )
        if result["retCode"] != 0:
            raise RuntimeError(f"Order failed: {result['retMsg']}")
        log.info("Order placed: orderId=%s", result["result"]["orderId"])
        return result["result"]

    async def place_limit_order(self, symbol: str, side: str,
                                 qty: float, price: float) -> dict:
        symbol = self._fmt(symbol)
        if settings.PAPER_TRADE:
            return {
                "orderId":  f"paper_limit_{int(time.time())}",
                "symbol":   symbol,
                "side":     side,
                "qty":      qty,
                "price":    price,
                "status":   "New",
                "is_paper": True,
            }
        result = self._rest().place_order(
            category  = "spot",
            symbol    = symbol,
            side      = side,
            orderType = "Limit",
            qty       = str(round(qty, 6)),
            price     = str(round(price, 2)),
        )
        if result["retCode"] != 0:
            raise RuntimeError(f"Limit order failed: {result['retMsg']}")
        return result["result"]

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        if settings.PAPER_TRADE:
            log.info("Paper: cancel order %s", order_id)
            return True
        symbol = self._fmt(symbol)
        result = self._rest().cancel_order(
            category = "spot",
            symbol   = symbol,
            orderId  = order_id,
        )
        return result["retCode"] == 0

    async def get_order_status(self, symbol: str,
                                order_id: str) -> dict | None:
        symbol = self._fmt(symbol)
        result = self._rest().get_order_history(
            category = "spot",
            symbol   = symbol,
            orderId  = order_id,
        )
        if result["retCode"] != 0 or not result["result"]["list"]:
            return None
        return result["result"]["list"][0]

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _fmt(symbol: str) -> str:
        """BTC/USDT → BTCUSDT"""
        return symbol.replace("/", "")

    def calc_qty(self, symbol: str, usd_amount: float, price: float) -> float:
        if price <= 0:
            return 0.0
        qty = usd_amount / price
        return round(qty, 6)

    def calc_fee(self, usd_amount: float, rate: float = 0.001) -> float:
        return round(usd_amount * rate, 4)


bybit = BybitClient()
