"""
exchange/bybit_client.py
Bybit API v5 — REST + WebSocket client.

PATCHED 2026-05-07 (Phase 1 — L0 kernel migration):
- place_market_order accepts orderLinkId; deterministic generation via
  build_order_link_id(pair, side, signal_epoch_minute) so a retry within
  the same minute window collapses to the original order. Bybit's duplicate
  rejection (retCode 10005 / 110072 / "duplicate") is treated as success
  and original order metadata is fetched.
- 1-minute deterministic bucket chosen to match the 30-second tick cadence
  in trading_loop. Two ticks within 60s sharing the same signal will not
  result in two orders.
- LayerZeroViolation is NOT caught here (BaseException subclass); it
  propagates if a downstream call (e.g., into safety_kernel) raises one.

PATCHED 2026-05-02 (prior):
- get_price() cache punya TTL (sebelumnya bisa stale berhari-hari)
- WS subscribe tidak dipanggil otomatis tapi tersedia untuk frontend later
- Helper qty rounding minimum (Bybit punya min order size per symbol)
"""

from __future__ import annotations
import asyncio
import hashlib
import logging
import time
from typing import Callable

from pybit.unified_trading import HTTP, WebSocket

from config.settings import settings

log = logging.getLogger("bybit")

# Cache harga TTL (detik) — kalau lebih dari ini, fetch fresh
_PRICE_CACHE_TTL = 5

# Idempotency window — collapse retries within the same minute bucket.
# Matches trading_loop's ~30s tick cadence.
ORDER_LINK_ID_BUCKET_SECONDS = 60

# Bybit duplicate-order error codes. Empirically observed:
#   10005  — "param error: duplicate orderLinkId"
#   110072 — "Order link ID already exists"
# We treat any of these as "the original is ours, fetch and return."
BYBIT_DUPLICATE_RETCODES = {10005, 110072}


def build_order_link_id(pair: str, side: str, signal_epoch_minute: int | None = None) -> str:
    """
    Deterministic order link id.

    Same (pair, side, minute) → same id. Bybit will reject the duplicate
    and we recover by fetching the original. This is the idempotency
    contract: a network-timeout retry MUST NOT result in two fills.

    Format: cb_<8-hex-of-sha256(pair|side|minute)> — 11 chars total,
    safe for Bybit's 36-char orderLinkId limit.
    """
    if signal_epoch_minute is None:
        signal_epoch_minute = int(time.time()) // ORDER_LINK_ID_BUCKET_SECONDS
    payload = f"{pair}|{side.lower()}|{signal_epoch_minute}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:8]
    return f"cb_{digest}"


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
                                  qty: float,
                                  order_link_id: str | None = None,
                                  stop_loss: float | None = None,
                                  take_profit: float | None = None) -> dict:
        """
        Place a spot market order with idempotency + optional exchange-side
        SL/TP attached.

        Args:
            symbol:        e.g. "BTC/USDT"
            side:          "Buy" or "Sell"
            qty:           base-asset quantity
            order_link_id: deterministic idempotency key. If None, computed
                           from (symbol, side, current minute). Caller MUST
                           supply this if it wants tighter retry collapsing.
            stop_loss:     optional exchange-side stop-loss price
            take_profit:   optional exchange-side take-profit price

        Returns Bybit's `result` dict on success, or a synthetic dict in
        paper mode. On Bybit duplicate-rejection, fetches and returns the
        existing order so the caller sees idempotent success.
        """
        symbol = self._fmt(symbol)

        # Idempotency key — deterministic if not supplied.
        if order_link_id is None:
            # Note: caller-side build_order_link_id uses the un-formatted pair
            # (e.g., "BTC/USDT") to keep it human-readable. Replicate here
            # using the un-formatted version reconstructed from BTCUSDT.
            # For paper/test we just use symbol; collisions don't matter.
            order_link_id = build_order_link_id(symbol, side)

        log.info(
            "Placing market order: %s %s %s qty=%.6f link_id=%s sl=%s tp=%s",
            "PAPER" if settings.PAPER_TRADE else "LIVE",
            side, symbol, qty, order_link_id, stop_loss, take_profit,
        )

        if settings.PAPER_TRADE:
            price = await self.get_price(symbol)
            return {
                "orderId":     f"paper_{order_link_id}_{int(time.time())}",
                "orderLinkId": order_link_id,
                "symbol":      symbol,
                "side":        side,
                "qty":         qty,
                "price":       price,
                "status":      "Filled",
                "stopLoss":    stop_loss,
                "takeProfit":  take_profit,
                "is_paper":    True,
            }

        kwargs = {
            "category":    "spot",
            "symbol":      symbol,
            "side":        side,
            "orderType":   "Market",
            "qty":         str(round(qty, 6)),
            "orderLinkId": order_link_id,
        }
        # Bybit spot SL/TP requires tpslMode + at least one of stopLoss/takeProfit
        if stop_loss is not None or take_profit is not None:
            kwargs["tpslMode"] = "Full"
            if stop_loss is not None:
                kwargs["stopLoss"] = str(round(float(stop_loss), 8))
            if take_profit is not None:
                kwargs["takeProfit"] = str(round(float(take_profit), 8))

        result = self._rest().place_order(**kwargs)

        # Idempotency recovery: duplicate orderLinkId means an earlier call
        # already succeeded. Fetch the existing order and return as success.
        ret_code = int(result.get("retCode", -1))
        if ret_code in BYBIT_DUPLICATE_RETCODES:
            log.warning(
                "Duplicate orderLinkId %s rejected by Bybit (retCode=%d) — "
                "treating as idempotent success, fetching original",
                order_link_id, ret_code,
            )
            existing = await self._get_order_by_link_id(symbol, order_link_id)
            if existing:
                return existing
            # Cannot find original — surface the duplicate as an error so
            # caller does not assume success without a confirmed fill.
            raise RuntimeError(
                f"Duplicate orderLinkId {order_link_id} but cannot fetch "
                f"original: {result.get('retMsg')}"
            )

        if ret_code != 0:
            raise RuntimeError(
                f"Order failed (retCode={ret_code}): {result.get('retMsg')}"
            )

        order_data = result["result"]
        # Echo orderLinkId back even if Bybit's response doesn't (defensive)
        if "orderLinkId" not in order_data:
            order_data["orderLinkId"] = order_link_id
        log.info(
            "Order placed: orderId=%s linkId=%s",
            order_data.get("orderId"), order_link_id,
        )
        return order_data

    async def _get_order_by_link_id(self, symbol: str,
                                     order_link_id: str) -> dict | None:
        """
        Look up an order by its orderLinkId. Used in idempotent recovery
        after a duplicate-rejection.
        """
        try:
            result = self._rest().get_open_orders(
                category    = "spot",
                symbol      = symbol,
                orderLinkId = order_link_id,
            )
            if result.get("retCode") == 0 and result["result"].get("list"):
                return result["result"]["list"][0]
        except Exception as e:
            log.error("Failed to fetch order by linkId %s: %s", order_link_id, e)
        # Try order history as fallback (filled orders are no longer "open")
        try:
            result = self._rest().get_order_history(
                category    = "spot",
                symbol      = symbol,
                orderLinkId = order_link_id,
            )
            if result.get("retCode") == 0 and result["result"].get("list"):
                return result["result"]["list"][0]
        except Exception as e:
            log.error("Failed to fetch order history by linkId %s: %s", order_link_id, e)
        return None

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
