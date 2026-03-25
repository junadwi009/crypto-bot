"""
monitoring/dashboard_api.py
FastAPI router untuk monitoring dan debug.
Di-mount ke app utama di main.py.
Semua endpoint read-only — tidak ada yang mengubah state bot.
"""

from __future__ import annotations
import logging

from fastapi import APIRouter, HTTPException

from config.settings import settings
from database.client import db
from monitoring.health_check import health_checker
from utils.redis_client import redis

log = logging.getLogger("dashboard_api")

router = APIRouter(prefix="/api", tags=["monitoring"])


@router.get("/auth/config")
async def auth_config():
    """
    Expose PIN hash ke frontend untuk verifikasi lokal.
    SHA-256 hash tidak bisa di-reverse — aman dikirim ke browser.
    """
    return {"pin_hash": settings.BOT_PIN_HASH}


# ── Health & status ───────────────────────────────────────────────────────────

@router.get("/health/full")
async def full_health():
    """Status lengkap semua komponen."""
    return await health_checker.full_check()


@router.get("/status")
async def bot_status():
    """Ringkasan status bot untuk monitoring eksternal."""
    capital      = await db.get_current_capital()
    tier         = await db.get_current_tier()
    active_pairs = await db.get_active_pairs()
    open_trades  = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
    daily_pnl    = await db.get_total_pnl(days=1)
    is_paused    = bool(await redis.get("bot_paused"))
    is_stopping  = bool(await redis.get("bot_stopping"))
    claude_mode  = await redis.get("claude_mode") or "normal"

    from engine.circuit_breaker import circuit_breaker
    cb_status    = await circuit_breaker.get_status()

    return {
        "status":       "stopping" if is_stopping else "paused" if is_paused else "running",
        "paper_trade":  settings.PAPER_TRADE,
        "capital":      capital,
        "tier":         tier,
        "daily_pnl":    daily_pnl,
        "active_pairs": active_pairs,
        "open_trades":  len(open_trades),
        "claude_mode":  claude_mode,
        "circuit_breaker": cb_status,
    }


# ── Portfolio ─────────────────────────────────────────────────────────────────

@router.get("/portfolio/history")
async def portfolio_history(days: int = 30):
    """Riwayat modal dan PnL."""
    if days > 90:
        days = 90
    history = await db.get_portfolio_history(days)
    return {"days": days, "data": history}


@router.get("/portfolio/summary")
async def portfolio_summary():
    """Ringkasan portfolio untuk Opus dan dashboard."""
    return await db.get_weekly_summary(days=7)


# ── Trades ────────────────────────────────────────────────────────────────────

@router.get("/trades/open")
async def open_trades():
    """Semua posisi yang sedang terbuka."""
    trades = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
    return {"count": len(trades), "trades": trades}


@router.get("/trades/recent")
async def recent_trades(days: int = 7):
    """Trade yang sudah closed dalam N hari terakhir."""
    if days > 30:
        days = 30
    trades = await db.get_trades_for_period(
        days=days, is_paper=settings.PAPER_TRADE
    )
    closed  = [t for t in trades if t.get("status") == "closed"]
    winners = [t for t in closed if (t.get("pnl_usd") or 0) > 0]
    total_pnl = sum(float(t.get("pnl_usd") or 0) for t in closed)

    return {
        "days":        days,
        "total":       len(closed),
        "winners":     len(winners),
        "win_rate":    round(len(winners) / len(closed), 4) if closed else 0,
        "total_pnl":   round(total_pnl, 4),
        "trades":      closed,
    }


# ── Pairs & strategy ──────────────────────────────────────────────────────────

@router.get("/pairs")
async def all_pairs():
    """Konfigurasi semua pair."""
    pairs = await db.get_all_pairs()
    return {"pairs": [p.model_dump(mode="json") for p in pairs]}


@router.get("/pairs/{pair}/params")
async def pair_params(pair: str):
    """Parameter strategi untuk pair tertentu."""
    pair = pair.upper().replace("-", "/")
    params = await db.get_strategy_params(pair)
    return params.model_dump(mode="json")


@router.get("/pairs/{pair}/backtest")
async def pair_backtest(pair: str):
    """Hasil backtest terbaik untuk pair."""
    pair = pair.upper().replace("-", "/")
    result = await db.get_best_backtest(pair)
    if not result:
        raise HTTPException(status_code=404, detail=f"No backtest for {pair}")
    return result


# ── Claude & news ─────────────────────────────────────────────────────────────

@router.get("/claude/usage")
async def claude_usage():
    """Penggunaan dan biaya Claude bulan ini."""
    monthly_cost = await db.get_claude_cost_this_month()

    from brains.credit_monitor import credit_monitor
    balance   = await credit_monitor.get_balance() or 0
    burn_rate = await credit_monitor.get_burn_rate()
    days_left = await credit_monitor.get_days_remaining()
    mode      = await credit_monitor.get_claude_mode()

    return {
        "monthly_cost_usd": monthly_cost,
        "estimated_balance": balance,
        "burn_rate_per_day": burn_rate,
        "days_remaining":    days_left,
        "mode":              mode,
        "spending_limit":    settings.ANTHROPIC_SPENDING_LIMIT,
    }


@router.get("/opus/memory")
async def opus_memory(weeks: int = 4):
    """Riwayat evaluasi Opus."""
    if weeks > 12:
        weeks = 12
    memories = await db.get_recent_opus_memory(weeks)
    return {"weeks": weeks, "evaluations": memories}


@router.get("/news/recent")
async def recent_news(hours: int = 24):
    """Berita yang diproses dalam N jam terakhir."""
    if hours > 72:
        hours = 72
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    res = (
        db._get()
        .table("news_items")
        .select("headline, source, pairs_mentioned, haiku_relevance, "
                "haiku_sentiment, sonnet_action, published_at")
        .gte("published_at", since)
        .order("published_at", desc=True)
        .limit(50)
        .execute()
    )
    return {"hours": hours, "count": len(res.data or []), "news": res.data or []}


# ── Infra ─────────────────────────────────────────────────────────────────────

@router.get("/infra/fund")
async def infra_fund():
    """Saldo dan riwayat infra fund."""
    balance = await db.get_infra_balance()
    res = (
        db._get()
        .table("infra_fund")
        .select("*")
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    )
    return {
        "current_balance": balance,
        "transactions":    res.data or [],
    }


@router.get("/events/recent")
async def recent_events(hours: int = 24, severity: str | None = None):
    """Event log terbaru."""
    events = await db.get_recent_events(hours=hours, severity=severity)
    return {"hours": hours, "count": len(events), "events": events}