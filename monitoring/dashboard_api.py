"""
monitoring/dashboard_api.py
FastAPI router untuk monitoring dan debug.
Di-mount ke app utama di main.py.
Semua endpoint read-only — tidak ada yang mengubah state bot.

UPDATED 2026-04-16:
  - Tambah GET /api/price/{pair}     — harga realtime dari Bybit
  - Tambah GET /api/ohlcv/{pair}     — OHLCV candle untuk chart
  - Tambah GET /api/ticker/all       — semua pair sekaligus untuk ticker tape
  - Tambah GET /api/portfolio/allocation — alokasi modal (trading/infra/buffer)
  - Tambah GET /api/opus/latest-actions  — P0/P1 actions terbaru untuk banner
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
    claude_mode  = await redis.get("claude_mode") or "normal"

    from main import _stopping as is_stopping
    from engine.circuit_breaker import circuit_breaker
    cb_status = await circuit_breaker.get_status()

    return {
        "status":          "stopping" if is_stopping else "paused" if is_paused else "running",
        "paper_trade":     settings.PAPER_TRADE,
        "capital":         capital,
        "tier":            tier,
        "daily_pnl":       daily_pnl,
        "active_pairs":    active_pairs,
        "open_trades":     len(open_trades),
        "claude_mode":     claude_mode,
        "circuit_breaker": cb_status,
    }


# ── Price & market data (NEW) ─────────────────────────────────────────────────

@router.get("/price/{pair}")
async def get_price(pair: str):
    """
    Harga realtime satu pair dari Bybit.
    pair: BTC-USDT atau BTCUSDT (di-normalize otomatis)
    Dipolling frontend setiap 5 detik untuk ticker + price cards.
    """
    from exchange.bybit_client import bybit
    symbol = pair.upper().replace("-", "/")
    try:
        ticker = await bybit.get_ticker(symbol)
        return {
            "symbol":     symbol,
            "price":      ticker["last"],
            "bid":        ticker["bid"],
            "ask":        ticker["ask"],
            "volume_24h": ticker["volume_24h"],
            "change_24h": ticker["change_24h"],
            "change_24h_pct": round(ticker["change_24h"] * 100, 3),
        }
    except Exception as e:
        log.warning("Price fetch failed for %s: %s", symbol, e)
        raise HTTPException(status_code=503, detail=f"Price unavailable: {e}")


@router.get("/ticker/all")
async def get_all_tickers():
    """
    Harga semua pair aktif + pair yang dimonitor sekaligus.
    Dipakai ticker tape — 1 request untuk semua pair.
    Fallback graceful jika Bybit tidak bisa dijangkau.
    """
    from exchange.bybit_client import bybit

    # Ambil semua pair dari DB + tambah pair populer untuk ticker
    active_pairs = await db.get_active_pairs()
    all_pairs = list(dict.fromkeys([
        *active_pairs,
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "AVAX/USDT",
    ]))

    results = []
    for pair in all_pairs:
        try:
            ticker = await bybit.get_ticker(pair)
            results.append({
                "symbol":         pair,
                "price":          ticker["last"],
                "change_24h_pct": round(ticker["change_24h"] * 100, 3),
                "volume_24h":     ticker["volume_24h"],
                "is_active":      pair in active_pairs,
            })
        except Exception as e:
            log.debug("Ticker skip %s: %s", pair, e)

    return {"count": len(results), "tickers": results}


@router.get("/ohlcv/{pair}")
async def get_ohlcv(pair: str, interval: str = "15", limit: int = 80):
    """
    OHLCV candle data dari Bybit untuk chart.
    pair: BTC-USDT
    interval: 1,3,5,15,30,60,120,240,D
    limit: max 200
    Dipakai CandlestickChart di dashboard.
    """
    from exchange.bybit_client import bybit
    symbol = pair.upper().replace("-", "/")
    if limit > 200:
        limit = 200
    try:
        candles = await bybit.get_ohlcv(symbol, interval=interval, limit=limit)
        # Tambah isUp flag dan format time untuk frontend
        result = []
        for c in candles:
            ts = c["timestamp"]
            dt = __import__("datetime").datetime.utcfromtimestamp(ts / 1000)
            result.append({
                "time":   dt.strftime("%H:%M"),
                "open":   c["open"],
                "high":   c["high"],
                "low":    c["low"],
                "close":  c["close"],
                "volume": c["volume"],
                "isUp":   c["close"] >= c["open"],
            })
        # Hitung BB dan RSI di backend supaya frontend ringan
        result = _attach_indicators(result)
        return {"symbol": symbol, "interval": interval, "count": len(result), "candles": result}
    except Exception as e:
        log.warning("OHLCV fetch failed for %s: %s", symbol, e)
        raise HTTPException(status_code=503, detail=f"OHLCV unavailable: {e}")


def _attach_indicators(candles: list[dict]) -> list[dict]:
    """Hitung BB(20) dan RSI(14) dan attach ke candles."""
    closes = [c["close"] for c in candles]
    n = len(closes)

    for i, c in enumerate(candles):
        # Bollinger Bands (period=20)
        if i >= 19:
            sl = closes[i - 19: i + 1]
            mean = sum(sl) / 20
            std  = (sum((x - mean) ** 2 for x in sl) / 20) ** 0.5
            c["bbUpper"] = round(mean + 2 * std, 2)
            c["bbLower"] = round(mean - 2 * std, 2)
            c["bbMid"]   = round(mean, 2)
        else:
            c["bbUpper"] = None
            c["bbLower"] = None
            c["bbMid"]   = None

        # RSI (period=14)
        if i >= 14:
            gains, losses = 0.0, 0.0
            for j in range(i - 13, i + 1):
                d = closes[j] - closes[j - 1]
                if d > 0:
                    gains += d
                else:
                    losses -= d
            rs = gains / (losses or 0.0001)
            c["rsi"] = round(100 - 100 / (1 + rs), 1)
        else:
            c["rsi"] = None

    return candles


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


@router.get("/portfolio/allocation")
async def portfolio_allocation():
    """
    Alokasi modal saat ini: trading 70%, infra 15%, buffer 15%.
    Dipakai chart alokasi modal di dashboard.
    """
    capital   = await db.get_current_capital()
    infra_bal = await db.get_infra_balance()
    trading   = round(capital * 0.70, 2)
    infra_r   = round(capital * 0.15, 2)
    emergency = round(capital * 0.15, 2)
    return {
        "total":     round(capital, 2),
        "trading":   trading,
        "infra":     infra_r,
        "emergency": emergency,
        "infra_fund_balance": round(infra_bal, 2),
    }


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
    closed    = [t for t in trades if t.get("status") == "closed"]
    winners   = [t for t in closed if (t.get("pnl_usd") or 0) > 0]
    total_pnl = sum(float(t.get("pnl_usd") or 0) for t in closed)
    return {
        "days":      days,
        "total":     len(closed),
        "winners":   len(winners),
        "win_rate":  round(len(winners) / len(closed), 4) if closed else 0,
        "total_pnl": round(total_pnl, 4),
        "trades":    closed,
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
    pair   = pair.upper().replace("-", "/")
    params = await db.get_strategy_params(pair)
    return params.model_dump(mode="json")


@router.get("/pairs/{pair}/backtest")
async def pair_backtest(pair: str):
    """Hasil backtest terbaik untuk pair."""
    pair   = pair.upper().replace("-", "/")
    result = await db.get_best_backtest(pair)
    if not result:
        raise HTTPException(status_code=404, detail=f"No backtest for {pair}")
    return result


# ── Claude & news ─────────────────────────────────────────────────────────────

@router.get("/claude/usage")
async def claude_usage():
    """Penggunaan dan biaya Claude bulan ini + burn rate."""
    monthly_cost = await db.get_claude_cost_this_month()
    from brains.credit_monitor import credit_monitor
    balance   = await credit_monitor.get_balance() or 0
    burn_rate = await credit_monitor.get_burn_rate()
    days_left = await credit_monitor.get_days_remaining()
    mode      = await credit_monitor.get_claude_mode()
    return {
        "monthly_cost_usd":  monthly_cost,
        "estimated_balance": balance,
        "burn_rate_per_day": burn_rate,
        "days_remaining":    days_left,
        "mode":              mode,
        "spending_limit":    settings.ANTHROPIC_SPENDING_LIMIT,
        "total_cost_usd":    monthly_cost,
    }


@router.get("/opus/memory")
async def opus_memory(weeks: int = 4):
    """Riwayat evaluasi Opus."""
    if weeks > 12:
        weeks = 12
    memories = await db.get_recent_opus_memory(weeks)
    return {"weeks": weeks, "evaluations": memories}


@router.get("/opus/latest-actions")
async def opus_latest_actions():
    """
    Ambil actions P0/P1 dari evaluasi Opus terbaru.
    Dipakai sticky banner di dashboard.
    """
    actions = await db.get_latest_opus_actions()
    p0 = [a for a in actions if a.get("priority") == "P0"]
    p1 = [a for a in actions if a.get("priority") == "P1"]
    return {
        "has_critical": len(p0) > 0,
        "p0":           p0,
        "p1":           p1,
        "total":        len(actions),
    }


@router.get("/news/recent")
async def recent_news(hours: int = 24):
    """Berita yang diproses pipeline Claude dalam N jam terakhir."""
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
    """Event log terbaru — trade_opened, order_error, circuit_breaker_tripped, dll."""
    events = await db.get_recent_events(hours=hours, severity=severity)
    return {"hours": hours, "count": len(events), "events": events}