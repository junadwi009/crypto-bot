"""
engine/auto_evolution.py
Aturan yang berubah-ubah sendiri (auto-evolving rules) berdasarkan
rekomendasi Opus weekly eval.

Tiga rule utama:

1. AUTO PAIR ACTIVATION
   Pair yang Opus rekomendasikan untuk diaktifkan, plus passing safety check
   (LRHR score, sharpe, win_rate, modal cukup), akan langsung di-activate
   tanpa intervensi manusia. Maksimal 1 aktivasi per minggu.

2. AUTO PAIR DEACTIVATION
   Pair yang underperform (win_rate <35% selama 14 hari ATAU drawdown >10%)
   atau yang Opus rekomendasikan untuk dimatikan akan otomatis di-deactivate.
   Posisi terbuka di-close dulu lewat news_action_executor mechanism.

3. CAPITAL INJECTION (recommend + approval)
   Opus boleh rekomendasi tambah modal kalau analisis menunjukkan modal
   adalah constraint. Rekomendasi disimpan di Redis sebagai "pending",
   dikirim ke user via Telegram dengan tombol Approve/Reject. Hanya jika
   user approve, capital tracking di-update.

Auto-execute (rule 1 & 2) memang invasif, jadi semua aksi:
- Wajib di-log ke bot_events untuk audit
- Wajib kirim Telegram notif ke user
- Punya safeguard berlapis (bounds, throttle, max actions/week)

Capital injection (rule 3) PERLU approval — tidak pernah auto-execute karena
melibatkan dana real.
"""

from __future__ import annotations
import json
import logging
import secrets
import time
from datetime import date, datetime, timedelta, timezone

from config.settings import settings
from database.client import db
from utils.redis_client import redis

log = logging.getLogger("auto_evolution")

# Max aksi per minggu — safeguard agar tidak spam aktivasi
MAX_PAIR_ACTIVATION_PER_WEEK   = 1
MAX_PAIR_DEACTIVATION_PER_WEEK = 2
MAX_PAIRS_ACTIVE               = 5

# Threshold untuk auto-activation
MIN_LRHR_SCORE_FOR_ACTIVATE   = 0.65
MIN_SHARPE_FOR_ACTIVATE       = 1.5
MIN_WIN_RATE_FOR_ACTIVATE     = 0.55

# Threshold untuk auto-deactivation (underperform)
MIN_AGE_DAYS_BEFORE_DEACTIVATE = 14
MAX_WIN_RATE_FOR_DEACTIVATE    = 0.35
MAX_PNL_PCT_FOR_DEACTIVATE     = -0.10  # PnL 14 hari < -10% modal awal

# Capital injection
MAX_INJECTION_RECOMMENDATION_USD = 200.0
INJECTION_TTL_DAYS               = 7

# Redis keys
PENDING_INJECTIONS_KEY = "capital_injections:pending"  # JSON list
WEEKLY_ACTIONS_KEY     = "auto_evolution:week_{week_start}"  # counters per minggu


