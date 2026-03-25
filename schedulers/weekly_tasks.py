"""
schedulers/weekly_tasks.py
Task mingguan — evaluasi Opus dan backtest ulang.
Dijalankan setiap Senin jam 08:00 WIB.
"""

from __future__ import annotations
import logging

from config.settings import settings
from database.client import db
from utils.redis_client import redis

log = logging.getLogger("weekly_tasks")


async def run_opus_evaluation():
    """
    Jalankan evaluasi mingguan Opus.
    Dijalankan Senin 08:00 WIB.
    Opus akan:
      - Analisis performa 7 hari terakhir
      - Update parameter strategi otomatis
      - Update bobot berita
      - Kirim action_required ke Telegram
    """
    try:
        log.info("Starting Opus weekly evaluation")

        # Cek apakah Claude tersedia
        from brains.credit_monitor import credit_monitor
        if not await credit_monitor.is_model_allowed("opus"):
            log.warning("Opus not available — skipping weekly evaluation")
            from notifications.telegram_bot import telegram
            await telegram.send(
                "Weekly evaluation dilewati — kredit Claude tidak cukup.\n"
                "Topup Anthropic untuk mengaktifkan kembali."
            )
            return

        # Cek apakah bulan ini sudah cukup Opus calls
        capital     = await db.get_current_capital()
        tier        = settings.get_tier(capital)
        limits      = settings.CLAUDE_LIMITS.get(tier, settings.CLAUDE_LIMITS["seed"])
        max_per_wk  = limits["opus_per_week"]

        opus_calls  = await db.get_claude_calls_today("opus")
        if opus_calls >= max_per_wk * 7:  # Approximasi batas mingguan
            log.warning("Opus weekly call limit reached for tier %s", tier)

        # Jalankan evaluasi
        from brains.opus_brain import opus_brain
        result = await opus_brain.weekly_evaluation()

        if not result:
            log.error("Opus evaluation returned empty result")
            return

        # Kirim laporan ke Telegram
        summary = result.get("summary", {})
        actions = result.get("action_required", [])

        from notifications.telegram_bot import telegram
        await telegram.send_opus_report(summary, actions)

        # Proses rekomendasi pair
        await _process_pair_recommendations(
            result.get("pair_recommendations", [])
        )

        log.info(
            "Opus evaluation done: %d actions, $%.4f cost",
            len(actions),
            result.get("token_cost", 0),
        )

    except Exception as e:
        log.error("Opus evaluation error: %s", e, exc_info=True)
        await db.log_event(
            "opus_evaluation_error",
            f"Weekly evaluation failed: {e}",
            severity="warning",
        )


async def run_weekly_backtest():
    """
    Jalankan ulang backtest untuk semua pair aktif.
    Dijalankan Senin 06:00 WIB (sebelum Opus eval).
    Hasil backtest fresh akan dipakai Opus dalam evaluasinya.
    """
    try:
        log.info("Starting weekly backtest run")
        from backtesting.runner import backtest_runner

        active_pairs = await db.get_active_pairs()
        for pair in active_pairs:
            result = await backtest_runner.run(pair, months=3)
            if result:
                log.info(
                    "Backtest %s: win=%.1f%% sharpe=%.2f",
                    pair, result.win_rate * 100, result.sharpe_ratio
                )

        log.info("Weekly backtest done for %d pairs", len(active_pairs))

    except Exception as e:
        log.error("Weekly backtest error: %s", e, exc_info=True)


async def run_weekly_crash_test():
    """
    Jalankan crash scenario test.
    Dijalankan Minggu 22:00 WIB (sebelum trading minggu baru).
    Kirim alert jika bot tidak survive skenario tertentu.
    """
    try:
        log.info("Starting weekly crash test")
        from backtesting.crash_injector import crash_injector

        summary = await crash_injector.run_all_scenarios()
        passed  = summary.get("passed", 0)
        total   = summary.get("total", 0)

        from notifications.telegram_bot import telegram

        if summary.get("all_passed"):
            await telegram.send(
                f"Crash test mingguan: {passed}/{total} passed\n"
                f"Bot siap untuk minggu baru."
            )
        else:
            failed = [
                name for name, r in summary.get("scenarios", {}).items()
                if not r.get("passed")
            ]
            await telegram.send(
                f"Crash test: {passed}/{total} passed\n\n"
                f"GAGAL di: {', '.join(failed)}\n\n"
                f"Review parameter sebelum trading dilanjutkan."
            )

    except Exception as e:
        log.error("Crash test error: %s", e, exc_info=True)


async def cleanup_old_news():
    """
    Hapus berita lebih dari 30 hari dari database.
    Dijalankan Minggu 01:00 WIB — jaga DB tetap ringan.
    """
    try:
        from datetime import datetime, timedelta, timezone
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).isoformat()

        db._get().table("news_items").delete().lt(
            "published_at", cutoff
        ).execute()

        log.info("Old news cleanup done (cutoff: %s)", cutoff[:10])

    except Exception as e:
        log.error("News cleanup error: %s", e)


async def _process_pair_recommendations(recommendations: list[dict]):
    """
    Proses rekomendasi pair dari Opus:
    - activate: aktifkan pair jika lulus backtest
    - deactivate: nonaktifkan pair bermasalah
    """
    for rec in recommendations:
        pair   = rec.get("pair", "")
        action = rec.get("action", "")
        reason = rec.get("reason", "")
        score  = float(rec.get("lrhr_score", 0))

        if not pair or action not in ("activate", "deactivate"):
            continue

        try:
            if action == "activate" and score >= 0.55:
                # Cek modal dulu
                config  = await db.get_pair_config(pair)
                capital = await db.get_current_capital()
                if config and capital >= config.min_capital_required:
                    await db.set_pair_active(pair, True)
                    log.info("Opus activated pair: %s (score=%.3f)", pair, score)
                    from notifications.telegram_bot import telegram
                    await telegram.send(
                        f"Pair baru diaktifkan: {pair}\n"
                        f"LRHR score: {score:.3f}\n"
                        f"Alasan: {reason}"
                    )
                else:
                    log.info("Opus wants to activate %s but capital insufficient", pair)

            elif action == "deactivate":
                await db.set_pair_active(pair, False, reason)
                log.info("Opus deactivated pair: %s (%s)", pair, reason)
                from notifications.telegram_bot import telegram
                await telegram.send(
                    f"Pair dinonaktifkan: {pair}\n"
                    f"Alasan: {reason}"
                )

        except Exception as e:
            log.error("Pair recommendation error %s: %s", pair, e)
