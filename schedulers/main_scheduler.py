"""
schedulers/main_scheduler.py
APScheduler — semua job terjadwal dalam satu tempat.
Timezone: Asia/Jakarta (WIB).

PATCHED 2026-05-02:
- run_job_now sekarang await coroutine dengan benar
- Tambah job harian aggregator news weights (bukan hanya weekly)
"""

from __future__ import annotations
import asyncio
import logging
import inspect
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from zoneinfo import ZoneInfo

from config.settings import settings

log = logging.getLogger("scheduler")
WIB = ZoneInfo("Asia/Jakarta")


class BotScheduler:

    def __init__(self):
        self._scheduler = AsyncIOScheduler(timezone=WIB)
        self._register_all_jobs()

    def _register_all_jobs(self):
        s = self._scheduler

        # ── Setiap 6 jam ──────────────────────────────────────────────
        s.add_job(
            self._wrap(self._seven_day_check),
            IntervalTrigger(hours=6),
            id="seven_day_check",
            name="7-day live monitor (every 6h)",
            max_instances=1,
        )

        # ── Setiap jam ────────────────────────────────────────────────
        s.add_job(
            self._wrap(self._update_news_outcomes),
            IntervalTrigger(hours=1),
            id="news_outcomes",
            name="Update news outcomes",
            max_instances=1,
        )
        s.add_job(
            self._wrap(self._monitor_circuit_breaker),
            IntervalTrigger(minutes=15),
            id="circuit_breaker_check",
            name="Circuit breaker monitor",
            max_instances=1,
        )
        # Reconciliation — Phase-2 designed worker. Writes l1:reconciliation_status
        # which the orchestrator (and L0 supervisor cycle log) consume.
        # Hourly cadence: well under the 25h stale threshold; sub-ms no-op in
        # paper mode. First run at boot so observe-only review starts with a
        # fresh recon status rather than the UNKNOWN default.
        s.add_job(
            self._wrap(self._reconcile),
            IntervalTrigger(hours=1),
            id="reconciliation",
            name="Reconciliation cycle (hourly)",
            max_instances=1,
            next_run_time=datetime.now(WIB),
        )

        # ── Harian ────────────────────────────────────────────────────
        s.add_job(
            self._wrap(self._portfolio_snapshot),
            CronTrigger(hour=0, minute=5, timezone=WIB),
            id="portfolio_snapshot",
            name="Portfolio snapshot 00:05 WIB",
            max_instances=1,
        )
        s.add_job(
            self._wrap(self._update_lrhr),
            CronTrigger(hour=1, minute=0, timezone=WIB),
            id="lrhr_update",
            name="LRHR scores update 01:00 WIB",
            max_instances=1,
        )
        # NEW: harian aggregate news weights (sebelumnya tidak pernah)
        s.add_job(
            self._wrap(self._aggregate_news_weights),
            CronTrigger(hour=2, minute=0, timezone=WIB),
            id="news_weights_aggregate",
            name="News weights aggregator 02:00 WIB",
            max_instances=1,
        )
        s.add_job(
            self._wrap(self._supabase_ping),
            CronTrigger(hour=6, minute=0, timezone=WIB),
            id="supabase_ping",
            name="Supabase keep-alive 06:00 WIB",
            max_instances=1,
        )
        s.add_job(
            self._wrap(self._payment_reminder),
            CronTrigger(hour=10, minute=0, timezone=WIB),
            id="payment_reminder",
            name="Payment reminder 10:00 WIB",
            max_instances=1,
        )
        s.add_job(
            self._wrap(self._daily_summary),
            CronTrigger(hour=20, minute=0, timezone=WIB),
            id="daily_summary",
            name="Daily summary 20:00 WIB",
            max_instances=1,
        )

        # ── Mingguan ──────────────────────────────────────────────────
        s.add_job(
            self._wrap(self._weekly_backtest),
            CronTrigger(day_of_week="mon", hour=6, minute=0, timezone=WIB),
            id="weekly_backtest",
            name="Weekly backtest Senin 06:00 WIB",
            max_instances=1,
        )
        s.add_job(
            self._wrap(self._opus_evaluation),
            CronTrigger(day_of_week="mon", hour=8, minute=0, timezone=WIB),
            id="opus_evaluation",
            name="Opus evaluation Senin 08:00 WIB",
            max_instances=1,
        )
        s.add_job(
            self._wrap(self._crash_test),
            CronTrigger(day_of_week="sun", hour=22, minute=0, timezone=WIB),
            id="crash_test",
            name="Crash test Minggu 22:00 WIB",
            max_instances=1,
        )
        s.add_job(
            self._wrap(self._cleanup_news),
            CronTrigger(day_of_week="sun", hour=1, minute=0, timezone=WIB),
            id="news_cleanup",
            name="News cleanup Minggu 01:00 WIB",
            max_instances=1,
        )

        # NEW: heartbeat tiap menit untuk health check
        s.add_job(
            self._wrap(self._heartbeat),
            IntervalTrigger(minutes=1),
            id="heartbeat",
            name="Scheduler heartbeat",
            max_instances=1,
        )

        log.info("Scheduler: %d jobs registered", len(s.get_jobs()))

    async def start(self):
        self._scheduler.start()
        log.info("Scheduler started (timezone: %s)", settings.BOT_TIMEZONE)
        try:
            while True:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            self._scheduler.shutdown(wait=False)
            log.info("Scheduler stopped")

    @staticmethod
    def _wrap(coro_func):
        """Wrap coroutine dengan error handling."""
        async def _job():
            name = coro_func.__name__.lstrip("_")
            try:
                log.debug("Job starting: %s", name)
                await coro_func()
                log.debug("Job done: %s", name)
            except Exception as e:
                log.error("Job error [%s]: %s", name, e, exc_info=True)
                try:
                    from database.client import db
                    await db.log_event(
                        "scheduler_job_error",
                        f"Job {name} failed: {e}",
                        severity="warning",
                    )
                except Exception:
                    pass
        return _job

    # ── Jobs ──────────────────────────────────────────────────────────

    async def _heartbeat(self):
        from utils.redis_client import redis
        from datetime import datetime, timezone
        await redis.setex(
            "last_scheduler_tick", 180,
            datetime.now(timezone.utc).isoformat()
        )

    async def _seven_day_check(self):
        from monitoring.seven_day_tracker import seven_day_tracker
        await seven_day_tracker.run_check()

    async def _portfolio_snapshot(self):
        from schedulers.daily_tasks import take_portfolio_snapshot
        await take_portfolio_snapshot()

    async def _payment_reminder(self):
        from schedulers.daily_tasks import run_payment_reminder
        await run_payment_reminder()

    async def _update_news_outcomes(self):
        from schedulers.daily_tasks import update_news_outcomes
        await update_news_outcomes()

    async def _aggregate_news_weights(self):
        from news.weights_aggregator import weights_aggregator
        result = await weights_aggregator.run(days=14)
        log.info("News weights aggregation: %s", result)

    async def _update_lrhr(self):
        from schedulers.daily_tasks import update_lrhr_scores
        await update_lrhr_scores()

    async def _monitor_circuit_breaker(self):
        from schedulers.daily_tasks import monitor_circuit_breaker
        await monitor_circuit_breaker()

    async def _reconcile(self):
        from governance import reconciliation
        status = await reconciliation.reconcile()
        log.info("Reconciliation cycle complete: status=%s", status.value)

    async def _supabase_ping(self):
        from schedulers.daily_tasks import check_supabase_activity
        await check_supabase_activity()

    async def _daily_summary(self):
        from schedulers.daily_tasks import send_daily_summary
        await send_daily_summary()

    async def _opus_evaluation(self):
        from schedulers.weekly_tasks import run_opus_evaluation
        await run_opus_evaluation()

    async def _weekly_backtest(self):
        from schedulers.weekly_tasks import run_weekly_backtest
        await run_weekly_backtest()

    async def _crash_test(self):
        from schedulers.weekly_tasks import run_weekly_crash_test
        await run_weekly_crash_test()

    async def _cleanup_news(self):
        from schedulers.weekly_tasks import cleanup_old_news
        await cleanup_old_news()

    # ── Debug ─────────────────────────────────────────────────────────

    def list_jobs(self) -> list[dict]:
        return [
            {
                "id":       job.id,
                "name":     job.name,
                "next_run": str(job.next_run_time),
            }
            for job in self._scheduler.get_jobs()
        ]

    async def run_job_now(self, job_id: str) -> bool:
        """Paksa jalankan satu job sekarang."""
        job = self._scheduler.get_job(job_id)
        if not job:
            return False
        result = job.func()
        # FIX: await jika coroutine
        if inspect.iscoroutine(result):
            await result
        return True
