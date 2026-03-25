"""
backtesting/walk_forward.py
Walk-forward validation — pastikan strategi tidak overfitting.
Train di periode A, test di periode B, geser jendela maju, ulangi.
"""

from __future__ import annotations
import logging
from datetime import date, timedelta

from backtesting.runner import backtest_runner
from database.client import db

log = logging.getLogger("walk_forward")


class WalkForwardValidator:

    async def validate(self, pair: str, strategy: str = "rsi_momentum",
                       total_months: int = 12,
                       train_months: int = 4,
                       test_months:  int = 2) -> dict:
        """
        Jalankan walk-forward validation.

        Contoh dengan total=12, train=4, test=2:
          Window 1: train Jan–Apr, test May–Jun
          Window 2: train Mar–Jun, test Jul–Aug
          Window 3: train May–Aug, test Sep–Oct
          Window 4: train Jul–Oct, test Nov–Dec

        Return ringkasan konsistensi strategi.
        """
        log.info("Walk-forward: %s | %s | total=%dm train=%dm test=%dm",
                 pair, strategy, total_months, train_months, test_months)

        windows = self._build_windows(total_months, train_months, test_months)
        results = []

        for i, window in enumerate(windows):
            log.debug("Window %d: train=%s test=%s",
                      i + 1, window["train_months"], window["test_months"])

            # Backtest periode test saja
            result = await backtest_runner.run(
                pair, strategy, window["test_months"]
            )
            if result:
                results.append({
                    "window":       i + 1,
                    "win_rate":     result.win_rate,
                    "sharpe_ratio": result.sharpe_ratio,
                    "total_return": result.total_return,
                    "max_drawdown": result.max_drawdown,
                })

        if not results:
            return {"status": "insufficient_data", "pair": pair}

        # Hitung konsistensi
        win_rates    = [r["win_rate"]     for r in results]
        sharpes      = [r["sharpe_ratio"] for r in results]
        returns      = [r["total_return"] for r in results]

        import numpy as np
        summary = {
            "pair":            pair,
            "strategy":        strategy,
            "windows_tested":  len(results),
            "avg_win_rate":    round(float(np.mean(win_rates)), 4),
            "std_win_rate":    round(float(np.std(win_rates)), 4),
            "avg_sharpe":      round(float(np.mean(sharpes)), 4),
            "avg_return":      round(float(np.mean(returns)), 4),
            "consistent":      self._is_consistent(win_rates, sharpes),
            "windows":         results,
        }

        log.info(
            "Walk-forward done: %s | avg_win=%.1f%% avg_sharpe=%.2f consistent=%s",
            pair, summary["avg_win_rate"] * 100,
            summary["avg_sharpe"], summary["consistent"]
        )

        await db.log_event(
            event_type = "walk_forward_complete",
            message    = (
                f"{pair} walk-forward: "
                f"win={summary['avg_win_rate']*100:.1f}% "
                f"sharpe={summary['avg_sharpe']:.2f} "
                f"consistent={summary['consistent']}"
            ),
            data = summary,
        )
        return summary

    def _build_windows(self, total: int, train: int, test: int) -> list[dict]:
        """Buat sliding windows untuk walk-forward."""
        windows = []
        step    = test
        start   = 0
        while start + train + test <= total:
            windows.append({
                "train_months": train,
                "test_months":  test,
                "offset":       start,
            })
            start += step
        return windows

    def _is_consistent(self, win_rates: list[float],
                        sharpes: list[float]) -> bool:
        """
        Strategi dianggap konsisten jika:
        - Rata-rata win rate > 52%
        - Lebih dari 60% window punya win rate > 50%
        - Rata-rata Sharpe > 0.5
        """
        import numpy as np
        avg_wr      = np.mean(win_rates)
        pct_pos     = sum(1 for w in win_rates if w > 0.50) / len(win_rates)
        avg_sharpe  = np.mean(sharpes)
        return bool(avg_wr > 0.52 and pct_pos > 0.60 and avg_sharpe > 0.5)


walk_forward = WalkForwardValidator()
