"""
exchange/order_manager.py
Kelola lifecycle order: buat → pantau → tutup.
Semua order wajib melalui file ini — tidak ada yang langsung ke bybit_client.
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
        """
        Buka posisi baru berdasarkan signal.
        Return trade_id jika berhasil, None jika gagal.
        """
        if not signal.is_actionable:
            log.debug("Signal not actionable: %s confidence=%.2f",
                      signal.pair, signal.confidence)
            return None

        try:
            current_price = await bybit.get_price(signal.pair)
            qty = bybit.calc_qty(signal.pair, signal.suggested_size, current_price)
            fee = bybit.calc_fee(signal.suggested_size)

            # Eksekusi order di Bybit
            order = await bybit.place_market_order(
                symbol = signal.pair,
                side   = "Buy" if signal.action == "buy" else "Sell",
                qty    = qty,
            )

            exec_price = float(order.get("price") or current_price)

            # Simpan ke database
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

            # Log event
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
                              reason: str = "signal") -> bool:
        """
        Tutup posisi yang sedang buka.
        Return True jika berhasil.
        """
        try:
            current_price = await bybit.get_price(pair)
            qty     = bybit.calc_qty(pair, amount_usd, entry_price)
            fee     = bybit.calc_fee(amount_usd)

            # Hitung PnL
            pnl = (current_price - entry_price) * qty - fee

            # Eksekusi close order
            order = await bybit.place_market_order(
                symbol = pair,
                side   = "Sell",   # close long position
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
                },
            )

            log.info("Position closed: %s @ %.4f PnL=$%.2f (%s)",
                     pair, current_price, pnl, reason)
            return True

        except Exception as e:
            log.error("Failed to close position %s: %s", pair, e)
            return False

    async def check_stop_loss_take_profit(self, trade: dict) -> str | None:
        """
        Cek apakah posisi sudah mencapai stop-loss atau take-profit.
        Return "stop_loss", "take_profit", atau None.
        """
        params = await db.get_strategy_params(trade["pair"])
        current_price = await bybit.get_price(trade["pair"])
        entry = float(trade["entry_price"])

        sl_price = entry * (1 - params.stop_loss_pct   / 100)
        tp_price = entry * (1 + params.take_profit_pct / 100)

        if current_price <= sl_price:
            return "stop_loss"
        if current_price >= tp_price:
            return "take_profit"
        return None

    async def monitor_open_trades(self):
        """
        Cek semua open trade — dipanggil dari trading loop setiap siklus.
        Tutup otomatis yang sudah kena SL/TP.
        """
        open_trades = await db.get_open_trades(
            is_paper=settings.PAPER_TRADE
        )

        for trade in open_trades:
            trigger = await self.check_stop_loss_take_profit(trade)
            if trigger:
                await self.close_position(
                    trade_id    = trade["id"],
                    pair        = trade["pair"],
                    amount_usd  = float(trade["amount_usd"]),
                    entry_price = float(trade["entry_price"]),
                    reason      = trigger,
                )


order_manager = OrderManager()
