"""
engine/position_manager.py
Hitung ukuran posisi dan track posisi yang terbuka.
Position sizing berdasarkan modal, tier, dan win rate.
"""

from __future__ import annotations
import logging

from config.settings import settings
from database.client import db
from utils.redis_client import redis

log = logging.getLogger("position_manager")


class PositionManager:

    async def calc_position_size(self, pair: str, confidence: float) -> float:
        """
        Hitung berapa USD yang boleh dipakai untuk satu trade.

        Formula:
          base_size = capital × risk_per_trade (2%)
          adjusted  = base_size × position_multiplier × confidence_factor
        """
        capital    = await db.get_current_capital()
        params     = await db.get_strategy_params(pair)
        multiplier = float(params.position_multiplier)

        # Base: 2% dari modal
        base_size = capital * settings.MAX_RISK_PER_TRADE

        # Faktor confidence dari Claude (0.6–1.0 → 0.8–1.0 scaling)
        conf_factor = 0.8 + (confidence - 0.6) * 0.5 if confidence >= 0.6 else 0.8

        # Apply multiplier dari Opus
        size = base_size * multiplier * conf_factor

        # Batas atas: 5% modal
        max_size = capital * 0.05
        size     = min(size, max_size)

        # Batas bawah: $5
        size = max(size, 5.0)

        log.debug("Position size: %s base=$%.2f mult=%.2f conf=%.2f → $%.2f",
                  pair, base_size, multiplier, confidence, size)
        return round(size, 2)

    async def calc_stop_loss_price(self, pair: str, entry_price: float,
                                    side: str) -> float:
        """Hitung harga stop-loss berdasarkan parameter strategi."""
        params   = await db.get_strategy_params(pair)
        sl_pct   = params.stop_loss_pct / 100
        if side == "buy":
            return round(entry_price * (1 - sl_pct), 8)
        else:
            return round(entry_price * (1 + sl_pct), 8)

    async def calc_take_profit_price(self, pair: str, entry_price: float,
                                      side: str) -> float:
        """Hitung harga take-profit berdasarkan parameter strategi."""
        params  = await db.get_strategy_params(pair)
        tp_pct  = params.take_profit_pct / 100
        if side == "buy":
            return round(entry_price * (1 + tp_pct), 8)
        else:
            return round(entry_price * (1 - tp_pct), 8)

    async def get_open_exposure(self) -> float:
        """Berapa USD yang sedang dalam posisi terbuka."""
        open_trades = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
        return sum(float(t.get("amount_usd", 0)) for t in open_trades)

    async def get_exposure_by_pair(self) -> dict[str, float]:
        """Exposure per pair dalam USD."""
        open_trades = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
        result: dict[str, float] = {}
        for t in open_trades:
            pair = t["pair"]
            result[pair] = result.get(pair, 0) + float(t.get("amount_usd", 0))
        return result

    async def get_daily_pnl(self) -> float:
        """PnL hari ini dari semua trade yang sudah closed."""
        return await db.get_total_pnl(days=1)

    async def should_reduce_exposure(self) -> bool:
        """
        Cek apakah perlu kurangi exposure.
        True jika PnL hari ini sudah -10% dari modal.
        """
        capital  = await db.get_current_capital()
        daily_pnl = await self.get_daily_pnl()
        if capital <= 0:
            return False
        return (daily_pnl / capital) <= -0.10


position_manager = PositionManager()
