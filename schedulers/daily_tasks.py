"""
schedulers/daily_tasks.py
Semua task yang berjalan setiap hari.
Dipanggil oleh main_scheduler.py pada waktu yang sudah ditentukan.
"""

from __future__ import annotations
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from config.settings import settings
from database.client import db
from database.models import PortfolioSnapshot
from utils.redis_client import redis

log = logging.getLogger("daily_tasks")
WIB = ZoneInfo("Asia/Jakarta")


async def take_portfolio_snapshot():
    """
    Simpan snapshot modal dan status portfolio hari ini.
    Dijalankan setiap hari 00:05 WIB.
    """
    try:
        capital      = await db.get_current_capital()
        tier         = await db.get_current_tier()
        active_pairs = await db.get_active_pairs()
        daily_pnl    = await db.get_total_pnl(days=1)
        infra_bal    = await db.get_infra_balance()
        drawdown     = await db.get_max_drawdown(days=1)

        # Alokasi modal
        trading_cap = capital * 0.70
        infra_res   = capital * 0.15
        emrg_buf    = capital * 0.15

        snap = PortfolioSnapshot(
            snapshot_date    = date.today(),
            total_capital    = round(capital, 4),
            trading_capital  = round(trading_cap, 4),
            infra_reserve    = round(infra_res, 4),
            emergency_buffer = round(emrg_buf, 4),
            current_tier     = tier,
            active_pairs     = active_pairs,
            daily_pnl        = round(daily_pnl, 4),
            drawdown_pct     = round(drawdown, 4),
        )
        await db.save_portfolio_snapshot(snap)
        log.info(
            "Portfolio snapshot: capital=$%.2f tier=%s pnl=$%.2f",
            capital, tier, daily_pnl
        )

        # Cek naik tier
        await check_tier_upgrade(capital, tier)

        # Alokasikan 15% profit ke infra fund
        if daily_pnl > 0:
            infra_alloc = round(daily_pnl * 0.15, 4)
            await db.add_infra_credit(
                infra_alloc,
                f"15% profit allocation {date.today()}"
            )
            log.info("Infra fund credited: +$%.4f", infra_alloc)

    except Exception as e:
        log.error("Portfolio snapshot error: %s", e, exc_info=True)


async def check_tier_upgrade(capital: float, current_tier: str):
    """
    Cek apakah modal sudah cukup untuk naik tier.
    Kirim notif Telegram jika naik.
    """
    try:
        new_tier = settings.get_tier(capital)
        if new_tier == current_tier:
            return

        # Hitung berapa hari di tier sebelumnya
        history = await db.get_tier_history()
        days_in_prev = 0
        if history:
            last_change = history[0]
            try:
                ts_str  = last_change["changed_at"].replace("Z", "+00:00")
                last_dt = datetime.fromisoformat(ts_str)
                days_in_prev = (
                    datetime.now(tz=last_dt.tzinfo) - last_dt
                ).days
            except Exception:
                days_in_prev = 0

        await db.log_tier_change(
            current_tier, new_tier, capital, days_in_prev
        )

        from notifications.telegram_bot import telegram
        await telegram.send_tier_upgrade(
            current_tier, new_tier, capital, days_in_prev
        )

        # Update Claude limits otomatis (diambil dari settings)
        await db.log_event(
            "tier_upgraded",
            f"Tier: {current_tier} → {new_tier} | capital=${capital:.2f}",
            severity="info",
            data={"from": current_tier, "to": new_tier, "capital": capital},
        )
        log.info("Tier upgraded: %s → %s at $%.2f", current_tier, new_tier, capital)

    except Exception as e:
        log.error("Tier check error: %s", e)


async def run_payment_reminder():
    """
    Kirim reminder pembayaran Render + Anthropic jika perlu.
    Dijalankan jam 10:00 WIB setiap hari.
    """
    try:
        from notifications.payment_reminder import payment_reminder
        await payment_reminder.check_and_send()
    except Exception as e:
        log.error("Payment reminder error: %s", e)


async def update_news_outcomes():
    """
    Isi price_1h_after dan price_24h_after untuk berita yang pending.
    Dijalankan setiap jam.
    """
    try:
        from news.outcome_tracker import outcome_tracker
        await outcome_tracker.update_pending_outcomes()
    except Exception as e:
        log.error("News outcome update error: %s", e)


async def update_lrhr_scores():
    """
    Hitung ulang LRHR score untuk semua pair aktif.
    Dijalankan setiap hari 01:00 WIB.
    """
    try:
        from engine.portfolio_manager import portfolio_manager

        active_pairs = await db.get_active_pairs()
        for pair in active_pairs:
            score = await portfolio_manager.calc_lrhr_score(pair)
            win_rate = await db.get_win_rate(days=30)
            await db.update_pair_lrhr_score(pair, score, win_rate)
            log.debug("LRHR score updated: %s = %.3f", pair, score)

        log.info("LRHR scores updated for %d pairs", len(active_pairs))

    except Exception as e:
        log.error("LRHR update error: %s", e)


async def monitor_circuit_breaker():
    """
    Cek drawdown harian dan trip circuit breaker jika perlu.
    Dijalankan setiap 15 menit.
    """
    try:
        from engine.circuit_breaker import circuit_breaker

        capital_now   = await db.get_current_capital()
        history       = await db.get_portfolio_history(days=1)
        capital_start = float(history[-1]["total_capital"]) if history else capital_now

        await circuit_breaker.check(capital_now, capital_start)

    except Exception as e:
        log.error("Circuit breaker monitor error: %s", e)


async def check_supabase_activity():
    """
    Kirim query ringan ke Supabase agar free tier tidak di-pause.
    Supabase pause project jika idle > 7 hari.
    Dijalankan setiap hari 06:00 WIB.
    """
    try:
        await db.ping()
        log.debug("Supabase activity ping sent")
    except Exception as e:
        log.error("Supabase ping failed: %s", e)


async def send_daily_summary():
    """
    Kirim ringkasan harian ke Telegram jam 20:00 WIB.
    Hanya jika ada aktivitas trading hari ini.
    """
    try:
        trades    = await db.get_trades_for_period(
            days=1, is_paper=settings.PAPER_TRADE
        )
        closed    = [t for t in trades if t.get("status") == "closed"]

        if not closed:
            return  # Tidak ada trade hari ini — skip

        total_pnl = sum(float(t.get("pnl_usd") or 0) for t in closed)
        wins      = sum(1 for t in closed if (t.get("pnl_usd") or 0) > 0)
        win_rate  = wins / len(closed) if closed else 0
        capital   = await db.get_current_capital()
        sign      = "+" if total_pnl >= 0 else ""

        from notifications.telegram_bot import telegram
        await telegram.send(
            f"Ringkasan hari ini\n\n"
            f"Trades:   {len(closed)}\n"
            f"Win rate: {win_rate*100:.0f}%\n"
            f"PnL:      {sign}${total_pnl:.2f}\n"
            f"Modal:    ${capital:.2f}"
        )

    except Exception as e:
        log.error("Daily summary error: %s", e)
