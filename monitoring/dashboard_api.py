"""
monitoring/dashboard_api.py
HTTP endpoints untuk frontend dashboard.

PATCHED 2026-05-02 (revisi 3 — Vercel deployment):
- Cookie samesite + secure dideteksi via env DEPLOY_ENV:
  * production (Vercel + Render): samesite=none, secure=true
    (cross-origin Vercel→Render butuh ini, browser modern wajib secure
     untuk samesite=none)
  * development (localhost):     samesite=lax,  secure=false
- /api/auth/login route_path tetap, frontend kirim PIN lewat POST
- Endpoint baru:
  * POST /api/capital/inject — manual injection oleh user (skip Opus)
  * GET  /api/capital/pending-injections — list rekomendasi pending
  * POST /api/capital/approve/{injection_id}
  * POST /api/capital/reject/{injection_id}
  * GET  /api/auto-evolution/recent-actions — log auto-activation/deactivation
- /api/status PUBLIC (App.jsx polling header sebelum login)
- /api/infra/fund pakai db.get_infra_balance()
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

# Detect deployment mode dari env. Production = cross-origin (Vercel→Render).
# Set ALLOWED_ORIGINS di Render env var → otomatis production mode.
DEPLOY_ENV = os.getenv("DEPLOY_ENV", "").lower()
if not DEPLOY_ENV:
    # Auto-detect: kalau ada ALLOWED_ORIGINS env, anggap production
    DEPLOY_ENV = "production" if os.getenv("ALLOWED_ORIGINS", "").strip() else "development"

IS_PRODUCTION = DEPLOY_ENV == "production"

# Cookie attributes per environment
COOKIE_SAMESITE = "none" if IS_PRODUCTION else "lax"
COOKIE_SECURE   = IS_PRODUCTION  # samesite=none requires secure=true di browser modern

log.info("Dashboard API mode: %s (cookie samesite=%s, secure=%s)",
         DEPLOY_ENV, COOKIE_SAMESITE, COOKIE_SECURE)

_SESSION_SECRET = os.getenv("SESSION_SECRET") or hashlib.sha256(
    (settings.BOT_PIN_HASH + "session_salt").encode()
).hexdigest()

LOGIN_RATE_KEY    = "login_attempts:{ip}"
LOGIN_MAX_PER_HR  = 10


# ── Auth helpers ──────────────────────────────────────────────────────────

def _make_session_token() -> str:
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


def _set_session_cookie(response: Response, token: str):
    """Set httpOnly cookie dengan attribute yang sesuai env."""
    response.set_cookie(
        key      = SESSION_COOKIE,
        value    = token,
        max_age  = SESSION_TTL,
        httponly = True,
        secure   = COOKIE_SECURE,
        samesite = COOKIE_SAMESITE,
    )


async def require_auth(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not _validate_session_token(token or ""):
        raise HTTPException(status_code=401, detail="unauthorized")
    return True


# ── Auth endpoints ────────────────────────────────────────────────────────

@router.post("/auth/login")
async def auth_login(request: Request, response: Response):
    # Rate limit
    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )
    rate_key = LOGIN_RATE_KEY.format(ip=client_ip)
    count    = await redis.incr(rate_key)
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
    _set_session_cookie(response, token)
    await redis.delete(rate_key)
    return {"ok": True, "expires_in": SESSION_TTL}


@router.post("/auth/logout")
async def auth_logout(response: Response):
    response.delete_cookie(
        SESSION_COOKIE,
        samesite = COOKIE_SAMESITE,
        secure   = COOKIE_SECURE,
    )
    return {"ok": True}


@router.get("/auth/check")
async def auth_check(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    return {"authenticated": _validate_session_token(token or "")}


# ── PUBLIC endpoint ───────────────────────────────────────────────────────

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

    # Frontend (Dashboard, header) expects daily_pnl, active_pairs,
    # open_trades, plus circuit_breaker as an object with .tripped.
    try:
        daily_pnl = await db.get_total_pnl(days=1)
    except Exception:
        daily_pnl = 0.0
    try:
        active_pairs = await db.get_active_pairs()
    except Exception:
        active_pairs = []
    try:
        open_trades = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
        open_count  = len(open_trades)
    except Exception:
        open_count = 0

    bot_status = "stopped" if cb_trip else "paused" if paused else "running"
    return {
        "status":          bot_status,
        "capital":         capital,
        "paper_trade":     settings.PAPER_TRADE,
        "tier":            tier,
        "daily_pnl":       daily_pnl,
        "active_pairs":    active_pairs,
        "open_trades":     open_count,
        "circuit_breaker": {"tripped": cb_trip},
    }


# ── Protected endpoints ───────────────────────────────────────────────────

@router.get("/portfolio/summary")
async def portfolio_summary(_=Depends(require_auth)):
    capital      = await db.get_current_capital()
    open_t       = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
    win_rate_30  = await db.get_win_rate(days=30)
    pnl_30       = await db.get_total_pnl(days=30)
    win_rate_7   = await db.get_win_rate(days=7)
    pnl_7        = await db.get_total_pnl(days=7)
    trades_7     = await db.get_trades_for_period(days=7)
    try:
        max_dd = await db.get_max_drawdown(days=30)
    except Exception:
        max_dd = 0.0

    return {
        "capital_usd":    capital,
        "open_positions": len(open_t),
        "win_rate_30d":   win_rate_30,
        "pnl_30d":        pnl_30,
        "tier":           await db.get_current_tier(),
        "paper_trade":    settings.PAPER_TRADE,
        # Aliases consumed by Dashboard.jsx StatCards (7-day window)
        "win_rate":       win_rate_7,
        "total_trades":   len(trades_7),
        "total_pnl":      pnl_7,
        "max_drawdown":   max_dd,
    }


@router.get("/portfolio/history")
async def portfolio_history(days: int = 30, _=Depends(require_auth)):
    history = await db.get_portfolio_history(days=days)
    # Frontend EquityChart and Dashboard EquityMini read `data.data`
    return {"days": days, "history": history, "data": history}


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
    cost_month   = await db.get_claude_cost_this_month()
    haiku_today  = await db.get_claude_calls_today("haiku")
    sonnet_today = await db.get_claude_calls_today("sonnet")
    opus_today   = await db.get_claude_calls_today("opus")

    capital = await db.get_current_capital()
    limits  = settings.get_claude_limits(capital)

    # Frontend ClaudeCard expects monthly_cost_usd, estimated_balance,
    # burn_rate_per_day, days_remaining, mode. Pull them from credit_monitor.
    try:
        from brains.credit_monitor import credit_monitor
        balance   = await credit_monitor.get_balance()
        burn_day  = await credit_monitor.get_burn_rate(days=7)
        days_left = await credit_monitor.get_days_remaining()
        mode      = await credit_monitor.get_claude_mode()
    except Exception as e:
        log.warning("credit_monitor lookup failed: %s", e)
        balance, burn_day, days_left, mode = 0.0, 0.0, 0.0, "normal"

    return {
        # Legacy keys
        "cost_this_month": cost_month,
        "calls_today": {
            "haiku":  haiku_today,
            "sonnet": sonnet_today,
            "opus":   opus_today,
        },
        "limits": limits,
        "spending_limit": settings.ANTHROPIC_SPENDING_LIMIT,
        # Frontend-expected keys
        "monthly_cost_usd":   round(cost_month, 4),
        "estimated_balance":  round(float(balance or 0), 2),
        "burn_rate_per_day":  round(float(burn_day or 0), 4),
        "days_remaining":     float(days_left or 0),
        "mode":               mode or "normal",
    }


@router.get("/opus/memory")
async def opus_memory(weeks: int = 8, _=Depends(require_auth)):
    memory = await db.get_recent_opus_memory(weeks=weeks)

    # Build evaluations payload that OpusPanel.jsx expects.
    evaluations: list[dict] = []
    for m in memory or []:
        actions = m.get("actions_required") or []
        if isinstance(actions, str):
            try:
                actions = json.loads(actions)
            except Exception:
                actions = []
        params_updated = m.get("params_updated") or {}
        if isinstance(params_updated, str):
            try:
                params_updated = json.loads(params_updated)
            except Exception:
                params_updated = {}
        # Hide the _auto meta key from the dashboard params view
        params_clean = {
            k: v for k, v in params_updated.items() if k != "_auto"
        } if isinstance(params_updated, dict) else {}

        evaluations.append({
            "week_start": str(m.get("week_start") or ""),
            "week_end":   str(m.get("week_end")   or ""),
            "summary": {
                "win_rate":     float(m.get("win_rate")     or 0),
                "total_pnl":    float(m.get("total_pnl")    or 0),
                "total_trades": int(m.get("total_trades")   or 0),
                "sharpe_ratio": float(m.get("sharpe_ratio") or 0),
                "max_drawdown": float(m.get("max_drawdown") or 0),
                "assessment":   m.get("assessment") or "",
            },
            "actions_required": actions,
            "params_updated":   params_clean,
            "patterns_found":   m.get("patterns_found") or [],
            "token_cost":       float(m.get("token_cost") or 0),
        })

    return {"weeks": weeks, "memory": memory, "evaluations": evaluations}


@router.get("/opus/latest-actions")
async def opus_latest_actions(_=Depends(require_auth)):
    memory = await db.get_recent_opus_memory(weeks=1)
    if not memory:
        return {
            "actions": [], "week_start": None,
            "has_critical": False, "p0": [], "p1": [], "p2": [],
        }
    latest = memory[0]
    actions = latest.get("actions_required") or []
    if isinstance(actions, str):
        try:
            actions = json.loads(actions)
        except Exception:
            actions = []

    p0 = [a for a in actions if isinstance(a, dict) and a.get("priority") == "P0"]
    p1 = [a for a in actions if isinstance(a, dict) and a.get("priority") == "P1"]
    p2 = [a for a in actions if isinstance(a, dict) and a.get("priority") == "P2"]

    return {
        "actions":      actions[:5],
        "week_start":   latest.get("week_start"),
        "has_critical": bool(p0),
        "p0":           p0,
        "p1":           p1,
        "p2":           p2,
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
        ticker = await bybit.get_ticker(pair)
        # Bybit returns price24hPcnt as a decimal (0.0123 = +1.23%).
        change_pct = float(ticker.get("change_24h") or 0) * 100
        return {
            "pair":            pair,
            "price":           float(ticker.get("last") or 0),
            "change_24h_pct":  round(change_pct, 4),
            "volume_24h":      float(ticker.get("volume_24h") or 0),
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"price_unavailable: {e}")


@router.get("/ticker/all")
async def ticker_all(_=Depends(require_auth)):
    from exchange.bybit_client import bybit
    # Show the same default set the frontend ticker tape renders even if
    # no pair is active yet — so the tape isn't blank in early stages.
    active = set(await db.get_active_pairs())
    universe = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]
    for p in active:
        if p not in universe:
            universe.append(p)

    tickers: list[dict] = []
    for pair in universe:
        try:
            t = await bybit.get_ticker(pair)
            tickers.append({
                "symbol":          pair,
                "price":           float(t.get("last") or 0),
                "change_24h_pct":  round(float(t.get("change_24h") or 0) * 100, 4),
                "volume_24h":      float(t.get("volume_24h") or 0),
                "is_active":       pair in active,
            })
        except Exception:
            pass
    return {"tickers": tickers}


def _compute_indicators(candles: list[dict]) -> list[dict]:
    """Add `time`, `isUp`, RSI(14) and Bollinger(20, 2σ) fields per candle."""
    if not candles:
        return candles

    closes = [float(c.get("close") or 0) for c in candles]

    # --- Wilder RSI(14) ---
    period = 14
    rsis: list[float | None] = [None] * len(closes)
    if len(closes) > period:
        gains_sum = 0.0
        losses_sum = 0.0
        for i in range(1, period + 1):
            delta = closes[i] - closes[i - 1]
            gains_sum  += max(delta, 0.0)
            losses_sum += max(-delta, 0.0)
        avg_gain = gains_sum  / period
        avg_loss = losses_sum / period
        rs = (avg_gain / avg_loss) if avg_loss > 0 else float("inf")
        rsis[period] = 100.0 if avg_loss == 0 else 100 - (100 / (1 + rs))
        for i in range(period + 1, len(closes)):
            delta = closes[i] - closes[i - 1]
            gain  = max(delta, 0.0)
            loss  = max(-delta, 0.0)
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
            if avg_loss == 0:
                rsis[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                rsis[i] = 100 - (100 / (1 + rs))

    # --- Bollinger Bands(20, 2) ---
    bb_period = 20
    bb_k      = 2.0
    bb_mid:   list[float | None] = [None] * len(closes)
    bb_upper: list[float | None] = [None] * len(closes)
    bb_lower: list[float | None] = [None] * len(closes)
    for i in range(bb_period - 1, len(closes)):
        window = closes[i - bb_period + 1: i + 1]
        mean   = sum(window) / bb_period
        var    = sum((x - mean) ** 2 for x in window) / bb_period
        std    = var ** 0.5
        bb_mid[i]   = mean
        bb_upper[i] = mean + bb_k * std
        bb_lower[i] = mean - bb_k * std

    enriched: list[dict] = []
    prev_close = None
    for i, c in enumerate(candles):
        ts = c["timestamp"]
        if ts > 1e12:
            ts = ts // 1000
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        # Use HH:MM as the human-readable time label
        time_label = dt.strftime("%H:%M") if c is candles[0] or True else dt.isoformat()
        cur_close = float(c.get("close") or 0)
        is_up = (cur_close >= prev_close) if prev_close is not None else (cur_close >= float(c.get("open") or 0))
        prev_close = cur_close

        enriched.append({
            **c,
            "iso_utc":  dt.isoformat(),
            "time":     time_label,
            "isUp":     bool(is_up),
            "rsi":      round(rsis[i], 2)      if rsis[i]      is not None else None,
            "bbMid":    round(bb_mid[i], 6)    if bb_mid[i]    is not None else None,
            "bbUpper":  round(bb_upper[i], 6)  if bb_upper[i]  is not None else None,
            "bbLower":  round(bb_lower[i], 6)  if bb_lower[i]  is not None else None,
        })
    return enriched


@router.get("/ohlcv/{pair_dash}")
async def ohlcv(pair_dash: str, interval: str = "15", limit: int = 80,
                 _=Depends(require_auth)):
    from exchange.bybit_client import bybit
    pair = pair_dash.replace("-", "/")
    try:
        candles = await bybit.get_ohlcv(pair, interval=interval, limit=limit)
        candles = _compute_indicators(candles)
        return {"pair": pair, "interval": interval, "candles": candles}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ohlcv_error: {e}")


# ── Learning summary ──────────────────────────────────────────────────────

@router.get("/learning/summary")
async def learning_summary(_=Depends(require_auth)):
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
        "news_weights":     weights_out,
        "opus_history":     memory_out,
        "source_breakdown": sources,
    }


# ── Capital injection workflow (NEW) ──────────────────────────────────────

@router.get("/capital/pending-injections")
async def pending_injections(_=Depends(require_auth)):
    """List rekomendasi capital injection yang masih pending approval."""
    from engine.auto_evolution import auto_evolution
    items = await auto_evolution.get_pending_injections()
    return {"pending": items}


@router.post("/capital/approve/{injection_id}")
async def approve_injection(injection_id: str, _=Depends(require_auth)):
    """Approve rekomendasi injection — update capital tracking."""
    from engine.auto_evolution import auto_evolution
    result = await auto_evolution.approve_injection(
        injection_id, approved_by="dashboard"
    )
    if not result:
        raise HTTPException(status_code=404, detail="injection_not_found")
    return {"ok": True, "applied": result}


@router.post("/capital/reject/{injection_id}")
async def reject_injection(injection_id: str, _=Depends(require_auth)):
    """Reject rekomendasi injection."""
    from engine.auto_evolution import auto_evolution
    ok = await auto_evolution.reject_injection(injection_id)
    if not ok:
        raise HTTPException(status_code=404, detail="injection_not_found")
    return {"ok": True}


@router.post("/capital/inject")
async def manual_capital_inject(request: Request, _=Depends(require_auth)):
    """
    Manual capital injection oleh user (bypass Opus recommendation).
    User input langsung dari dashboard tanpa harus tunggu rekomendasi.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_body")

    try:
        amount = float(body.get("amount") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="invalid_amount")

    if amount <= 0 or amount > 10000:
        raise HTTPException(status_code=400, detail="amount_out_of_bounds")

    note = str(body.get("note", ""))[:200]

    try:
        current = await db.get_current_capital()
        new_capital = current + amount

        from database.models import PortfolioSnapshot
        infra = await db.get_infra_balance()
        active_pairs = await db.get_active_pairs()
        snap = PortfolioSnapshot(
            snapshot_date    = date.today(),
            total_capital    = new_capital,
            trading_capital  = max(0, new_capital - infra),
            infra_reserve    = infra,
            emergency_buffer = round(new_capital * 0.05, 4),
            current_tier     = settings.get_tier(new_capital),
            active_pairs     = active_pairs,
            daily_pnl        = 0,
            drawdown_pct     = 0,
        )
        await db.save_portfolio_snapshot(snap)

        await db.log_event(
            event_type = "capital_injection_manual",
            severity   = "info",
            message    = f"Manual capital injection ${amount:.2f}: {note}",
            data = {
                "amount":           amount,
                "previous_capital": current,
                "new_capital":      new_capital,
                "note":             note,
                "source":           "dashboard_manual",
            },
        )

        # Notif Telegram
        try:
            from notifications.telegram_bot import telegram
            await telegram.send(
                f"MANUAL CAPITAL INJECTION\n\n"
                f"Amount: ${amount:.2f}\n"
                f"Previous: ${current:.2f}\n"
                f"New total: ${new_capital:.2f}\n"
                f"Note: {note or '(no note)'}"
            )
        except Exception:
            pass

        return {
            "ok": True,
            "previous_capital": current,
            "new_capital":      new_capital,
            "amount":           amount,
        }
    except Exception as e:
        log.error("Manual injection failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="injection_failed")


@router.get("/auto-evolution/recent-actions")
async def recent_auto_evolution(days: int = 30, _=Depends(require_auth)):
    """Log auto-activation/deactivation/injection terakhir untuk audit."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    res = (
        db._get()
        .table("bot_events")
        .select("*")
        .in_("event_type", [
            "auto_pair_activated",
            "auto_pair_deactivated",
            "capital_injection_recommended",
            "capital_injection_approved",
            "capital_injection_rejected",
            "capital_injection_manual",
        ])
        .gte("created_at", since)
        .order("created_at", desc=True)
        .limit(50)
        .execute()
    )
    return {"days": days, "actions": res.data or []}