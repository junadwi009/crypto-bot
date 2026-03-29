"""
crypto-bot — main.py
Entry point. Menjalankan semua service secara paralel:
  - Telegram bot (notifikasi + auth + error alerts)
  - Trading loop (signal → order)
  - News pipeline (RSS + CryptoPanic)
  - Credit monitor
  - Scheduler (daily/weekly tasks)
  - Health check HTTP server (FastAPI + Render logs)
"""

import asyncio
import logging
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

# ── Setup logging ──────────────────────────────────────────────────────────
setup_logging()
log = logging.getLogger("main")

# ── Graceful shutdown flag ─────────────────────────────────────────────────
_stopping = False


async def trading_loop():
    """
    Loop utama trading — berjalan terus selama bot aktif.
    Setiap siklus:
      1. Cek circuit breaker
      2. Generate sinyal untuk setiap pair aktif (rule → Haiku → Sonnet)
      3. Monitor posisi terbuka (SL/TP)
    """
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
    """Loop news pipeline — ambil berita setiap 15 menit."""
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
    """Cek saldo token Anthropic setiap jam."""
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
    """Hentikan bot dengan aman — tunggu posisi terbuka, lalu stop semua task."""
    global _stopping
    _stopping = True

    log.info("Graceful shutdown initiated")
    await redis.set("bot_stopping", "1")

    try:
        await telegram.send(
            "Bot sedang berhenti dengan aman...\n"
            "Tidak ada order baru yang akan dibuka.\n"
            "Posisi terbuka tetap berjalan sampai SL/TP."
        )
    except Exception:
        pass

    await asyncio.sleep(5)

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

# CORS — izinkan frontend Render akses API
# Ganti URL setelah deploy frontend ke Render
ALLOWED_ORIGINS = [
    "http://localhost:5173",           # dev lokal
    "http://localhost:4173",           # vite preview
    f"https://{settings.FRONTEND_URL}" if hasattr(settings, "FRONTEND_URL") and settings.FRONTEND_URL else "",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins     = [o for o in ALLOWED_ORIGINS if o],
    allow_credentials = True,
    allow_methods     = ["GET"],
    allow_headers     = ["*"],
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
    """Render.com health check endpoint — dipanggil setiap 30 detik."""
    paused   = bool(await redis.get("bot_paused"))
    stopping = bool(await redis.get("bot_stopping"))
    return {
        "status":      "stopping" if stopping else "paused" if paused else "ok",
        "paper_trade": settings.PAPER_TRADE,
        "tier":        await db.get_current_tier(),
    }

@app.get("/status")
async def status():
    capital = await db.get_current_capital()
    return {
        "capital_usd":  capital,
        "paper_trade":  settings.PAPER_TRADE,
        "active_pairs": await db.get_active_pairs(),
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

    # 1. Validasi semua env vars wajib
    validate_secrets()

    # 2. Koneksi database & Redis
    await db.ping()
    await redis.ping()
    log.info("DB and Redis: connected")

    # Clear flag lama dari session sebelumnya
    await redis.delete("bot_stopping")
    await redis.delete("bot_paused")
    log.info("Startup flags cleared")

    # 3. FIX: Drop webhook & pending updates saat startup
    # Mencegah Conflict error saat Render restart instance baru
    # sebelum instance lama benar-benar mati
    try:
        await telegram.app.bot.delete_webhook(drop_pending_updates=True)
        log.info("Telegram webhook cleared — conflict prevention OK")
    except Exception as e:
        log.warning("Could not clear Telegram webhook (non-fatal): %s", e)

    # Tambahan jeda kecil setelah delete webhook
    # agar Telegram server sempat menutup koneksi polling lama
    await asyncio.sleep(2)

    # 4. Koneksi Bybit (non-fatal saat lokal — Bybit bisa diblokir ISP)
    try:
        await bybit.ping()
        log.info("Bybit: connected")
    except Exception as e:
        if settings.PAPER_TRADE:
            log.warning("Bybit ping failed (paper trade mode — continuing anyway): %s", e)
        else:
            log.critical("Bybit ping failed in LIVE mode — stopping bot")
            raise

    # 5. Notif startup ke Telegram
    mode = "PAPER TRADE" if settings.PAPER_TRADE else "LIVE TRADING"
    await telegram.send(
        f"Bot started\n"
        f"Mode:     {mode}\n"
        f"Capital:  ${settings.INITIAL_CAPITAL}\n"
        f"Timezone: WIB (Asia/Jakarta)"
    )

    # 6. Inisialisasi scheduler
    scheduler = BotScheduler()

    # 7. Start semua async tasks secara paralel
    tasks = [
        asyncio.create_task(trading_loop(),       name="trading"),
        asyncio.create_task(news_loop(),           name="news"),
        asyncio.create_task(credit_monitor_loop(), name="credit"),
        asyncio.create_task(telegram.run(),        name="telegram"),
        asyncio.create_task(scheduler.start(),     name="scheduler"),
    ]

    # 8. Handle SIGTERM dari Render (Linux only — Windows tidak support add_signal_handler)
    loop = asyncio.get_event_loop()

    def handle_signal():
        log.info("Signal received — initiating graceful shutdown")
        asyncio.create_task(graceful_shutdown(tasks))

    import platform
    if platform.system() != "Windows":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, handle_signal)
    else:
        # Windows: pakai signal.signal biasa (sync, tapi cukup untuk Ctrl+C lokal)
        import signal as _signal
        _signal.signal(_signal.SIGINT,  lambda s, f: asyncio.create_task(graceful_shutdown(tasks)))
        _signal.signal(_signal.SIGTERM, lambda s, f: asyncio.create_task(graceful_shutdown(tasks)))

    # 9. Health check server (FastAPI + uvicorn)
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    tasks.append(asyncio.create_task(server.serve(), name="health"))

    log.info("All services running — bot is active")

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