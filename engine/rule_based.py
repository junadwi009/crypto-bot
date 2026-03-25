"""
engine/rule_based.py
Lapisan pertama analisis — rule-based tanpa Claude.
Cepat, deterministik, gratis. Berjalan setiap tick.
Kalau sinyal cukup kuat → lolos ke Haiku untuk validasi.
"""

from __future__ import annotations
import logging

from config.settings import settings
from database.client import db
from exchange.market_data import market_data

log = logging.getLogger("rule_based")


class RuleBasedEngine:

    async def analyze(self, symbol: str) -> dict:
        """
        Analisis teknikal lengkap untuk satu pair.
        Return dict dengan action, confidence, dan reasoning.
        """
        params = await db.get_strategy_params(symbol)
        ind    = await market_data.get_indicators(symbol, interval="15")

        # ── Guard: ATR terlalu rendah = market sideways ──────────────
        if ind["atr_pct"] < params.atr_no_trade_threshold:
            return self._signal("hold", 0.0, "atr_low",
                                f"ATR {ind['atr_pct']:.2f}% < threshold {params.atr_no_trade_threshold}%")

        # ── Guard: volume terlalu rendah ─────────────────────────────
        if ind["volume_ratio"] < 1.0:
            return self._signal("hold", 0.0, "low_volume",
                                f"Volume ratio {ind['volume_ratio']:.2f} < 1.0")

        # ── Scoring sinyal ───────────────────────────────────────────
        buy_score  = 0.0
        sell_score = 0.0
        reasons    = []

        # RSI
        if ind["rsi"] <= params.rsi_oversold:
            weight     = min((params.rsi_oversold - ind["rsi"]) / 10, 1.0) * 0.35
            buy_score += weight
            reasons.append(f"RSI oversold {ind['rsi']:.1f}")
        elif ind["rsi"] >= params.rsi_overbought:
            weight      = min((ind["rsi"] - params.rsi_overbought) / 10, 1.0) * 0.35
            sell_score += weight
            reasons.append(f"RSI overbought {ind['rsi']:.1f}")

        # MACD crossover
        if ind["macd"] > ind["macd_signal"] and ind["macd_hist"] > 0:
            buy_score += 0.25
            reasons.append("MACD bullish crossover")
        elif ind["macd"] < ind["macd_signal"] and ind["macd_hist"] < 0:
            sell_score += 0.25
            reasons.append("MACD bearish crossover")

        # Bollinger Bands
        price = ind["price"]
        if price <= ind["bb_lower"] * 1.005:
            buy_score += 0.20
            reasons.append("Price at BB lower")
        elif price >= ind["bb_upper"] * 0.995:
            sell_score += 0.20
            reasons.append("Price at BB upper")

        # Volume konfirmasi (bonus)
        if ind["volume_ratio"] >= 1.5:
            if buy_score > sell_score:
                buy_score  = min(buy_score + 0.10, 1.0)
            elif sell_score > buy_score:
                sell_score = min(sell_score + 0.10, 1.0)
            reasons.append(f"High volume {ind['volume_ratio']:.1f}x")

        # ── Tentukan aksi ────────────────────────────────────────────
        if buy_score >= 0.40:
            return self._signal("buy",  round(buy_score, 3),
                                "rule_based", " + ".join(reasons), ind)
        if sell_score >= 0.40:
            return self._signal("sell", round(sell_score, 3),
                                "rule_based", " + ".join(reasons), ind)

        return self._signal("hold", 0.0, "rule_based",
                            f"No clear signal (buy={buy_score:.2f} sell={sell_score:.2f})")

    @staticmethod
    def _signal(action: str, confidence: float, source: str,
                reason: str, indicators: dict | None = None) -> dict:
        return {
            "action":     action,
            "confidence": confidence,
            "source":     source,
            "reason":     reason,
            "indicators": indicators or {},
        }


rule_engine = RuleBasedEngine()