class AutoEvolution:

    async def apply_opus_recommendations(self, opus_data: dict,
                                          week_start: date) -> dict:
        """
        Dipanggil dari opus_brain.weekly_evaluation() setelah eval selesai.
        Apply rules berdasarkan output Opus.

        Return summary {pair_activated, pair_deactivated, injection_pending}.
        """
        summary = {
            "pair_activated":    [],
            "pair_deactivated":  [],
            "injection_pending": None,
        }

        try:
            recommendations = opus_data.get("pair_recommendations") or []

            # Reset weekly counters jika minggu baru
            week_key = WEEKLY_ACTIONS_KEY.format(week_start=week_start.isoformat())

            # Rule A: Auto-activate
            try:
                activated = await self._auto_activate_pairs(
                    recommendations, week_key
                )
                summary["pair_activated"] = activated
            except Exception as e:
                log.error("Auto-activate error: %s", e, exc_info=True)

            # Rule B: Auto-deactivate (combined: Opus rec + underperform check)
            try:
                deactivated = await self._auto_deactivate_pairs(
                    recommendations, week_key
                )
                summary["pair_deactivated"] = deactivated
            except Exception as e:
                log.error("Auto-deactivate error: %s", e, exc_info=True)

            # Rule C: Capital injection recommendation (no auto-execute)
            try:
                injection = opus_data.get("recommended_capital_injection") or {}
                if injection and isinstance(injection, dict):
                    pending = await self._queue_injection_recommendation(
                        injection, week_start
                    )
                    summary["injection_pending"] = pending
            except Exception as e:
                log.error("Injection recommendation error: %s", e, exc_info=True)

            log.info(
                "Auto-evolution applied: activated=%d deactivated=%d injection=%s",
                len(summary["pair_activated"]),
                len(summary["pair_deactivated"]),
                "yes" if summary["injection_pending"] else "no",
            )

        except Exception as e:
            log.error("Auto-evolution top-level error: %s", e, exc_info=True)

        return summary

    # ── Rule A: Auto-activate ─────────────────────────────────────────────

    async def _auto_activate_pairs(self, recommendations: list,
                                    week_key: str) -> list[dict]:
        applied: list[dict] = []

        # Cek throttle
        already = int(await redis.get(f"{week_key}:activated") or 0)
        if already >= MAX_PAIR_ACTIVATION_PER_WEEK:
            log.info("Activation throttle reached this week (%d)", already)
            return []

        # Cek total active pairs
        active_pairs = await db.get_active_pairs()
        if len(active_pairs) >= MAX_PAIRS_ACTIVE:
            log.info("Max %d active pairs reached — skip activation",
                     MAX_PAIRS_ACTIVE)
            return []

        for rec in recommendations:
            if not isinstance(rec, dict):
                continue
            if rec.get("action") != "activate":
                continue

            pair = rec.get("pair", "")
            if not pair or "/" not in pair:
                continue
            if pair in active_pairs:
                continue

            score = float(rec.get("lrhr_score") or 0)
            if score < MIN_LRHR_SCORE_FOR_ACTIVATE:
                log.info("Skip activate %s: lrhr_score %.2f < %.2f",
                         pair, score, MIN_LRHR_SCORE_FOR_ACTIVATE)
                continue

            # Validasi safety: backtest data
            best_bt = await db.get_best_backtest(pair)
            if not best_bt:
                log.info("Skip activate %s: no backtest data yet", pair)
                continue

            sharpe   = float(best_bt.get("sharpe_ratio") or 0)
            win_rate = float(best_bt.get("win_rate") or 0)
            if sharpe < MIN_SHARPE_FOR_ACTIVATE:
                log.info("Skip activate %s: sharpe %.2f < %.2f",
                         pair, sharpe, MIN_SHARPE_FOR_ACTIVATE)
                continue
            if win_rate < MIN_WIN_RATE_FOR_ACTIVATE:
                log.info("Skip activate %s: win_rate %.2f < %.2f",
                         pair, win_rate, MIN_WIN_RATE_FOR_ACTIVATE)
                continue

            # Cek modal cukup untuk pair ini
            pair_cfg = await db.get_pair_config(pair)
            if pair_cfg:
                capital = await db.get_current_capital()
                min_cap = float(pair_cfg.min_capital_required or 0)
                if capital < min_cap:
                    log.info("Skip activate %s: capital $%.2f < min $%.2f",
                             pair, capital, min_cap)
                    continue

            # Aktivasi!
            await db.set_pair_active(pair, True)
            await db.log_event(
                event_type = "auto_pair_activated",
                severity   = "info",
                message    = f"Auto-activated {pair} (Opus recommendation)",
                data       = {
                    "pair":         pair,
                    "lrhr_score":   score,
                    "sharpe":       sharpe,
                    "win_rate":     win_rate,
                    "reason":       rec.get("reason", "")[:120],
                    "triggered_by": "auto_evolution",
                },
            )

            await redis.incr(f"{week_key}:activated")
            await redis.expire(f"{week_key}:activated", 14 * 24 * 3600)

            applied.append({
                "pair":       pair,
                "lrhr_score": score,
                "sharpe":     sharpe,
                "win_rate":   win_rate,
                "reason":     rec.get("reason", "")[:120],
            })

            try:
                from notifications.telegram_bot import telegram
                await telegram.send(
                    f"AUTO-ACTIVATED PAIR: {pair}\n\n"
                    f"LRHR score: {score:.2f}\n"
                    f"Sharpe: {sharpe:.2f}\n"
                    f"Win rate: {win_rate * 100:.1f}%\n"
                    f"Reason: {rec.get('reason', '')[:120]}\n\n"
                    f"Bot akan mulai trading pair ini di siklus berikutnya."
                )
            except Exception:
                pass

            # Throttle: max 1 per call
            break

        return applied

    # ── Rule B: Auto-deactivate ───────────────────────────────────────────

    async def _auto_deactivate_pairs(self, recommendations: list,
                                      week_key: str) -> list[dict]:
        applied: list[dict] = []

        already = int(await redis.get(f"{week_key}:deactivated") or 0)
        if already >= MAX_PAIR_DEACTIVATION_PER_WEEK:
            return []

        active_pairs = await db.get_active_pairs()

        # Source A: rekomendasi Opus eksplisit
        opus_targets = set()
        for rec in recommendations:
            if isinstance(rec, dict) and rec.get("action") == "deactivate":
                p = rec.get("pair", "")
                if p in active_pairs:
                    opus_targets.add(p)

        # Source B: underperform detection
        underperform = await self._detect_underperformers(active_pairs)

        # Combine
        all_targets: dict[str, str] = {}
        for p in opus_targets:
            all_targets[p] = "opus_recommendation"
        for p, reason in underperform.items():
            if p not in all_targets:
                all_targets[p] = reason

        for pair, reason in all_targets.items():
            if already >= MAX_PAIR_DEACTIVATION_PER_WEEK:
                break

            # Safety: jangan deactivate kalau masih ada open position
            open_trades = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
            has_open = any(t.get("pair") == pair for t in open_trades)
            if has_open:
                log.warning(
                    "Skip deactivate %s: open position exists. "
                    "Will retry next week after positions close.",
                    pair
                )
                continue

            await db.set_pair_active(pair, False, reason=reason)
            await db.log_event(
                event_type = "auto_pair_deactivated",
                severity   = "warning",
                message    = f"Auto-deactivated {pair}: {reason}",
                data       = {
                    "pair":         pair,
                    "reason":       reason,
                    "triggered_by": "auto_evolution",
                },
            )

            await redis.incr(f"{week_key}:deactivated")
            await redis.expire(f"{week_key}:deactivated", 14 * 24 * 3600)
            already += 1

            applied.append({"pair": pair, "reason": reason})

            try:
                from notifications.telegram_bot import telegram
                await telegram.send(
                    f"AUTO-DEACTIVATED PAIR: {pair}\n\n"
                    f"Reason: {reason}\n\n"
                    f"Bot tidak akan trading pair ini sampai diaktifkan kembali manual."
                )
            except Exception:
                pass

        return applied

    async def _detect_underperformers(self, pairs: list[str]) -> dict[str, str]:
        """Cari pair yang underperform berdasarkan trades 14 hari terakhir."""
        result: dict[str, str] = {}

        try:
            trades_14d = await db.get_trades_for_period(days=14)
            by_pair: dict[str, list] = {}
            for t in trades_14d:
                if t.get("status") != "closed":
                    continue
                p = t.get("pair", "")
                if p:
                    by_pair.setdefault(p, []).append(t)

            for pair in pairs:
                trades = by_pair.get(pair, [])
                if len(trades) < 5:
                    # Tidak cukup data untuk men-judge
                    continue

                wins = sum(1 for t in trades if (t.get("pnl_usd") or 0) > 0)
                win_rate = wins / len(trades)

                total_pnl = sum(float(t.get("pnl_usd") or 0) for t in trades)
                capital   = await db.get_current_capital()
                if capital <= 0:
                    continue
                pnl_pct = total_pnl / capital

                if win_rate < MAX_WIN_RATE_FOR_DEACTIVATE:
                    result[pair] = (
                        f"underperform_winrate_{win_rate * 100:.0f}pct_14d"
                    )
                elif pnl_pct < MAX_PNL_PCT_FOR_DEACTIVATE:
                    result[pair] = (
                        f"underperform_pnl_{pnl_pct * 100:.1f}pct_14d"
                    )

        except Exception as e:
            log.error("Underperformer detection error: %s", e)

        return result

    # ── Rule C: Capital Injection (recommend + approval) ──────────────────

    async def _queue_injection_recommendation(self, injection: dict,
                                                week_start: date) -> dict | None:
        """Simpan rekomendasi inject capital di Redis, kirim notif."""
        try:
            amount = float(injection.get("amount") or 0)
        except (TypeError, ValueError):
            return None

        if amount <= 0 or amount > MAX_INJECTION_RECOMMENDATION_USD:
            log.warning("Injection amount %s out of bounds [0, %s] — skip",
                        amount, MAX_INJECTION_RECOMMENDATION_USD)
            return None

        injection_id = secrets.token_hex(8)
        reason = str(injection.get("reason", ""))[:300]
        impact = str(injection.get("expected_impact", ""))[:300]

        record = {
            "id":              injection_id,
            "amount":          round(amount, 2),
            "reason":          reason,
            "expected_impact": impact,
            "recommended_at":  datetime.now(timezone.utc).isoformat(),
            "week_start":      week_start.isoformat(),
            "status":          "pending",
            "expires_at":      (
                datetime.now(timezone.utc) + timedelta(days=INJECTION_TTL_DAYS)
            ).isoformat(),
        }

        # Append ke Redis list
        existing = await redis.get(PENDING_INJECTIONS_KEY)
        try:
            pending_list = json.loads(existing) if existing else []
        except Exception:
            pending_list = []
        pending_list.append(record)
        # Simpan dengan TTL 14 hari (lebih lama dari injection TTL untuk audit)
        await redis.setex(
            PENDING_INJECTIONS_KEY, 14 * 24 * 3600,
            json.dumps(pending_list)
        )

        # Audit
        await db.log_event(
            event_type = "capital_injection_recommended",
            severity   = "info",
            message    = f"Opus recommends ${amount:.2f} injection",
            data       = {
                "injection_id": injection_id,
                "amount":       amount,
                "reason":       reason[:200],
            },
        )

        # Notif user dengan tombol approve/reject
        try:
            from notifications.telegram_bot import telegram
            await telegram.send_with_buttons(
                f"CAPITAL INJECTION RECOMMENDATION\n\n"
                f"Opus merekomendasikan tambahan modal: ${amount:.2f}\n\n"
                f"Alasan:\n{reason[:200]}\n\n"
                f"Expected impact:\n{impact[:200]}\n\n"
                f"Approve = capital tracking akan di-update.\n"
                f"Anda harus transfer dana ke akun Bybit secara manual.",
                [[
                    {"text": "Approve",
                     "callback_data": f"injection_approve_{injection_id}"},
                    {"text": "Reject",
                     "callback_data": f"injection_reject_{injection_id}"},
                ]],
            )
        except Exception as e:
            log.error("Failed to send injection notif: %s", e)

        return record

    async def get_pending_injections(self) -> list[dict]:
        """List rekomendasi injection yang masih pending dan belum expired."""
        existing = await redis.get(PENDING_INJECTIONS_KEY)
        if not existing:
            return []
        try:
            items = json.loads(existing)
        except Exception:
            return []

        now = datetime.now(timezone.utc)
        valid = []
        for item in items:
            if item.get("status") != "pending":
                continue
            try:
                expires = datetime.fromisoformat(item["expires_at"])
                if expires < now:
                    continue
            except Exception:
                continue
            valid.append(item)
        return valid

    async def approve_injection(self, injection_id: str,
                                 approved_by: str = "user") -> dict | None:
        """User approve injection — update capital tracking."""
        existing = await redis.get(PENDING_INJECTIONS_KEY)
        if not existing:
            return None
        try:
            items = json.loads(existing)
        except Exception:
            return None

        target = None
        for item in items:
            if item.get("id") == injection_id and item.get("status") == "pending":
                target = item
                item["status"]      = "approved"
                item["approved_at"] = datetime.now(timezone.utc).isoformat()
                item["approved_by"] = approved_by
                break

        if not target:
            return None

        # Update capital tracking — upsert snapshot hari ini dengan capital baru
        try:
            current_capital = await db.get_current_capital()
            new_capital     = current_capital + float(target["amount"])

            from database.models import PortfolioSnapshot
            tier = settings.get_tier(new_capital)
            infra = await db.get_infra_balance()
            active_pairs = await db.get_active_pairs()
            snap = PortfolioSnapshot(
                snapshot_date    = date.today(),
                total_capital    = new_capital,
                trading_capital  = max(0, new_capital - infra),
                infra_reserve    = infra,
                emergency_buffer = round(new_capital * 0.05, 4),
                current_tier     = tier,
                active_pairs     = active_pairs,
                daily_pnl        = 0,
                drawdown_pct     = 0,
            )
            await db.save_portfolio_snapshot(snap)

            await db.log_event(
                event_type = "capital_injection_approved",
                severity   = "info",
                message    = f"Capital injection ${target['amount']:.2f} approved",
                data = {
                    "injection_id":     injection_id,
                    "amount":           target["amount"],
                    "previous_capital": current_capital,
                    "new_capital":      new_capital,
                    "approved_by":      approved_by,
                },
            )

            log.info("Capital injection %s approved: $%.2f → new total $%.2f",
                     injection_id, target["amount"], new_capital)

        except Exception as e:
            log.error("Failed to apply injection: %s", e, exc_info=True)
            return None

        # Save updated list
        await redis.setex(
            PENDING_INJECTIONS_KEY, 14 * 24 * 3600, json.dumps(items)
        )

        return target

    async def reject_injection(self, injection_id: str) -> bool:
        existing = await redis.get(PENDING_INJECTIONS_KEY)
        if not existing:
            return False
        try:
            items = json.loads(existing)
        except Exception:
            return False

        found = False
        for item in items:
            if item.get("id") == injection_id and item.get("status") == "pending":
                item["status"]     = "rejected"
                item["rejected_at"]= datetime.now(timezone.utc).isoformat()
                found = True
                break

        if not found:
            return False

        await redis.setex(
            PENDING_INJECTIONS_KEY, 14 * 24 * 3600, json.dumps(items)
        )
        await db.log_event(
            event_type = "capital_injection_rejected",
            severity   = "info",
            message    = f"Capital injection {injection_id} rejected by user",
        )
        return True


auto_evolution = AutoEvolution()
