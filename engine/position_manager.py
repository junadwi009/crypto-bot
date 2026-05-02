"""
engine/position_manager.py
Hitung ukuran posisi dan track posisi yang terbuka.

PATCHED 2026-05-02:
- Position sizing menyesuaikan flag news (reduce_exposure → size × 0.5,
  opportunity → size × 1.3 dengan cap maksimum)
- Defensive division-by-zero
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
        Hitung berapa USD untuk satu trade.
        Formula:
          base_size  = capital × MAX_RISK_PER_TRADE (2%)
          adjusted   = base × multiplier × confidence_factor × news_factor
          clamp      = [$5, capital × 5%]
        """
        capital    = await db.get_current_capital()
        params     = await db.get_strategy_params(pair)
        multiplier = float(params.position_multiplier)

        base_size = capital * settings.MAX_RISK_PER_TRADE

        # Confidence factor: 0.6→0.8x, 1.0→1.0x
        if confidence >= 0.6:
            conf_factor = 0.8 + (confidence - 0.6) * 0.5
        else:
            conf_factor = 0.8

        # News factor — diatur dari engine/news_action_executor
        news_factor = 1.0
        try:
            from engine.news_action_executor import news_action_executor
            if await news_action_executor.is_reduce_exposure(pair):
                news_factor = 0.5
                log.info("%s: news_reduce_exposure active → size × 0.5", pair)
            elif await news_action_executor.is_opportunity(pair):
                news_factor = 1.3
                log.info("%s: news_opportunity active → size × 1.3", pair)
        except Exception:
            pass

        size = base_size * multiplier * conf_factor * news_factor

        # Clamp
        max_size = capital * 0.05
        size = min(size, max_size)
        size = max(size, 5.0)

        log.debug("Position size: %s base=$%.2f mult=%.2f conf=%.2f news=%.2f → $%.2f",
                  pair, base_size, multiplier, confidence, news_factor, size)
        return round(size, 2)

    async def calc_stop_loss_price(self, pair: str, entry_price: float,
                                    side: str) -> float:
        params = await db.get_strategy_params(pair)
        sl_pct = params.stop_loss_pct / 100
        if side == "buy":
            return round(entry_price * (1 - sl_pct), 8)
        return round(entry_price * (1 + sl_pct), 8)

    async def calc_take_profit_price(self, pair: str, entry_price: float,
                                      side: str) -> float:
        params = await db.get_strategy_params(pair)
        tp_pct = params.take_profit_pct / 100
        if side == "buy":
            return round(entry_price * (1 + tp_pct), 8)
        return round(entry_price * (1 - tp_pct), 8)

    async def get_open_exposure(self) -> float:
        open_trades = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
        return sum(float(t.get("amount_usd", 0)) for t in open_trades)

    async def get_exposure_by_pair(self) -> dict[str, float]:
        open_trades = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
        result: dict[str, float] = {}
        for t in open_trades:
            pair = t["pair"]
            result[pair] = result.get(pair, 0) + float(t.get("amount_usd", 0))
        return result

    async def get_daily_pnl(self) -> float:
        return await db.get_total_pnl(days=1)

    async def should_reduce_exposure(self) -> bool:
        capital  = await db.get_current_capital()
        daily_pnl = await self.get_daily_pnl()
        if capital <= 0:
            return False
        return (daily_pnl / capital) <= -0.10


position_manager = PositionManager()
