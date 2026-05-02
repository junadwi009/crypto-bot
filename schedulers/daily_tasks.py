"""
schedulers/daily_tasks.py
Job harian — semua task yang dijadwal cron harian.

PATCHED 2026-05-02 (revisi 2):
- Portfolio snapshot pakai db.get_infra_balance() bukan
  settings.INFRA_FUND_INITIAL yang tidak ada → 500 error sebelumnya
- Capital_start_of_day diambil benar untuk circuit breaker reference
- update_news_outcomes punya error per-batch (dulu satu error matiin loop)
"""

from __future__ import annotations
import logging
from datetime import date, datetime, timezone

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

        # Hitung tier
        if capital < 300:    tier = "seed"
        elif capital < 700:  tier = "growth"
        elif capital < 1500: tier = "pro"
        else:                tier = "elite"

        # Get infra balance from DB (single source of truth)
        try:
            infra = await db.get_infra_balance()
        except Exception as e:
            log.warning("Could not read infra balance: %s", e)
            infra = 0.0

        snap = PortfolioSnapshot(
            snapshot_date  = date.today(),
            total_capital  = capital,
            trading_capital= max(0, capital - infra),
            infra_fund     = infra,
            open_positions = len(open_trades),
            daily_pnl      = total_pnl,
            tier           = tier,
            paper_trade    = settings.PAPER_TRADE,
        )
        await db.save_portfolio_snapshot(snap)
        log.info("Snapshot saved: tier=%s capital=$%.2f infra=$%.2f open=%d daily_pnl=$%.2f",
                 tier, capital, infra, len(open_trades), total_pnl)
    except Exception as e:
        log.error("Portfolio snapshot error: %s", e)


async def run_payment_reminder():
    from notifications.payment_reminder import payment_reminder
    await payment_reminder.send_if_due()


async def update_news_outcomes():
    """
    Ambil semua news_items yang belum punya price_1h_after / price_24h_after,
    cek kalau sudah lewat horizon, isi harga & prediction_correct.
    """
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
    """Cek drawdown intraday, trip CB kalau perlu."""
    try:
        from engine.circuit_breaker import circuit_breaker
        capital_now = await db.get_current_capital()

        # Ambil snapshot 00:05 hari ini (start of day reference)
        history = await db.get_portfolio_history(days=2)
        if history:
            capital_start = float(history[0].get("total_capital") or capital_now)
            await circuit_breaker.check(capital_now, capital_start)
    except Exception as e:
        log.error("Circuit breaker monitor error: %s", e)


async def check_supabase_activity():
    """Keep-alive query agar Supabase free tier tidak pause."""
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