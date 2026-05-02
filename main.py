"""
crypto-bot — main.py
Entry point. Menjalankan semua service secara paralel.

PATCHED 2026-05-02 (revisi 2 — Render+Vercel deployment):
- CORS origins env-based: ALLOWED_ORIGINS env var bisa berisi
  comma-separated list. Hindari regex `.*\\.vercel\\.app` yang
  terlalu loose (preview deployment orang lain bisa akses).
- Health endpoint baca _stopping in-memory
- Trust proxy headers (Render dan Vercel pakai reverse proxy)
"""

import asyncio
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from config.settings import settings
from monitoring.dashboard_api import router as dashboard_router
from utils.logger import setup_logging
from utils.redis_client import redis
from database.client import db
from security.secret_guard import validate_secrets
from exchange.bybit_client import bybit
from engine.signal_generator import signal_generator
from engine.circuit_breaker import circuit_breaker
from brains.credit_monitor import credit_monitor
from news.fetcher import news_fetcher
from notifications.telegram_bot import telegram
from schedulers.main_scheduler import BotScheduler

setup_logging()
log = logging.getLogger("main")

_stopping = False


def is_stopping() -> bool:
    return _stopping


async def trading_loop():
    log.info("Trading loop started | paper_trade=%s", settings.PAPER_TRADE)
    while not _stopping:
        try:
            if await redis.get("bot_paused"):
                await asyncio.sleep(10)
                continue

            if await circuit_breaker.is_tripped():
                log.warning("Circuit breaker active — skipping cycle")
                await asyncio.sleep(30)
                continue

            try:
                history = await db.get_portfolio_history(days=2)
                if history:
                    capital_start = float(history[0].get("total_capital", 0))
                    capital_now   = await db.get_current_capital()
                    if capital_start > 0:
                        await circuit_breaker.check(capital_now, capital_start)
            except Exception as e:
                log.debug("CB inline check skipped: %s", e)

            active_pairs = await db.get_active_pairs()
            for pair in active_pairs:
                await signal_generator.process(pair)

            await signal_generator.monitor()
            await asyncio.sleep(30)

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("Trading loop error: %s", e, exc_info=True)
            await asyncio.sleep(10)


async def news_loop():
    log.info("News pipeline started")
    while not _stopping:
        try:
            await news_fetcher.run()
            await asyncio.sleep(15 * 60)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("News loop error: %s", e, exc_info=True)
            await asyncio.sleep(60)


async def credit_monitor_loop():
    log.info("Credit monitor started")
    while not _stopping:
        try:
            await credit_monitor.check()
            await asyncio.sleep(60 * 60)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("Credit monitor error: %s", e, exc_info=True)
            await asyncio.sleep(300)


async def graceful_shutdown(tasks: list):
    global _stopping
    _stopping = True
    log.info("Graceful shutdown initiated")

    try:
        await telegram.send(
            "Bot sedang berhenti dengan aman...\n"
            "Tidak ada order baru yang akan dibuka.\n"
            "Posisi terbuka tetap berjalan sampai SL/TP."
        )
    except Exception:
        pass

    await telegram.stop()
    log.info("Telegram polling stopped")
    await asyncio.sleep(3)

    for task in tasks:
        if not task.done():
            task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("All tasks stopped — bot shutdown complete")


# ── FastAPI app ────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Health check server started")
    yield
    log.info("Health check server stopped")


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(dashboard_router)


# ── CORS configuration ─────────────────────────────────────────────────────
# Untuk deployment Vercel + Render:
#   ALLOWED_ORIGINS env var berisi comma-separated list seperti:
#     https://my-dashboard.vercel.app,https://my-dashboard-arjuna.vercel.app
#   Plus localhost untuk dev otomatis selalu di-allow.

def _build_cors_origins() -> list[str]:
    origins = [
        "http://localhost:5173",
        "http://localhost:4173",
        "http://localhost:3000",
    ]
    env_origins = os.getenv("ALLOWED_ORIGINS", "").strip()
    if env_origins:
        for o in env_origins.split(","):
            o = o.strip()
            if o and o not in origins:
                origins.append(o)
    return origins


_origins = _build_cors_origins()
log.info("CORS allowed origins: %s", _origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = _origins,
    allow_credentials = True,
    allow_methods     = ["GET", "POST", "OPTIONS"],
    allow_headers     = ["Content-Type", "Authorization"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Robots-Tag"]           = "noindex, nofollow, noarchive"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"]        = "DENY"
    return response


@app.get("/health")
async def health():
    paused = bool(await redis.get("bot_paused"))
    return {
        "status":      "stopping" if _stopping else "paused" if paused else "ok",
        "paper_trade": settings.PAPER_TRADE,
        "tier":        await db.get_current_tier(),
    }


# ── Entry point ────────────────────────────────────────────────────────────
async def main():
    global _stopping

    log.info("=" * 50)
    log.info("Crypto Bot starting up")
    log.info("  Paper trade : %s", settings.PAPER_TRADE)
    log.info("  Timezone    : %s", settings.BOT_TIMEZONE)
    log.info("  Capital     : $%s", settings.INITIAL_CAPITAL)
    log.info("=" * 50)

    validate_secrets()

    await db.ping()
    await redis.ping()
    log.info("DB and Redis: connected")

    try:
        await bybit.ping()
        log.info("Bybit: connected")
    except Exception as e:
        if settings.PAPER_TRADE:
            log.warning("Bybit ping failed (paper trade — continuing): %s", e)
        else:
            log.critical("Bybit ping failed in LIVE mode — stopping bot")
            raise

    mode = "PAPER TRADE" if settings.PAPER_TRADE else "LIVE TRADING"
    await telegram.send(
        f"Bot started\n"
        f"Mode:     {mode}\n"
        f"Capital:  ${settings.INITIAL_CAPITAL}\n"
        f"Timezone: WIB (Asia/Jakarta)"
    )

    scheduler = BotScheduler()

    tasks = [
        asyncio.create_task(trading_loop(),        name="trading"),
        asyncio.create_task(news_loop(),           name="news"),
        asyncio.create_task(credit_monitor_loop(), name="credit"),
        asyncio.create_task(telegram.run(),        name="telegram"),
        asyncio.create_task(scheduler.start(),     name="scheduler"),
    ]

    loop = asyncio.get_running_loop()

    def handle_signal():
        log.info("Signal received — initiating graceful shutdown")
        asyncio.create_task(graceful_shutdown(tasks))

    import platform
    if platform.system() != "Windows":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, handle_signal)
    else:
        import signal as _signal
        _signal.signal(_signal.SIGINT,  lambda s, f: asyncio.create_task(graceful_shutdown(tasks)))
        _signal.signal(_signal.SIGTERM, lambda s, f: asyncio.create_task(graceful_shutdown(tasks)))

    # Render menyediakan PORT env var
    port = int(os.getenv("PORT", "8000"))
    config = uvicorn.Config(
        app,
        host       = "0.0.0.0",
        port       = port,
        log_level  = "warning",
        forwarded_allow_ips = "*",  # Trust Render proxy headers
    )
    server = uvicorn.Server(config)
    tasks.append(asyncio.create_task(server.serve(), name="health"))

    await redis.delete("bot_stopping")
    await redis.delete("bot_paused")
    log.info("Startup flags cleared — all services running, bot is active on port %d", port)

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        log.info("Bot shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Keyboard interrupt — bot stopped")
        sys.exit(0)