"""
exchange/market_data.py
Ambil dan cache market data untuk dipakai rule-based engine.
"""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime

import pandas as pd
import pandas_ta_classic as ta

from exchange.bybit_client import bybit

log = logging.getLogger("market_data")

# Cache sederhana — simpan OHLCV terakhir per pair
_cache: dict[str, dict] = {}
_cache_ttl = 60  # detik


class MarketData:

    async def get_candles(self, symbol: str, interval: str = "15",
                          limit: int = 100) -> pd.DataFrame:
        """
        Ambil OHLCV sebagai DataFrame pandas.
        Di-cache 60 detik agar tidak spam Bybit API.
        """
        cache_key = f"{symbol}_{interval}"
        cached = _cache.get(cache_key)

        if cached and (datetime.utcnow().timestamp() - cached["ts"]) < _cache_ttl:
            return cached["df"]

        raw = await bybit.get_ohlcv(symbol, interval, limit)
        df = pd.DataFrame(raw)
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)

        _cache[cache_key] = {"df": df, "ts": datetime.utcnow().timestamp()}
        return df

    async def get_indicators(self, symbol: str, interval: str = "15") -> dict:
        """
        Hitung semua indikator teknikal sekaligus.
        Return dict siap pakai untuk rule_based engine.
        """
        df = await self.get_candles(symbol, interval, limit=100)

        # RSI
        rsi_series = ta.rsi(df["close"], length=14)
        rsi = float(rsi_series.iloc[-1]) if rsi_series is not None else 50.0

        # MACD
        macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
        macd        = float(macd_df["MACD_12_26_9"].iloc[-1])   if macd_df is not None else 0.0
        macd_signal = float(macd_df["MACDs_12_26_9"].iloc[-1])  if macd_df is not None else 0.0
        macd_hist   = float(macd_df["MACDh_12_26_9"].iloc[-1])  if macd_df is not None else 0.0

        # Bollinger Bands
        bb_df = ta.bbands(df["close"], length=20, std=2)
        bb_upper = float(bb_df["BBU_20_2.0"].iloc[-1]) if bb_df is not None else 0.0
        bb_lower = float(bb_df["BBL_20_2.0"].iloc[-1]) if bb_df is not None else 0.0
        bb_mid   = float(bb_df["BBM_20_2.0"].iloc[-1]) if bb_df is not None else 0.0

        # ATR
        atr_series = ta.atr(df["high"], df["low"], df["close"], length=14)
        atr = float(atr_series.iloc[-1]) if atr_series is not None else 0.0
        atr_pct = (atr / float(df["close"].iloc[-1])) * 100 if float(df["close"].iloc[-1]) > 0 else 0

        # Volume ratio (volume sekarang vs rata-rata 20 candle)
        avg_vol    = float(df["volume"].rolling(20).mean().iloc[-1])
        curr_vol   = float(df["volume"].iloc[-1])
        vol_ratio  = curr_vol / avg_vol if avg_vol > 0 else 1.0

        # Harga terakhir
        current_price = float(df["close"].iloc[-1])
        prev_price    = float(df["close"].iloc[-2])
        price_change  = ((current_price - prev_price) / prev_price) * 100

        return {
            "symbol":        symbol,
            "interval":      interval,
            "price":         round(current_price, 8),
            "price_change":  round(price_change, 4),
            "rsi":           round(rsi, 2),
            "macd":          round(macd, 6),
            "macd_signal":   round(macd_signal, 6),
            "macd_hist":     round(macd_hist, 6),
            "bb_upper":      round(bb_upper, 4),
            "bb_lower":      round(bb_lower, 4),
            "bb_mid":        round(bb_mid, 4),
            "atr":           round(atr, 6),
            "atr_pct":       round(atr_pct, 4),
            "volume":        round(curr_vol, 2),
            "volume_ratio":  round(vol_ratio, 3),
        }

    async def get_multi_timeframe(self, symbol: str) -> dict:
        """
        Ambil indikator dari 3 timeframe sekaligus.
        Dipakai Sonnet untuk konfirmasi multi-timeframe.
        """
        tf_15m, tf_1h, tf_4h = await asyncio.gather(
            self.get_indicators(symbol, "15"),
            self.get_indicators(symbol, "60"),
            self.get_indicators(symbol, "240"),
        )
        return {"15m": tf_15m, "1h": tf_1h, "4h": tf_4h}

    def clear_cache(self, symbol: str | None = None):
        """Clear cache — dipanggil saat ada event market besar."""
        if symbol:
            keys = [k for k in _cache if k.startswith(symbol)]
            for k in keys:
                del _cache[k]
        else:
            _cache.clear()


market_data = MarketData()