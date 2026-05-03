"""
schedulers/weekly_tasks.py
Job mingguan: backtest, Opus evaluation, crash test, cleanup.

PATCHED 2026-05-02 (revisi 4):
- BUG FIX: crash_injector.run(pair) → crash_injector.run_all_scenarios()
  Method run() tidak ada di CrashInjector class. Selain itu konsep loop
  per-pair juga salah — crash test memang per-skenario historis (covid,
  china ban, luna, ftx), bukan per-pair Arjuna.
- BUG FIX: backtest_runner.run(pair, days=30) → backtest_runner.run(pair, months=1)
  Parameter `days` tidak ada, yang ada `months`.
- BUG FIX: result adalah BacktestResult object (Pydantic), pakai
  result.sharpe_ratio bukan result.get("sharpe_ratio")
- Opus evaluation tetap memanggil weights_aggregator dulu (di Opus._build_context)
"""

from __future__ import annotations
import logging
from datetime import date, timedelta

from database.client import db
from config.settings import settings

log = logging.getLogger("weekly_tasks")


async def run_opus_evaluation():
    """
    Opus mingguan — sudah include:
      1. weights_aggregator.run() (dipanggil dari opus_brain)
      2. trade_source_breakdown
      3. backtest summary semua pair aktif
      4. learning context dari minggu lalu (param change → outcome)
    """
    log.info("Weekly: Opus evaluation")
    try:
        from brains.opus_brain import opus_brain
        result = await opus_brain.weekly_evaluation()
        if result:
            actions = result.get("action_required", [])
            patterns = result.get("patterns_found", [])
            log.info("Opus evaluation: %d patterns, %d actions",
                     len(patterns), len(actions))

            try:
                from notifications.telegram_bot import telegram
                summary = result.get("summary", {})
                msg = (
                    f"Opus Weekly Eval\n\n"
                    f"Win rate:  {float(summary.get('win_rate', 0)) * 100:.1f}%\n"
                    f"PnL:       ${float(summary.get('total_pnl', 0)):.2f}\n"
                    f"Trades:    {int(summary.get('total_trades', 0))}\n"
                    f"Patterns:  {len(patterns)}\n"
                    f"Actions:   {len(actions)} "
                    f"({sum(1 for a in actions if a.get('priority') == 'P0')} P0)\n"
                    f"Cost:      ${float(result.get('token_cost', 0)):.4f}\n\n"
                )
                if patterns:
                    msg += "Top pattern:\n"
                    msg += f"  {patterns[0].get('pattern', '')[:200]}\n"
                await telegram.send(msg)
            except Exception:
                pass
    except Exception as e:
        log.error("Opus evaluation error: %s", e, exc_info=True)


async def run_weekly_backtest():
    """
    Backtest tiap pair aktif, pakai data 1 bulan terakhir.
    Hasil disimpan otomatis oleh backtest_runner.run() ke tabel backtest_results.
    """
    log.info("Weekly: backtest semua pair aktif")
    try:
        from backtesting.runner import backtest_runner

        active_pairs = await db.get_active_pairs()
        for pair in active_pairs:
            try:
                # FIX: parameter benar adalah `months`, bukan `days`
                result = await backtest_runner.run(pair, months=1)
                if result:
                    # FIX: BacktestResult adalah Pydantic model, akses via attribute
                    log.info(
                        "Backtest %s: sharpe=%.2f win=%.1f%% trades=%d",
                        pair,
                        float(result.sharpe_ratio),
                        float(result.win_rate) * 100,
                        int(result.total_trades),
                    )
                else:
                    log.warning("Backtest %s returned no result (insufficient data)", pair)
            except Exception as e:
                log.error("Backtest %s failed: %s", pair, e)
    except Exception as e:
        log.error("Weekly backtest orchestrator error: %s", e)


async def run_weekly_crash_test():
    """
    Test bot terhadap skenario crash historis (COVID 2020, China ban 2021,
    LUNA 2022, FTX 2022). Bukan per-pair — per-skenario.
    """
    log.info("Weekly: crash injection test")
    try:
        from backtesting.crash_injector import crash_injector
        # FIX: method yang benar adalah run_all_scenarios(), tanpa argument.
        # CrashInjector punya CRASH_EVENTS dict sendiri, dia tidak per-pair.
        summary = await crash_injector.run_all_scenarios()

        if summary:
            log.info(
                "Crash tests: %d/%d scenarios passed (rate=%.0f%%)",
                summary.get("passed", 0),
                summary.get("total", 0),
                summary.get("pass_rate", 0) * 100,
            )

            # Notif Telegram kalau ada yang gagal
            if not summary.get("all_passed", False):
                failed_names = [
                    name for name, data in summary.get("scenarios", {}).items()
                    if not data.get("passed")
                ]
                try:
                    from notifications.telegram_bot import telegram
                    await telegram.send(
                        f"CRASH TEST WARNING\n\n"
                        f"{summary['passed']}/{summary['total']} scenarios passed.\n"
                        f"Failed: {', '.join(failed_names)}\n\n"
                        f"Bot mungkin tidak survive kondisi market ekstrem. "
                        f"Cek log untuk detail."
                    )
                except Exception:
                    pass
    except Exception as e:
        log.error("Crash test orchestrator error: %s", e, exc_info=True)


async def cleanup_old_news():
    """Hapus news_items yang lebih lama dari 60 hari."""
    log.info("Weekly: cleanup news >60 days")
    try:
        cutoff = (date.today() - timedelta(days=60)).isoformat()
        res = (
            db._get()
            .table("news_items")
            .delete()
            .lt("published_at", cutoff)
            .execute()
        )
        deleted = len(res.data) if res.data else 0
        log.info("Cleaned up %d old news items", deleted)
    except Exception as e:
        log.error("News cleanup error: %s", e)