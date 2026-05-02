"""
monitoring/dashboard_api.py
HTTP endpoints untuk frontend dashboard.

PATCHED 2026-05-02 (revisi 2):
- AUTH SECURITY FIX:
  * Hapus /api/auth/config (bocorin SHA-256 hash 6-digit PIN)
  * Tambah /api/auth/login POST yang verifikasi PIN di server
  * Token sesi HMAC-SHA256, dikirim lewat httpOnly cookie
- /api/status PUBLIC — info minimal supaya App.jsx bisa polling header
  sebelum user login (tidak return data sensitif)
- BUG FIX: /api/infra/fund dan /api/portfolio/allocation pakai
  db.get_infra_balance() (settings.INFRA_FUND_INITIAL tidak ada)
- DEPRECATION FIX: utcfromtimestamp → datetime.fromtimestamp(ts, tz=timezone.utc)
- NEW endpoint: /api/learning/summary
"""

from __future__ import annotations
import hmac
import hashlib
import json
import logging
import secrets
import time
import os
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from config.settings import settings
from database.client import db
from utils.redis_client import redis

log = logging.getLogger("dashboard_api")

router = APIRouter(prefix="/api")

SESSION_COOKIE = "cb_session"
SESSION_TTL    = 4 * 60 * 60   # 4 jam

# Server-side secret untuk HMAC. Diambil dari env var.
# Kalau SESSION_SECRET tidak diset, fallback ke hash BOT_PIN_HASH+salt.
_SESSION_SECRET = os.getenv("SESSION_SECRET") or hashlib.sha256(
    (settings.BOT_PIN_HASH + "session_salt").encode()
).hexdigest()

# Rate limit untuk login attempt
LOGIN_RATE_KEY    = "login_attempts:{ip}"
LOGIN_MAX_PER_HR  = 10


# ── Auth helpers ──────────────────────────────────────────────────────────

