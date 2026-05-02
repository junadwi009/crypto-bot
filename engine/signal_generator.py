"""
engine/signal_generator.py
Orkestrasi pipeline sinyal lengkap:
  Rule-based → (jika cukup kuat) → Haiku → (jika kuat) → Sonnet
  → OrderGuard → Order execution

PATCHED 2026-05-02:
- Threshold sama (0.45/0.65) — sudah di-tune untuk paper mode
- News opportunity flag boost confidence threshold ke bawah
  (lebih agresif kalau ada sinyal berita bullish kuat)
- Defensive checks
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


def _haiku():
    from brains.haiku_brain import haiku_brain
    return haiku_brain

def _sonnet():
    from brains.sonnet_brain import sonnet_brain
    return sonnet_brain


HAIKU_CONFIDENCE_THRESHOLD  = 0.45
SONNET_CONFIDENCE_THRESHOLD = 0.65

# Boost saat news opportunity aktif
HAIKU_THRESHOLD_OPPTY  = 0.35
SONNET_THRESHOLD_OPPTY = 0.55


class SignalGenerator:

    async def process(self, pair: str):
        try:
            # Cek news opportunity flag
            try:
                from engine.news_action_executor import news_action_executor
                is_oppty = await news_action_executor.is_opportunity(pair)
            except Exception:
                is_oppty = False

            haiku_thresh  = HAIKU_THRESHOLD_OPPTY  if is_oppty else HAIKU_CONFIDENCE_THRESHOLD
            sonnet_thresh = SONNET_THRESHOLD_OPPTY if is_oppty else SONNET_CONFIDENCE_THRESHOLD

            # Step 1: Rule-based
            rule_result = await rule_engine.analyze(pair)

            if rule_result["action"] == "hold":
                log.debug("%s: rule-based → hold (%s)",
                          pair, str(rule_result.get("reason", ""))[:60])
                return

            log.info("%s: rule-based → %s conf=%.2f | %s",
                     pair, rule_result["action"],
                     rule_result["confidence"],
                     str(rule_result.get("reason", ""))[:80])

            # Step 2: Haiku validation
            capital = await db.get_current_capital()
            limits  = settings.get_claude_limits(capital)
            calls_today = await db.get_claude_calls_today("haiku")

            haiku_result = None
            if limits["haiku"] == -1 or calls_today < limits["haiku"]:
                haiku_result = await _haiku().validate(
                    pair       = pair,
                    rule_signal= rule_result,
                    indicators = rule_result.get("indicators", {}),
                )

                if haiku_result["action"] == "hold":
                    log.info("%s: haiku → hold (%s)",
                             pair, str(haiku_result.get("reason", ""))[:60])
                    return

                log.info("%s: haiku → %s conf=%.2f",
                         pair, haiku_result["action"],
                         haiku_result["confidence"])
            else:
                log.debug("%s: Haiku rate limit reached (%d/%d) — using rule signal",
                          pair, calls_today, limits["haiku"])
                haiku_result = rule_result

            if haiku_result["confidence"] < haiku_thresh:
                log.info(
                    "%s: confidence too low after Haiku (%.2f < %.2f) — skip",
                    pair, haiku_result["confidence"], haiku_thresh,
                )
                return

            # Step 3: Sonnet confirmation
            final_signal = haiku_result
            sonnet_calls = await db.get_claude_calls_today("sonnet")

            if (haiku_result["confidence"] >= sonnet_thresh
                    and sonnet_calls < limits["sonnet"]):
                sonnet_result = await _sonnet().confirm(
                    pair         = pair,
                    haiku_signal = haiku_result,
                    indicators   = rule_result.get("indicators", {}),
                )
                if sonnet_result["action"] == "hold":
                    log.info("%s: sonnet → hold (%s)",
                             pair, str(sonnet_result.get("reason", ""))[:60])
                    return
                final_signal = sonnet_result
                log.info("%s: sonnet → %s conf=%.2f",
                         pair, final_signal["action"], final_signal["confidence"])

            # Step 4: Build TradeSignal
            current_price = await bybit.get_price(pair)
            if current_price <= 0:
                log.warning("%s: invalid current price — skip", pair)
                return

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
                reason         = str(final_signal.get("reason", ""))[:200],
                price          = current_price,
                suggested_size = size,
                stop_loss      = sl,
                take_profit    = tp,
            )

            # Step 5: OrderGuard
            approved, reason = await order_guard.approve(
                pair       = pair,
                side       = signal.action,
                amount_usd = signal.suggested_size,
                capital    = capital,
            )

            if not approved:
                log.info("%s: order rejected by guard (%s)", pair, reason)
                return

            # Step 6: Execute
            trade_id = await order_manager.open_position(signal)
            if trade_id:
                log.info("%s: trade executed → id=%s", pair, trade_id)
            else:
                log.warning("%s: order execution failed", pair)

        except Exception as e:
            log.error("Signal pipeline error for %s: %s", pair, e, exc_info=True)

    async def monitor(self):
        await order_manager.monitor_open_trades()


signal_generator = SignalGenerator()
