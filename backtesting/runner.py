"""
backtesting/runner.py
Jalankan backtest menggunakan vectorbt.
Ambil data historis dari Bybit, test strategi, simpan hasil ke DB.
"""

from __future__ import annotations
import logging
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from config.settings import settings
from database.client import db
from database.models import BacktestResult
from exchange.bybit_client import bybit

log = logging.getLogger("backtesting")


class BacktestRunner:

    async def run(self, pair: str, strategy: str = "rsi_momentum",
                  months: int = 6) -> BacktestResult | None:
        """
        Jalankan backtest untuk satu pair dan satu strategi.
        Return BacktestResult yang langsung bisa disimpan ke DB.
        """
        log.info("Backtest: %s | %s | %d months", pair, strategy, months)

        try:
            # Ambil data historis
            df = await self._fetch_historical(pair, months)
            if df is None or len(df) < 50:
                log.warning("Not enough data for %s", pair)
                return None

            # Jalankan strategi
            if strategy == "rsi_momentum":
                result = self._run_rsi_momentum(df, pair)
            elif strategy == "macd_crossover":
                result = self._run_macd_crossover(df, pair)
            else:
                result = self._run_rsi_momentum(df, pair)

            period_start = date.today() - timedelta(days=months * 30)
            period_end   = date.today()

            bt_result = BacktestResult(
                pair         = pair,
                strategy     = strategy,
                period_start = period_start,
                period_end   = period_end,
                total_return = result["total_return"],
                sharpe_ratio = result["sharpe_ratio"],
                win_rate     = result["win_rate"],
                max_drawdown = result["max_drawdown"],
                total_trades = result["total_trades"],
                params_used  = result["params"],
            )

            await db.save_backtest_result(bt_result)
            await db.update_pair_lrhr_score(
                pair, result["sharpe_ratio"] / 2.0, result["win_rate"]
            )

            log.info(
                "Backtest done: %s | return=%.1f%% | win=%.1f%% "
                "| sharpe=%.2f | dd=%.1f%%",
                pair,
                result["total_return"] * 100,
                result["win_rate"] * 100,
                result["sharpe_ratio"],
                result["max_drawdown"] * 100,
            )
            return bt_result

        except Exception as e:
            log.error("Backtest error %s: %s", pair, e, exc_info=True)
            return None

    async def run_all_pairs(self, months: int = 6) -> list[BacktestResult]:
        """Backtest semua pair yang terdaftar."""
        from config.settings import settings
        import json
        from pathlib import Path

        pairs_cfg = json.loads(
            (Path(__file__).parent.parent / "config" / "pairs.json").read_text()
        )
        results = []
        for p in pairs_cfg["pairs"]:
            result = await self.run(p["pair"], p["strategy"], months)
            if result:
                results.append(result)
        return results

    async def _fetch_historical(self, pair: str,
                                 months: int) -> pd.DataFrame | None:
        """Ambil data OHLCV historis dari Bybit."""
        try:
            # Bybit max 1000 candles per request
            # Pakai interval 1h, ~720 candles per bulan
            limit = min(months * 720, 1000)
            raw   = await bybit.get_ohlcv(pair, interval="60", limit=limit)
            if not raw:
                return None

            df = pd.DataFrame(raw)
            df.set_index("timestamp", inplace=True)
            df = df.astype(float)
            df.index = pd.to_datetime(df.index, unit="ms")
            df.sort_index(inplace=True)
            return df

        except Exception as e:
            log.error("Failed to fetch historical data for %s: %s", pair, e)
            return None

    def _run_rsi_momentum(self, df: pd.DataFrame, pair: str) -> dict:
        """
        Backtest strategi RSI momentum.
        Buy saat RSI < 32, sell saat RSI > 71.
        """
        import pandas_ta_classic as ta

        close  = df["close"]
        high   = df["high"]
        low    = df["low"]

        # Indikator
        rsi    = ta.rsi(close, length=14)
        atr    = ta.atr(high, low, close, length=14)
        atr_pct = (atr / close) * 100

        params = {
            "rsi_oversold":  32,
            "rsi_overbought": 71,
            "stop_loss_pct":  2.2,
            "take_profit_pct": 4.5,
            "atr_threshold":  0.8,
        }

        # Simulasi trading
        trades       = []
        in_position  = False
        entry_price  = 0.0
        initial_cap  = 100.0  # Normalized capital
        capital      = initial_cap
        peak_capital = initial_cap

        for i in range(20, len(df)):
            price   = float(close.iloc[i])
            rsi_val = float(rsi.iloc[i]) if pd.notna(rsi.iloc[i]) else 50.0
            atr_val = float(atr_pct.iloc[i]) if pd.notna(atr_pct.iloc[i]) else 1.0

            if not in_position:
                # Entry signal
                if rsi_val < params["rsi_oversold"] and atr_val >= params["atr_threshold"]:
                    entry_price = price
                    in_position = True

            else:
                sl_price = entry_price * (1 - params["stop_loss_pct"] / 100)
                tp_price = entry_price * (1 + params["take_profit_pct"] / 100)

                if price <= sl_price:
                    pnl = (price - entry_price) / entry_price
                    capital *= (1 + pnl)
                    trades.append({"pnl": pnl, "exit": "stop_loss"})
                    in_position = False
                elif price >= tp_price or rsi_val > params["rsi_overbought"]:
                    pnl = (price - entry_price) / entry_price
                    capital *= (1 + pnl)
                    trades.append({"pnl": pnl, "exit": "take_profit"})
                    in_position = False

                peak_capital = max(peak_capital, capital)

        if not trades:
            return self._empty_result(params)

        wins      = [t for t in trades if t["pnl"] > 0]
        win_rate  = len(wins) / len(trades)
        total_ret = (capital - initial_cap) / initial_cap
        max_dd    = (peak_capital - capital) / peak_capital if peak_capital > capital else 0.0

        # Sharpe ratio (simplified annualized)
        pnls       = [t["pnl"] for t in trades]
        mean_pnl   = np.mean(pnls)
        std_pnl    = np.std(pnls) if len(pnls) > 1 else 0.01
        sharpe     = (mean_pnl / std_pnl) * np.sqrt(252) if std_pnl > 0 else 0.0

        return {
            "total_return": round(float(total_ret), 4),
            "sharpe_ratio": round(float(sharpe), 4),
            "win_rate":     round(float(win_rate), 4),
            "max_drawdown": round(float(max_dd), 4),
            "total_trades": len(trades),
            "params":       params,
        }

    def _run_macd_crossover(self, df: pd.DataFrame, pair: str) -> dict:
        """Backtest strategi MACD crossover."""
        import pandas_ta_classic as ta

        close = df["close"]
        macd_df = ta.macd(close, fast=12, slow=26, signal=9)
        if macd_df is None:
            return self._empty_result({})

        macd_line = macd_df["MACD_12_26_9"]
        macd_sig  = macd_df["MACDs_12_26_9"]
        macd_hist = macd_df["MACDh_12_26_9"]

        params = {"macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
                  "stop_loss_pct": 2.2, "take_profit_pct": 4.5}

        trades      = []
        in_position = False
        entry_price = 0.0
        capital     = 100.0
        peak        = 100.0

        for i in range(30, len(df)):
            price    = float(close.iloc[i])
            hist_now = float(macd_hist.iloc[i])   if pd.notna(macd_hist.iloc[i])  else 0.0
            hist_prv = float(macd_hist.iloc[i-1]) if pd.notna(macd_hist.iloc[i-1]) else 0.0

            if not in_position:
                # Bullish crossover
                if hist_prv <= 0 and hist_now > 0:
                    entry_price = price
                    in_position = True
            else:
                sl = entry_price * (1 - params["stop_loss_pct"] / 100)
                tp = entry_price * (1 + params["take_profit_pct"] / 100)

                if price <= sl or (hist_prv >= 0 and hist_now < 0):
                    pnl = (price - entry_price) / entry_price
                    capital *= (1 + pnl)
                    trades.append({"pnl": pnl})
                    in_position = False
                elif price >= tp:
                    pnl = (price - entry_price) / entry_price
                    capital *= (1 + pnl)
                    trades.append({"pnl": pnl})
                    in_position = False

                peak = max(peak, capital)

        if not trades:
            return self._empty_result(params)

        wins     = [t for t in trades if t["pnl"] > 0]
        pnls     = [t["pnl"] for t in trades]
        mean_pnl = np.mean(pnls)
        std_pnl  = np.std(pnls) if len(pnls) > 1 else 0.01
        sharpe   = (mean_pnl / std_pnl) * np.sqrt(252) if std_pnl > 0 else 0.0

        return {
            "total_return": round(float((capital - 100) / 100), 4),
            "sharpe_ratio": round(float(sharpe), 4),
            "win_rate":     round(float(len(wins) / len(trades)), 4),
            "max_drawdown": round(float((peak - capital) / peak if peak > capital else 0), 4),
            "total_trades": len(trades),
            "params":       params,
        }

    @staticmethod
    def _empty_result(params: dict) -> dict:
        return {
            "total_return": 0.0, "sharpe_ratio": 0.0,
            "win_rate": 0.0,     "max_drawdown": 0.0,
            "total_trades": 0,   "params": params,
        }


backtest_runner = BacktestRunner()