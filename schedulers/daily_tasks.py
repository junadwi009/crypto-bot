"""
schedulers/daily_tasks.py
Job harian — semua task yang dijadwal cron harian.

PATCHED 2026-05-02 (revisi 3):
- BUG FIX: payment_reminder.send_if_due() → check_and_send()
  (method name salah, hasilnya scheduler_job_error setiap hari jam 10:00)
- Portfolio snapshot pakai db.get_infra_balance() bukan
  settings.INFRA_FUND_INITIAL yang tidak ada
- Capital_start_of_day diambil benar untuk circuit breaker reference
"""

from __future__ import annotations
import logging
from datetime import date

from database.client import db
from database.models import PortfolioSnapshot
from config.settings import settings

log = logging.getLogger("daily_tasks")


async def take_portfolio_snapshot():
    log.info("Daily: portfolio snapshot")
    try:
        capital     = await db.get_current_capital()
        open_trades = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
        total_pnl   = await db.get_total_pnl(days=1)
        tier        = settings.get_tier(capital)

        try:
            infra = await db.get_infra_balance()
        except Exception as e:
            log.warning("Could not read infra balance: %s", e)
            infra = 0.0

        # Compute drawdown vs. recent peak so the dashboard chart isn't
        # always 0%. Look back 30 days; if no history yet, drawdown is 0.
        try:
            history = await db.get_portfolio_history(days=30)
            peak = max((float(r.get("total_capital") or 0) for r in history),
                       default=capital)
            drawdown_pct = max(0.0, (peak - capital) / peak) if peak > 0 else 0.0
        except Exception:
            drawdown_pct = 0.0

        active_pairs = await db.get_active_pairs()

        # NB: PortfolioSnapshot field names match schema (infra_reserve,
        # current_tier). Using infra_fund/tier/paper_trade/open_positions
        # raises ValidationError, which silently ate every prior snapshot.
        snap = PortfolioSnapshot(
            snapshot_date    = date.today(),
            total_capital    = capital,
            trading_capital  = max(0, capital - infra),
            infra_reserve    = infra,
            emergency_buffer = round(capital * 0.05, 4),
            current_tier     = tier,
            active_pairs     = active_pairs,
            daily_pnl        = total_pnl,
            drawdown_pct     = round(drawdown_pct, 4),
        )
        await db.save_portfolio_snapshot(snap)
        log.info("Snapshot saved: tier=%s capital=$%.2f infra=$%.2f open=%d daily_pnl=$%.2f dd=%.2f%%",
                 tier, capital, infra, len(open_trades), total_pnl, drawdown_pct * 100)
    except Exception as e:
        log.error("Portfolio snapshot error: %s", e, exc_info=True)


async def run_payment_reminder():
    """
    PATCHED: method sebelumnya `send_if_due()` tidak ada — bug ini
    yang muncul di event log sebagai 'PaymentReminder...failed'.
    Method yang benar: check_and_send().
    """
    from notifications.payment_reminder import payment_reminder
    await payment_reminder.check_and_send()


async def update_news_outcomes():
    log.info("Hourly: news outcome update")
    try:
        from news.outcome_tracker import outcome_tracker
        await outcome_tracker.update_pending_outcomes()
    except Exception as e:
        log.error("Outcome tracker error: %s", e)


async def update_lrhr_scores():
    log.info("Daily: LRHR scoring update")
    try:
        from engine.portfolio_manager import portfolio_manager
        capital = await db.get_current_capital()
        candidates = await portfolio_manager.get_candidate_pairs(capital)
        scores: list[tuple[str, float]] = []
        for pair in candidates:
            score = await portfolio_manager.calc_lrhr_score(pair)
            scores.append((pair, score))
        log.info("LRHR scored %d candidates", len(scores))
    except Exception as e:
        log.error("LRHR scoring error: %s", e)


async def monitor_circuit_breaker():
    try:
        from engine.circuit_breaker import circuit_breaker
        capital_now = await db.get_current_capital()
        history = await db.get_portfolio_history(days=2)
        if history:
            capital_start = float(history[0].get("total_capital") or capital_now)
            await circuit_breaker.check(capital_now, capital_start)
    except Exception as e:
        log.error("Circuit breaker monitor error: %s", e)


async def check_supabase_activity():
    try:
        await db.ping()
        log.info("Supabase keep-alive: OK")
    except Exception as e:
        log.error("Supabase ping error: %s", e)


async def send_daily_summary():
    log.info("Daily: 20:00 summary")
    try:
        from notifications.telegram_bot import telegram
        capital   = await db.get_current_capital()
        win_rate  = await db.get_win_rate(days=1)
        total_pnl = await db.get_total_pnl(days=1)
        trades    = await db.get_trades_for_period(days=1)

        await telegram.send(
            f"DAILY SUMMARY {date.today().isoformat()}\n\n"
            f"Capital:   ${capital:.2f}\n"
            f"Trades:    {len(trades)}\n"
            f"Win rate:  {win_rate * 100:.1f}%\n"
            f"PnL:       ${total_pnl:.2f}\n"
            f"Mode:      {'PAPER' if settings.PAPER_TRADE else 'LIVE'}"
        )
    except Exception as e:
        log.error("Daily summary error: %s", e)