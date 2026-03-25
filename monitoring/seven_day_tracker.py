"""
monitoring/seven_day_tracker.py
Monitor intensif 7 hari pertama setelah go live.
Kirim laporan kondisi bot setiap 6 jam ke Telegram.
Lebih ketat dari monitoring normal — threshold lebih rendah,
notif lebih sering, dan ada gate check untuk stop jika tidak sehat.
"""

from __future__ import annotations
import logging
from datetime import date, datetime, timedelta, timezone

from config.settings import settings
from database.client import db
from utils.redis_client import redis

log = logging.getLogger("seven_day_tracker")

_TRACKER_START_KEY = "seven_day_tracker_start"
_TRACKER_ACTIVE    = "seven_day_tracker_active"


class SevenDayTracker:

    async def activate(self):
        """
        Aktifkan tracker — dipanggil saat bot pertama kali switch ke live.
        Catat timestamp mulai dan kirim notif pertama.
        """
        now = datetime.now(timezone.utc).isoformat()
        await redis.set(_TRACKER_START_KEY, now)
        await redis.set(_TRACKER_ACTIVE,    "1")

        await db.log_event(
            "seven_day_tracker_start",
            "7-day live monitoring activated",
            severity="info",
        )
        log.info("7-day tracker activated")

        from notifications.telegram_bot import telegram
        await telegram.send(
            "7-Day Live Monitoring Aktif\n\n"
            "Bot sekarang dalam mode live trading.\n"
            "Laporan kondisi dikirim setiap 6 jam selama 7 hari.\n"
            "Threshold lebih ketat dari monitoring normal."
        )

    async def is_active(self) -> bool:
        """Cek apakah masih dalam periode 7 hari."""
        if not await redis.get(_TRACKER_ACTIVE):
            return False

        start_str = await redis.get(_TRACKER_START_KEY)
        if not start_str:
            return False

        start = datetime.fromisoformat(start_str)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)

        elapsed = (datetime.now(timezone.utc) - start).days
        if elapsed >= 7:
            await self._deactivate()
            return False
        return True

    async def run_check(self):
        """
        Jalankan health check 7-day.
        Dipanggil dari scheduler setiap 6 jam.
        """
        if not await self.is_active():
            return

        try:
            metrics = await self._collect_metrics()
            issues  = self._detect_issues(metrics)

            await self._send_report(metrics, issues)

            # Auto-pause jika kondisi sangat buruk
            if self._should_auto_pause(issues):
                await redis.set("bot_paused", "1")
                await db.log_event(
                    "auto_pause_seven_day",
                    "Bot auto-paused by 7-day tracker due to critical issues",
                    severity="critical",
                    data={"issues": issues, "metrics": metrics},
                )
                from notifications.telegram_bot import telegram
                await telegram.send(
                    "BOT AUTO-PAUSED\n\n"
                    "7-day tracker mendeteksi masalah kritis:\n"
                    + "\n".join(f"  • {i}" for i in issues) +
                    "\n\nKetik /resume setelah review."
                )

        except Exception as e:
            log.error("7-day tracker error: %s", e, exc_info=True)

    async def _collect_metrics(self) -> dict:
        """Kumpulkan semua metrik relevan."""
        capital      = await db.get_current_capital()
        daily_pnl    = await db.get_total_pnl(days=1)
        win_rate_7d  = await db.get_win_rate(days=7, is_paper=False)
        max_dd_7d    = await db.get_max_drawdown(days=7)
        open_trades  = await db.get_open_trades(is_paper=False)
        events_crit  = await db.get_recent_events(hours=6, severity="critical")
        claude_mode  = await redis.get("claude_mode") or "normal"

        start_str   = await redis.get(_TRACKER_START_KEY)
        start       = datetime.fromisoformat(start_str)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        day_number  = (datetime.now(timezone.utc) - start).days + 1

        from brains.credit_monitor import credit_monitor
        credit_bal  = await credit_monitor.get_balance() or 0
        burn_rate   = await credit_monitor.get_burn_rate()

        return {
            "day_number":   day_number,
            "capital":      round(capital, 2),
            "daily_pnl":    round(daily_pnl, 2),
            "win_rate_7d":  round(win_rate_7d, 4),
            "max_dd_7d":    round(max_dd_7d, 4),
            "open_trades":  len(open_trades),
            "critical_events": len(events_crit),
            "claude_mode":  claude_mode,
            "credit_bal":   round(credit_bal, 2),
            "burn_rate":    round(burn_rate, 4),
        }

    def _detect_issues(self, m: dict) -> list[str]:
        """Deteksi masalah berdasarkan threshold ketat 7-day."""
        issues = []

        # Drawdown lebih ketat dari circuit breaker normal
        if m["max_dd_7d"] >= 0.10:
            issues.append(
                f"Drawdown tinggi: {m['max_dd_7d']*100:.1f}% "
                f"(threshold 10%)"
            )

        # Win rate terlalu rendah
        if m["win_rate_7d"] < 0.45 and m["win_rate_7d"] > 0:
            issues.append(
                f"Win rate rendah: {m['win_rate_7d']*100:.1f}% "
                f"(minimum 45%)"
            )

        # Modal turun signifikan
        initial = settings.INITIAL_CAPITAL
        if m["capital"] < initial * 0.85:
            loss_pct = (initial - m["capital"]) / initial * 100
            issues.append(
                f"Modal turun {loss_pct:.1f}% dari awal "
                f"(${m['capital']:.2f} dari ${initial:.2f})"
            )

        # Event kritis terlalu banyak
        if m["critical_events"] >= 3:
            issues.append(
                f"{m['critical_events']} critical events dalam 6 jam terakhir"
            )

        # Claude mode degraded
        if m["claude_mode"] in ("haiku_only", "off"):
            issues.append(
                f"Claude mode: {m['claude_mode']} "
                f"(kredit: ${m['credit_bal']:.2f})"
            )

        return issues

    def _should_auto_pause(self, issues: list[str]) -> bool:
        """
        Auto-pause jika ada 2+ masalah serius,
        atau ada 1 masalah yang menyebut drawdown/modal.
        """
        if len(issues) >= 2:
            return True
        if issues and any("drawdown" in i.lower() or "modal" in i.lower()
                          for i in issues):
            return True
        return False

    async def _send_report(self, m: dict, issues: list[str]):
        """Kirim laporan 6-jam ke Telegram."""
        from notifications.telegram_bot import telegram

        status = "OK" if not issues else f"PERLU PERHATIAN ({len(issues)} masalah)"
        pnl_sign = "+" if m["daily_pnl"] >= 0 else ""

        msg = (
            f"7-Day Monitor — Hari {m['day_number']}/7\n\n"
            f"Status:      {status}\n"
            f"Capital:     ${m['capital']:.2f}\n"
            f"Daily PnL:   {pnl_sign}${m['daily_pnl']:.2f}\n"
            f"Win rate 7d: {m['win_rate_7d']*100:.1f}%\n"
            f"Max DD 7d:   {m['max_dd_7d']*100:.1f}%\n"
            f"Open trades: {m['open_trades']}\n"
            f"Claude mode: {m['claude_mode']}\n"
            f"Kredit:      ${m['credit_bal']:.2f}\n"
        )

        if issues:
            msg += "\nMasalah terdeteksi:\n"
            msg += "\n".join(f"  • {i}" for i in issues)

        await telegram.send(msg)

    async def _deactivate(self):
        """Nonaktifkan tracker setelah 7 hari."""
        await redis.delete(_TRACKER_ACTIVE)
        await db.log_event(
            "seven_day_tracker_complete",
            "7-day live monitoring period complete",
            severity="info",
        )
        from notifications.telegram_bot import telegram
        await telegram.send(
            "7-Day Live Monitoring Selesai\n\n"
            "Bot sudah berjalan 7 hari dalam mode live.\n"
            "Monitoring beralih ke mode normal."
        )
        log.info("7-day tracker deactivated — monitoring period complete")


seven_day_tracker = SevenDayTracker()
