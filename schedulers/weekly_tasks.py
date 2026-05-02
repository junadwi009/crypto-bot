"""
schedulers/weekly_tasks.py
Job mingguan: backtest, Opus evaluation, crash test, cleanup.

PATCHED 2026-05-02:
- Opus evaluation memanggil weights_aggregator dulu (di Opus._build_context),
  lalu backtest_results_analyzer untuk feedback ke param
- Cleanup news pakai delete cascade
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

            # Notif Telegram dengan ringkasan
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
    """Backtest tiap pair aktif. Hasil disimpan ke backtest_results."""
    log.info("Weekly: backtest semua pair aktif")
    try:
        from backtesting.runner import backtest_runner

        active_pairs = await db.get_active_pairs()
        for pair in active_pairs:
            try:
                result = await backtest_runner.run(pair, days=30)
                log.info("Backtest %s: sharpe=%.2f win=%.1f%% trades=%d",
                         pair, result.get("sharpe_ratio", 0),
                         result.get("win_rate", 0) * 100,
                         result.get("total_trades", 0))
            except Exception as e:
                log.error("Backtest %s failed: %s", pair, e)
    except Exception as e:
        log.error("Weekly backtest orchestrator error: %s", e)


async def run_weekly_crash_test():
    """Inject crash scenario ke market data dan run backtest."""
    log.info("Weekly: crash injection test")
    try:
        from backtesting.crash_injector import crash_injector
        active_pairs = await db.get_active_pairs()
        for pair in active_pairs:
            try:
                result = await crash_injector.run(pair)
                log.info("Crash test %s: max_dd=%.1f%% survived=%s",
                         pair, result.get("max_drawdown", 0) * 100,
                         result.get("survived", False))
            except Exception as e:
                log.error("Crash test %s failed: %s", pair, e)
    except Exception as e:
        log.error("Crash test orchestrator error: %s", e)


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
