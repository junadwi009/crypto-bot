"""
exchange/order_manager.py
Lifecycle order: buat → pantau → tutup.

PATCHED 2026-05-02:
- BUG FIX: PnL calculation untuk SELL — dulu pakai formula long-only
  (current - entry) * qty bahkan kalau side='sell' → flip tanda
- Notif Telegram saat trade closed (sebelumnya hanya log)
- Defensive checks untuk price <= 0
"""

from __future__ import annotations
import logging

from config.settings import settings
from database.client import db
from database.models import TradeCreate, TradeSignal
from exchange.bybit_client import bybit

log = logging.getLogger("order_manager")


class OrderManager:

    async def open_position(self, signal: TradeSignal) -> str | None:
        if not signal.is_actionable:
            log.debug("Signal not actionable: %s confidence=%.2f",
                      signal.pair, signal.confidence)
            return None

        try:
            current_price = await bybit.get_price(signal.pair)
            if current_price <= 0:
                log.warning("%s: invalid price %s", signal.pair, current_price)
                return None

            qty = bybit.calc_qty(signal.pair, signal.suggested_size, current_price)
            fee = bybit.calc_fee(signal.suggested_size)

            order = await bybit.place_market_order(
                symbol = signal.pair,
                side   = "Buy" if signal.action == "buy" else "Sell",
                qty    = qty,
            )

            exec_price = float(order.get("price") or current_price)

            trade = TradeCreate(
                pair           = signal.pair,
                side           = signal.action,
                amount_usd     = signal.suggested_size,
                entry_price    = exec_price,
                trigger_source = signal.source,
                bybit_order_id = order.get("orderId"),
                is_paper       = settings.PAPER_TRADE,
            )
            trade_id = await db.save_trade(trade)

            await db.log_event(
                event_type = "trade_opened",
                message    = f"{signal.action.upper()} {signal.pair} "
                             f"${signal.suggested_size:.2f} @ {exec_price:.4f}",
                data = {
                    "trade_id":   trade_id,
                    "pair":       signal.pair,
                    "side":       signal.action,
                    "size_usd":   signal.suggested_size,
                    "price":      exec_price,
                    "confidence": signal.confidence,
                    "source":     signal.source,
                },
            )

            try:
                from notifications.telegram_bot import telegram
                await telegram.send_trade_opened(
                    pair  = signal.pair,
                    side  = signal.action,
                    size  = signal.suggested_size,
                    price = exec_price,
                    source= signal.source,
                )
            except Exception as e:
                log.debug("Trade open notif skipped: %s", e)

            log.info("Position opened: %s %s $%.2f @ %.4f (id=%s)",
                     signal.action, signal.pair, signal.suggested_size,
                     exec_price, trade_id)
            return trade_id

        except Exception as e:
            log.error("Failed to open position %s: %s", signal.pair, e)
            await db.log_event(
                event_type = "order_error",
                message    = f"Failed to open {signal.pair}: {e}",
                severity   = "warning",
            )
            return None

    async def close_position(self, trade_id: str, pair: str,
                              amount_usd: float, entry_price: float,
                              reason: str = "signal",
                              side: str = "buy") -> bool:
        """
        Tutup posisi yang sedang terbuka.
        side: side ASLI posisi ('buy' = long, 'sell' = short).
        """
        try:
            current_price = await bybit.get_price(pair)
            if current_price <= 0 or entry_price <= 0:
                log.warning("%s: invalid prices for close (entry=%s curr=%s)",
                            pair, entry_price, current_price)
                return False

            qty = bybit.calc_qty(pair, amount_usd, entry_price)
            fee = bybit.calc_fee(amount_usd)

            # FIX PnL direction
            if side == "buy":
                pnl = (current_price - entry_price) * qty - fee
                close_side = "Sell"
            else:
                pnl = (entry_price - current_price) * qty - fee
                close_side = "Buy"

            await bybit.place_market_order(
                symbol = pair,
                side   = close_side,
                qty    = qty,
            )

            await db.close_trade(trade_id, current_price, pnl, fee)

            await db.log_event(
                event_type = "trade_closed",
                message    = f"CLOSE {pair} @ {current_price:.4f} "
                             f"PnL=${pnl:.2f} reason={reason}",
                data = {
                    "trade_id":   trade_id,
                    "pair":       pair,
                    "exit_price": current_price,
                    "pnl_usd":    round(pnl, 4),
                    "fee_usd":    round(fee, 4),
                    "reason":     reason,
                    "side":       side,
                },
            )

            try:
                from notifications.telegram_bot import telegram
                await telegram.send_trade_closed(
                    pair       = pair,
                    pnl        = pnl,
                    reason     = reason,
                    exit_price = current_price,
                )
            except Exception as e:
                log.debug("Close notif skipped: %s", e)

            log.info("Position closed: %s side=%s @ %.4f PnL=$%.2f (%s)",
                     pair, side, current_price, pnl, reason)
            return True

        except Exception as e:
            log.error("Failed to close position %s: %s", pair, e)
            return False

    async def check_stop_loss_take_profit(self, trade: dict) -> str | None:
        params = await db.get_strategy_params(trade["pair"])
        current_price = await bybit.get_price(trade["pair"])
        entry = float(trade["entry_price"])
        side  = trade.get("side", "buy")

        if entry <= 0 or current_price <= 0:
            return None

        if side == "buy":
            sl_price = entry * (1 - params.stop_loss_pct   / 100)
            tp_price = entry * (1 + params.take_profit_pct / 100)
            if current_price <= sl_price:
                return "stop_loss"
            if current_price >= tp_price:
                return "take_profit"
        else:
            sl_price = entry * (1 + params.stop_loss_pct   / 100)
            tp_price = entry * (1 - params.take_profit_pct / 100)
            if current_price >= sl_price:
                return "stop_loss"
            if current_price <= tp_price:
                return "take_profit"
        return None

    async def monitor_open_trades(self):
        open_trades = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
        for trade in open_trades:
            try:
                trigger = await self.check_stop_loss_take_profit(trade)
                if trigger:
                    await self.close_position(
                        trade_id    = trade["id"],
                        pair        = trade["pair"],
                        amount_usd  = float(trade["amount_usd"]),
                        entry_price = float(trade["entry_price"]),
                        reason      = trigger,
                        side        = trade.get("side", "buy"),
                    )
            except Exception as e:
                log.error("Monitor error for %s: %s", trade.get("pair"), e)


order_manager = OrderManager()
