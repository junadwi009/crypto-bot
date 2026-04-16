"""
engine/signal_generator.py
Orkestrasi pipeline sinyal lengkap:
  Rule-based → (jika cukup kuat) → Haiku → (jika kuat) → Sonnet
  → OrderGuard → Order execution

Ini jantung dari bot — semua keputusan trading dimulai di sini.

PATCHED 2026-04-16:
- Haiku confidence threshold: 0.60 → 0.45 (P0 fix low trade frequency)
- Sonnet threshold: 0.75 → 0.65
- Tambah log jumlah cycle per pair untuk monitoring
"""

from __future__ import annotations
import logging

from config.settings import settings
from database.client import db
from database.models import TradeSignal
from engine.rule_based import rule_engine
from engine.order_guard import order_guard
from engine.position_manager import position_manager
from exchange.bybit_client import bybit
from exchange.order_manager import order_manager

log = logging.getLogger("signal_generator")

# Lazy import untuk menghindari circular dependency
def _haiku():
    from brains.haiku_brain import haiku_brain
    return haiku_brain

def _sonnet():
    from brains.sonnet_brain import sonnet_brain
    return sonnet_brain

# PATCHED: turunkan dari 0.60 → 0.45
HAIKU_CONFIDENCE_THRESHOLD = 0.45
# PATCHED: turunkan dari 0.75 → 0.65
SONNET_CONFIDENCE_THRESHOLD = 0.65


class SignalGenerator:

    async def process(self, pair: str):
        """
        Proses satu pair melalui pipeline sinyal lengkap.
        Dipanggil dari trading_loop di main.py setiap 30 detik.
        """
        try:
            # ── Step 1: Rule-based analysis (gratis, cepat) ──────────
            rule_result = await rule_engine.analyze(pair)

            if rule_result["action"] == "hold":
                log.debug("%s: rule-based → hold (%s)",
                          pair, rule_result["reason"][:60])
                return

            log.info("%s: rule-based → %s conf=%.2f | %s",
                     pair, rule_result["action"],
                     rule_result["confidence"],
                     rule_result["reason"][:80])

            # ── Step 2: Haiku validation (cepat, murah) ──────────────
            capital = await db.get_current_capital()
            limits  = settings.get_claude_limits(capital)
            calls_today = await db.get_claude_calls_today("haiku")

            haiku_result = None
            if calls_today < limits["haiku"]:
                haiku_result = await _haiku().validate(
                    pair       = pair,
                    rule_signal= rule_result,
                    indicators = rule_result.get("indicators", {}),
                )

                if haiku_result["action"] == "hold":
                    log.info("%s: haiku → hold (%s)",
                             pair, haiku_result.get("reason", "")[:60])
                    return

                log.info("%s: haiku → %s conf=%.2f",
                         pair, haiku_result["action"],
                         haiku_result["confidence"])
            else:
                log.debug("%s: Haiku rate limit reached (%d/%d) — using rule signal only",
                          pair, calls_today, limits["haiku"])
                haiku_result = rule_result

            # Confidence threshold setelah Haiku (PATCHED: 0.60 → 0.45)
            if haiku_result["confidence"] < HAIKU_CONFIDENCE_THRESHOLD:
                log.info(
                    "%s: confidence too low after Haiku (%.2f < %.2f) — skip",
                    pair, haiku_result["confidence"], HAIKU_CONFIDENCE_THRESHOLD,
                )
                return

            # ── Step 3: Sonnet confirmation (hanya untuk high-confidence) ──
            final_signal = haiku_result
            sonnet_calls = await db.get_claude_calls_today("sonnet")

            if (haiku_result["confidence"] >= SONNET_CONFIDENCE_THRESHOLD
                    and sonnet_calls < limits["sonnet"]):
                sonnet_result = await _sonnet().confirm(
                    pair         = pair,
                    haiku_signal = haiku_result,
                    indicators   = rule_result.get("indicators", {}),
                )
                if sonnet_result["action"] == "hold":
                    log.info("%s: sonnet → hold (%s)",
                             pair, sonnet_result.get("reason", "")[:60])
                    return
                final_signal = sonnet_result
                log.info("%s: sonnet → %s conf=%.2f",
                         pair, final_signal["action"], final_signal["confidence"])

            # ── Step 4: Build TradeSignal ─────────────────────────────
            current_price = await bybit.get_price(pair)
            size = await position_manager.calc_position_size(
                pair, final_signal["confidence"]
            )
            sl = await position_manager.calc_stop_loss_price(
                pair, current_price, final_signal["action"]
            )
            tp = await position_manager.calc_take_profit_price(
                pair, current_price, final_signal["action"]
            )

            signal = TradeSignal(
                pair           = pair,
                action         = final_signal["action"],
                confidence     = final_signal["confidence"],
                source         = final_signal.get("source", "rule_based"),
                reason         = final_signal.get("reason", ""),
                price          = current_price,
                suggested_size = size,
                stop_loss      = sl,
                take_profit    = tp,
            )

            # ── Step 5: OrderGuard validation ────────────────────────
            approved, reason = await order_guard.approve(
                pair       = pair,
                side       = signal.action,
                amount_usd = signal.suggested_size,
                capital    = capital,
            )

            if not approved:
                log.info("%s: order rejected by guard (%s)", pair, reason)
                return

            # ── Step 6: Execute ───────────────────────────────────────
            trade_id = await order_manager.open_position(signal)

            if trade_id:
                log.info("%s: trade executed → id=%s", pair, trade_id)
            else:
                log.warning("%s: order execution failed", pair)

        except Exception as e:
            log.error("Signal pipeline error for %s: %s", pair, e, exc_info=True)

    async def monitor(self):
        """
        Monitor posisi terbuka — cek SL/TP.
        Dipanggil terpisah dari process() agar tidak blocking.
        """
        await order_manager.monitor_open_trades()


signal_generator = SignalGenerator()