def _make_session_token() -> str:
    """Generate session token: <random>.<expiry>.<hmac>"""
    rand = secrets.token_urlsafe(24)
    expiry = int(time.time()) + SESSION_TTL
    payload = f"{rand}.{expiry}"
    sig = hmac.new(
        _SESSION_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{sig}"


def _validate_session_token(token: str) -> bool:
    if not token:
        return False
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return False
        rand, expiry_str, sig = parts
        expiry = int(expiry_str)
        if expiry < int(time.time()):
            return False
        payload = f"{rand}.{expiry_str}"
        expected = hmac.new(
            _SESSION_SECRET.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


async def require_auth(request: Request):
    """FastAPI dependency: cek session cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    if not _validate_session_token(token or ""):
        raise HTTPException(status_code=401, detail="unauthorized")
    return True


# ── Auth endpoints ────────────────────────────────────────────────────────

@router.post("/auth/login")
async def auth_login(request: Request, response: Response):
    """Verifikasi PIN server-side. Set httpOnly cookie kalau sukses."""
    client_ip = request.client.host if request.client else "unknown"
    rate_key  = LOGIN_RATE_KEY.format(ip=client_ip)
    count     = await redis.incr(rate_key)
    if count == 1:
        await redis.expire(rate_key, 3600)
    if int(count) > LOGIN_MAX_PER_HR:
        raise HTTPException(status_code=429, detail="too_many_attempts")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_body")

    pin = body.get("pin", "")
    if not isinstance(pin, str) or len(pin) != 6 or not pin.isdigit():
        raise HTTPException(status_code=400, detail="invalid_pin_format")

    expected_hash = settings.BOT_PIN_HASH
    actual_hash   = hashlib.sha256(pin.encode()).hexdigest()

    if not hmac.compare_digest(expected_hash, actual_hash):
        await db.log_event(
            event_type="auth_failed",
            message=f"Login failed from {client_ip}",
            severity="warning",
        )
        raise HTTPException(status_code=401, detail="invalid_pin")

    token = _make_session_token()
    response.set_cookie(
        key      = SESSION_COOKIE,
        value    = token,
        max_age  = SESSION_TTL,
        httponly = True,
        secure   = not settings.PAPER_TRADE,
        samesite = "lax",
    )
    await redis.delete(rate_key)
    return {"ok": True, "expires_in": SESSION_TTL}


@router.post("/auth/logout")
async def auth_logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/auth/check")
async def auth_check(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    return {"authenticated": _validate_session_token(token or "")}


# ── PUBLIC endpoint ───────────────────────────────────────────────────────
# /api/status PUBLIC supaya App.jsx bisa render header bar sebelum user login.
# Hanya return info minimal/non-sensitif.

@router.get("/status")
async def status_public():
    try:
        capital = await db.get_current_capital()
    except Exception:
        capital = 0.0
    try:
        paused  = bool(await redis.get("bot_paused"))
        cb_trip = bool(await redis.get("circuit_breaker_tripped"))
    except Exception:
        paused, cb_trip = False, False
    try:
        tier = await db.get_current_tier()
    except Exception:
        tier = "seed"

    bot_status = "stopped" if cb_trip else "paused" if paused else "running"
    return {
        "status":          bot_status,
        "capital":         capital,
        "paper_trade":     settings.PAPER_TRADE,
        "tier":            tier,
        "circuit_breaker": cb_trip,
    }


# ── Protected endpoints — semua butuh require_auth ────────────────────────

@router.get("/portfolio/summary")
async def portfolio_summary(_=Depends(require_auth)):
    capital   = await db.get_current_capital()
    open_t    = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
    win_rate  = await db.get_win_rate(days=30)
    pnl_30d   = await db.get_total_pnl(days=30)

    return {
        "capital_usd":    capital,
        "open_positions": len(open_t),
        "win_rate_30d":   win_rate,
        "pnl_30d":        pnl_30d,
        "tier":           await db.get_current_tier(),
        "paper_trade":    settings.PAPER_TRADE,
    }


@router.get("/portfolio/history")
async def portfolio_history(days: int = 30, _=Depends(require_auth)):
    history = await db.get_portfolio_history(days=days)
    return {"days": days, "history": history}


@router.get("/portfolio/allocation")
async def portfolio_allocation(_=Depends(require_auth)):
    capital = await db.get_current_capital()
    try:
        infra = await db.get_infra_balance()
    except Exception:
        infra = 0.0
    trading = max(0, capital - infra)
    buffer  = capital * 0.05
    return {
        "trading": round(trading - buffer, 2),
        "infra":   round(infra, 2),
        "buffer":  round(buffer, 2),
        "total":   round(capital, 2),
    }


@router.get("/trades/open")
async def trades_open(_=Depends(require_auth)):
    trades = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
    return {"trades": trades}


@router.get("/trades/recent")
async def trades_recent(days: int = 14, _=Depends(require_auth)):
    trades = await db.get_trades_for_period(days=days)
    return {"days": days, "trades": trades}


@router.get("/pairs")
async def pairs(_=Depends(require_auth)):
    all_pairs = await db.get_all_pairs()
    return {
        "pairs": [
            {
                "pair":     p.pair,
                "active":   p.active,
                "category": p.category,
                "min_capital": float(p.min_capital_required),
            }
            for p in all_pairs
        ],
    }


@router.get("/pairs/{pair_dash}/params")
async def pair_params(pair_dash: str, _=Depends(require_auth)):
    pair = pair_dash.replace("-", "/")
    params = await db.get_strategy_params(pair)
    return {
        "pair": pair,
        "params": {
            "rsi_period":             params.rsi_period,
            "rsi_oversold":           params.rsi_oversold,
            "rsi_overbought":         params.rsi_overbought,
            "stop_loss_pct":          float(params.stop_loss_pct),
            "take_profit_pct":        float(params.take_profit_pct),
            "atr_no_trade_threshold": float(params.atr_no_trade_threshold),
            "position_multiplier":    float(params.position_multiplier),
        },
    }


@router.get("/claude/usage")
async def claude_usage(_=Depends(require_auth)):
    cost_month = await db.get_claude_cost_this_month()
    haiku_today  = await db.get_claude_calls_today("haiku")
    sonnet_today = await db.get_claude_calls_today("sonnet")
    opus_today   = await db.get_claude_calls_today("opus")

    capital = await db.get_current_capital()
    limits  = settings.get_claude_limits(capital)

    return {
        "cost_this_month": cost_month,
        "calls_today": {
            "haiku":  haiku_today,
            "sonnet": sonnet_today,
            "opus":   opus_today,
        },
        "limits": limits,
        "spending_limit": settings.ANTHROPIC_SPENDING_LIMIT,
    }


@router.get("/opus/memory")
async def opus_memory(weeks: int = 8, _=Depends(require_auth)):
    memory = await db.get_recent_opus_memory(weeks=weeks)
    return {"weeks": weeks, "memory": memory}


@router.get("/opus/latest-actions")
async def opus_latest_actions(_=Depends(require_auth)):
    memory = await db.get_recent_opus_memory(weeks=1)
    if not memory:
        return {"actions": [], "week_start": None}
    latest = memory[0]
    actions = latest.get("actions_required") or []
    if isinstance(actions, str):
        try:
            actions = json.loads(actions)
        except Exception:
            actions = []
    return {
        "actions":    actions[:5],
        "week_start": latest.get("week_start"),
    }


@router.get("/news/recent")
async def news_recent(hours: int = 24, _=Depends(require_auth)):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    res = (
        db._get()
        .table("news_items")
        .select("*")
        .gte("published_at", since)
        .order("published_at", desc=True)
        .limit(50)
        .execute()
    )
    return {"hours": hours, "news": res.data or []}


@router.get("/events/recent")
async def events_recent(hours: int = 48, _=Depends(require_auth)):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    res = (
        db._get()
        .table("bot_events")
        .select("*")
        .gte("created_at", since)
        .order("created_at", desc=True)
        .limit(100)
        .execute()
    )
    return {"hours": hours, "events": res.data or []}


@router.get("/infra/fund")
async def infra_fund(_=Depends(require_auth)):
    """Saldo dan riwayat infra fund."""
    try:
        balance = await db.get_infra_balance()
    except Exception as e:
        log.warning("infra_balance lookup failed: %s", e)
        balance = 0.0
    try:
        res = (
            db._get()
            .table("infra_fund")
            .select("*")
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        transactions = res.data or []
    except Exception:
        transactions = []
    return {
        "current_balance": round(balance, 2),
        "transactions":    transactions,
        "next_due":        (date.today() + timedelta(days=30)).isoformat(),
    }


# ── Market data ────────────────────────────────────────────────────────────

@router.get("/price/{pair_dash}")
async def get_price(pair_dash: str, _=Depends(require_auth)):
    from exchange.bybit_client import bybit
    pair = pair_dash.replace("-", "/")
    try:
        price = await bybit.get_price(pair)
        return {"pair": pair, "price": price}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"price_unavailable: {e}")


@router.get("/ticker/all")
async def ticker_all(_=Depends(require_auth)):
    from exchange.bybit_client import bybit
    active = await db.get_active_pairs()
    out = {}
    for pair in active:
        try:
            t = await bybit.get_ticker(pair)
            out[pair] = t
        except Exception:
            pass
    return {"tickers": out}


@router.get("/ohlcv/{pair_dash}")
async def ohlcv(pair_dash: str, interval: str = "15", limit: int = 80,
                 _=Depends(require_auth)):
    from exchange.bybit_client import bybit
    pair = pair_dash.replace("-", "/")
    try:
        candles = await bybit.get_ohlcv(pair, interval=interval, limit=limit)
        for c in candles:
            ts = c["timestamp"]
            if ts > 1e12:
                ts = ts // 1000
            c["iso_utc"] = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        return {"pair": pair, "interval": interval, "candles": candles}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ohlcv_error: {e}")


# ── Learning summary (NEW) ────────────────────────────────────────────────

@router.get("/learning/summary")
async def learning_summary(_=Depends(require_auth)):
    """
    Ringkas state pembelajaran:
      - News weights terbaru (akurasi per kategori)
      - Param changes 4 minggu terakhir + outcome
      - Trade source breakdown
    """
    weights = await db.get_news_weights()
    weights_out = {
        cat: {
            "weight":       float(w.weight),
            "accuracy_1h":  float(w.accuracy_1h),
            "accuracy_24h": float(w.accuracy_24h),
            "sample_size":  w.sample_size,
        }
        for cat, w in weights.items()
    }

    memory = await db.get_recent_opus_memory(weeks=4)
    memory_out = []
    for m in memory:
        memory_out.append({
            "week_start":     str(m.get("week_start")),
            "win_rate":       float(m.get("win_rate") or 0),
            "total_pnl":      float(m.get("total_pnl") or 0),
            "params_updated": m.get("params_updated") or {},
            "patterns_count": len(m.get("patterns_found") or []),
        })

    trades = await db.get_trades_for_period(days=7)
    sources: dict[str, dict] = {}
    for t in trades:
        src = t.get("trigger_source") or "unknown"
        if src not in sources:
            sources[src] = {"count": 0, "wins": 0, "total_pnl": 0.0}
        sources[src]["count"] += 1
        pnl = float(t.get("pnl_usd") or 0)
        sources[src]["total_pnl"] += pnl
        if pnl > 0:
            sources[src]["wins"] += 1
    for src in sources:
        s = sources[src]
        s["win_rate"] = s["wins"] / s["count"] if s["count"] else 0
        s["avg_pnl"]  = s["total_pnl"] / s["count"] if s["count"] else 0

    return {
        "news_weights":    weights_out,
        "opus_history":    memory_out,
        "source_breakdown": sources,
    }