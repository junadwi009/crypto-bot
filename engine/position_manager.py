"""
engine/position_manager.py
Hitung ukuran posisi dan track posisi yang terbuka.

PATCHED 2026-05-07 (Phase 1 — L0 kernel migration):
- All sizing constants now read from governance.safety_kernel (immutable
  L0 invariants). Local hardcoded 0.05 / 5.0 removed.
- position_multiplier re-validated via L0.validate_position_multiplier
  on every read; LayerZeroViolation raised on out-of-bounds (Opus must not
  be able to push multiplier past L0's tighter ceiling regardless of its
  own internal bounds).
- News factor capped via L0.cap_news_factor — news may REDUCE size
  (factor < 1.0) but NEVER raise size above ABSOLUTE_NEWS_AMP_CAP (= 1.0).
  Single-direction amplification per Council mandate M5.
- News executor exception now defaults to NEWS_FACTOR_SAFE_DEFAULT (0.5),
  not 1.0 — fail-safe direction is reduce, not preserve.
- LayerZeroViolation propagation: do NOT catch with broad except. The
  inner news executor try/except explicitly re-raises before swallowing
  non-L0 errors.

Previous behavior (silent except → news_factor=1.0) is replaced by the
above. Sizing math otherwise unchanged.
"""

from __future__ import annotations
import logging

from config.settings import settings
from database.client import db
from utils.redis_client import redis
from governance.exceptions import LayerZeroViolation
from governance import safety_kernel as L0

log = logging.getLogger("position_manager")


class PositionManager:

    async def calc_position_size(self, pair: str, confidence: float) -> float:
        """
        Hitung berapa USD untuk satu trade.
        Formula:
          base_size  = capital × MAX_RISK_PER_TRADE
          adjusted   = base × multiplier × confidence_factor × news_factor
          clamp      = [ABSOLUTE_MIN_ORDER_USD, capital × ABSOLUTE_SIZE_CAP_PCT]

        Raises LayerZeroViolation if position_multiplier from DB is outside
        L0 bounds. Caller must propagate (do not catch).
        """
        capital = await db.get_current_capital()
        params  = await db.get_strategy_params(pair)

        # L0: re-validate multiplier on every read. Raises on out-of-bounds.
        multiplier = L0.validate_position_multiplier(
            params.position_multiplier,
            source="engine.position_manager",
        )

        base_size = capital * L0.MAX_RISK_PER_TRADE

        # Confidence factor: 0.6→0.8x, 1.0→1.0x
        if confidence >= 0.6:
            conf_factor = 0.8 + (confidence - 0.6) * 0.5
        else:
            conf_factor = 0.8

        # News factor — derived from engine/news_action_executor.
        # CONTRACT: news may reduce (< 1.0), never raise above L0 cap.
        # Default on any failure path is the SAFE direction (reduce).
        news_factor_raw = L0.NEWS_FACTOR_SAFE_DEFAULT
        try:
            from engine.news_action_executor import news_action_executor
            if await news_action_executor.is_reduce_exposure(pair):
                news_factor_raw = 0.5
                log.info("%s: news_reduce_exposure active → size × 0.5", pair)
            elif await news_action_executor.is_opportunity(pair):
                # Council mandate M5: opportunity does NOT amplify size in
                # Phase 1. The flag is preserved in news pipeline; sizing
                # ignores it. Future Phase-3 evidence/upsize split (A19)
                # will allow conditional amplification with corroboration.
                news_factor_raw = 1.0
                log.info("%s: news_opportunity active (size cap = 1.0)", pair)
            else:
                news_factor_raw = 1.0
        except LayerZeroViolation:
            raise
        except Exception as e:
            log.warning(
                "%s: news executor unreachable (%s) — defaulting news_factor to %.2f (safe)",
                pair, e, L0.NEWS_FACTOR_SAFE_DEFAULT,
            )
            news_factor_raw = L0.NEWS_FACTOR_SAFE_DEFAULT

        # L0: cap news factor — defense-in-depth, even if logic above is wrong.
        news_factor = L0.cap_news_factor(news_factor_raw, source="engine.position_manager")

        size = base_size * multiplier * conf_factor * news_factor

        # Clamp using L0 invariants (replaces local hardcodes)
        max_size = capital * L0.ABSOLUTE_SIZE_CAP_PCT
        size = min(size, max_size)
        size = max(size, L0.ABSOLUTE_MIN_ORDER_USD)

        # L0: defense-in-depth — final size must not exceed cap.
        # If this raises, the formula above produced a value the clamp missed.
        L0.validate_size_against_capital(
            size, capital, source="engine.position_manager",
        )

        log.debug(
            "Position size: %s base=$%.2f mult=%.2f conf=%.2f news=%.2f → $%.2f",
            pair, base_size, multiplier, confidence, news_factor, size,
        )
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
