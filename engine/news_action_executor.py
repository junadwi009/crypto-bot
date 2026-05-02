"""
engine/news_action_executor.py
Eksekusi rekomendasi aksi dari Sonnet news analyzer.

Sebelumnya: Sonnet bilang "close BTC karena hack besar" → cuma di-log,
            tidak ada efek nyata. Pipeline news cuma ornament.
Sekarang:
  - close      → tutup semua posisi terbuka di pair tersebut
  - reduce_risk → set short-term flag "reduce_exposure" di Redis
                  (signal generator akan mengecil position size sementara)
  - opportunity → set short-term flag "news_opportunity" di Redis dengan TTL 1 jam
                  (signal generator akan boost confidence threshold ke bawah
                  untuk pair tersebut, sehingga rule_based + haiku langsung approve)

Semua action selalu butuh Sonnet confidence ≥ 0.75 untuk dieksekusi
(threshold tinggi karena efek besar). Action gagal bila guard menolak.
"""

from __future__ import annotations
import logging

from config.settings import settings
from database.client import db
from utils.redis_client import redis

log = logging.getLogger("news_action_executor")

MIN_CONFIDENCE_FOR_ACTION = 0.75

# Redis key templates
_REDUCE_KEY    = "news_reduce_exposure:{pair}"   # TTL 30 menit
_OPPTY_KEY     = "news_opportunity:{pair}"        # TTL 60 menit
_REDUCE_TTL    = 30 * 60
_OPPTY_TTL     = 60 * 60


class NewsActionExecutor:

    async def execute(self, pairs: list[str], sonnet_data: dict,
                       haiku_data: dict, news_id: str, headline: str):
        """Dispatch action berdasarkan sonnet_data['action']."""
        action     = sonnet_data.get("action", "hold")
        confidence = float(sonnet_data.get("confidence") or 0)

        if action == "hold":
            return

        if confidence < MIN_CONFIDENCE_FOR_ACTION:
            log.info(
                "News action '%s' skipped — confidence %.2f < %.2f",
                action, confidence, MIN_CONFIDENCE_FOR_ACTION,
            )
            return

        # Dispatch
        if action == "close":
            await self._close_positions(pairs, sonnet_data, headline)
        elif action == "reduce_risk":
            await self._reduce_exposure(pairs, sonnet_data, headline)
        elif action == "opportunity":
            await self._mark_opportunity(pairs, sonnet_data, headline)
        else:
            log.warning("Unknown news action: %s", action)
            return

        # Audit trail
        await db.log_event(
            event_type = f"news_action_{action}",
            severity   = "warning" if action == "close" else "info",
            message    = f"News action {action}: {headline[:80]}",
            data       = {
                "action":     action,
                "confidence": confidence,
                "pairs":      pairs,
                "news_id":    news_id,
                "reasoning":  sonnet_data.get("reasoning", "")[:200],
            },
        )

    async def _close_positions(self, pairs: list[str],
                                sonnet_data: dict, headline: str):
        """Tutup semua open position di pair yang disebut."""
        from exchange.order_manager import order_manager

        open_trades = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
        affected = [t for t in open_trades if t.get("pair") in pairs]

        if not affected:
            log.info("News close: no open positions in %s", pairs)
            return

        log.warning(
            "NEWS CLOSE: %d position(s) for %s | reason=%s",
            len(affected), pairs, sonnet_data.get("reasoning", "")[:80],
        )

        for trade in affected:
            try:
                await order_manager.close_position(
                    trade_id    = trade["id"],
                    pair        = trade["pair"],
                    amount_usd  = float(trade["amount_usd"]),
                    entry_price = float(trade["entry_price"]),
                    reason      = "news_close",
                )
            except Exception as e:
                log.error("Failed to close %s on news event: %s", trade["pair"], e)

        # Notif Telegram (lazy import)
        try:
            from notifications.telegram_bot import telegram
            await telegram.send(
                f"NEWS-DRIVEN CLOSE\n\n"
                f"Pairs: {', '.join(pairs)}\n"
                f"Closed: {len(affected)} position(s)\n"
                f"Reason: {sonnet_data.get('reasoning', '')[:120]}\n\n"
                f"Headline: {headline[:120]}"
            )
        except Exception:
            pass

    async def _reduce_exposure(self, pairs: list[str],
                                sonnet_data: dict, headline: str):
        """Set flag agar signal generator pakai position size lebih kecil."""
        for pair in pairs:
            await redis.setex(_REDUCE_KEY.format(pair=pair), _REDUCE_TTL, "1")
        log.info("News reduce_risk active for %s for %d min",
                 pairs, _REDUCE_TTL // 60)

    async def _mark_opportunity(self, pairs: list[str],
                                  sonnet_data: dict, headline: str):
        """Set flag opportunity — signal generator akan lebih agresif."""
        for pair in pairs:
            await redis.setex(_OPPTY_KEY.format(pair=pair), _OPPTY_TTL, "1")
        log.info("News opportunity active for %s for %d min",
                 pairs, _OPPTY_TTL // 60)

    # ── Helpers untuk dipakai signal generator ────────────────────

    @staticmethod
    async def is_reduce_exposure(pair: str) -> bool:
        return bool(await redis.get(_REDUCE_KEY.format(pair=pair)))

    @staticmethod
    async def is_opportunity(pair: str) -> bool:
        return bool(await redis.get(_OPPTY_KEY.format(pair=pair)))


news_action_executor = NewsActionExecutor()
