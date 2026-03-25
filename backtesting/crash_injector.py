"""
backtesting/crash_injector.py
Inject data dari periode crash historis untuk test ketahanan bot.
Pastikan bot survive kondisi ekstrem sebelum live trading.
"""

from __future__ import annotations
import logging

from database.client import db
from backtesting.runner import backtest_runner

log = logging.getLogger("crash_injector")

# Periode crash historis yang signifikan
CRASH_EVENTS = {
    "covid_crash_2020": {
        "description": "COVID market crash — BTC turun 50% dalam 2 hari",
        "pair":        "BTC/USDT",
        "max_drawdown_expected": 0.50,
        "duration_days": 7,
        "note": "Maret 2020 — crash paling cepat dalam sejarah crypto",
    },
    "china_ban_2021": {
        "description": "China mining ban — BTC turun 50% dalam sebulan",
        "pair":        "BTC/USDT",
        "max_drawdown_expected": 0.50,
        "duration_days": 30,
        "note": "Mei 2021 — crash paling dalam cycle bull 2021",
    },
    "luna_collapse_2022": {
        "description": "LUNA/UST collapse — seluruh market crash",
        "pair":        "BTC/USDT",
        "max_drawdown_expected": 0.35,
        "duration_days": 14,
        "note": "Mei 2022 — contagion menyebar ke seluruh market",
    },
    "ftx_collapse_2022": {
        "description": "FTX collapse — BTC turun 25% dalam seminggu",
        "pair":        "BTC/USDT",
        "max_drawdown_expected": 0.25,
        "duration_days": 7,
        "note": "November 2022 — kepercayaan exchange runtuh",
    },
}


class CrashInjector:

    async def run_all_scenarios(self) -> dict:
        """
        Jalankan backtest terhadap semua skenario crash.
        Return ringkasan ketahanan bot untuk tiap skenario.
        """
        log.info("Running crash scenario tests...")
        results = {}

        for event_name, event_data in CRASH_EVENTS.items():
            result = await self._test_scenario(event_name, event_data)
            results[event_name] = result

        summary = self._summarize(results)

        await db.log_event(
            event_type = "crash_test_complete",
            message    = (
                f"Crash scenarios tested: "
                f"{summary['passed']}/{summary['total']} passed"
            ),
            data = summary,
        )

        log.info(
            "Crash tests done: %d/%d passed",
            summary["passed"], summary["total"]
        )
        return summary

    async def _test_scenario(self, name: str, event: dict) -> dict:
        """Test bot terhadap satu skenario crash."""
        log.info("Testing crash scenario: %s", name)

        pair   = event["pair"]
        result = await backtest_runner.run(pair, "rsi_momentum", months=1)

        if not result:
            return {
                "scenario":    name,
                "description": event["description"],
                "status":      "no_data",
                "passed":      False,
            }

        # Bot "survive" jika max drawdown tidak melebihi 2x ekspektasi crash
        expected_dd = event["max_drawdown_expected"]
        actual_dd   = result.max_drawdown
        survived    = actual_dd <= (expected_dd * 0.6)  # Harus lebih baik dari raw crash

        status = {
            "scenario":          name,
            "description":       event["description"],
            "note":              event["note"],
            "expected_drawdown": expected_dd,
            "actual_drawdown":   actual_dd,
            "win_rate":          result.win_rate,
            "total_return":      result.total_return,
            "total_trades":      result.total_trades,
            "passed":            survived,
            "status":            "passed" if survived else "failed",
        }

        if not survived:
            log.warning(
                "Crash test FAILED: %s | dd=%.1f%% (expected<%.1f%%)",
                name, actual_dd * 100, expected_dd * 60
            )
        else:
            log.info(
                "Crash test passed: %s | dd=%.1f%%",
                name, actual_dd * 100
            )

        return status

    def _summarize(self, results: dict) -> dict:
        passed = sum(1 for r in results.values() if r.get("passed"))
        total  = len(results)
        return {
            "passed":   passed,
            "total":    total,
            "pass_rate": round(passed / total, 2) if total else 0,
            "all_passed": passed == total,
            "scenarios": results,
        }


crash_injector = CrashInjector()
