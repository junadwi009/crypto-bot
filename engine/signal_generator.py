"""
engine/signal_generator.py
Orkestrasi pipeline sinyal lengkap:
  Rule-based → (jika cukup kuat) → Haiku → (jika kuat) → Sonnet
  → OrderGuard → Orchestrator (observe-only) → Order execution

PATCHED 2026-05-07 (Phase 2 — orchestrator wiring):
- Every pipeline invocation produces an orchestrator_decisions row,
  including: rule-hold, haiku-reject, sonnet-reject, guard-reject,
  exception path. The audit trail is unconditional (Council constraint 18).
- Each early-exit calls _record_decision() with stage_reached so the
  forensic snapshot tells us exactly where the pipeline terminated.
- Orchestrator.evaluate() is the single sink before order_manager.

PATCHED 2026-05-07 (Phase 1):
- LayerZeroViolation re-raised before broad except.
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
from governance.exceptions import LayerZeroViolation
from governance.redis_acl import RedisACLViolation
from governance.orchestrator import orchestrator

log = logging.getLogger("signal_generator")


def _haiku():
    from brains.haiku_brain import haiku_brain
    return haiku_brain

def _sonnet():
    from brains.sonnet_brain import sonnet_brain
    return sonnet_brain


HAIKU_CONFIDENCE_THRESHOLD  = 0.45
SONNET_CONFIDENCE_THRESHOLD = 0.65

HAIKU_THRESHOLD_OPPTY  = 0.35
SONNET_THRESHOLD_OPPTY = 0.55


class SignalGenerator:

    async def process(self, pair: str):
        """Run one pipeline invocation. ALWAYS produces an orchestrator
        decision row, regardless of whether a trade is placed.
        """
        pipeline_state: dict = {
            "pair":          pair,
            "stage_reached": "entry",
            "rule_result":   None,
            "haiku_result":  None,
            "sonnet_result": None,
            "guard_state":   None,
        }

        try:
            # ── Cek news opportunity flag ──
            try:
                from engine.news_action_executor import news_action_executor
                is_oppty = await news_action_executor.is_opportunity(pair)
            except RedisACLViolation:
                raise
            except LayerZeroViolation:
                raise
            except Exception:
                is_oppty = False

            haiku_thresh  = HAIKU_THRESHOLD_OPPTY  if is_oppty else HAIKU_CONFIDENCE_THRESHOLD
            sonnet_thresh = SONNET_THRESHOLD_OPPTY if is_oppty else SONNET_CONFIDENCE_THRESHOLD

            # ── Step 1: Rule-based ──
            rule_result = await rule_engine.analyze(pair)
            pipeline_state["rule_result"] = rule_result
            pipeline_state["stage_reached"] = "rule"

            if rule_result["action"] == "hold":
                log.debug("%s: rule-based -> hold (%s)",
                          pair, str(rule_result.get("reason", ""))[:60])
                # Constraint 18: record even on hold
                await self._record_decision(
                    pair=pair, requested_action="hold",
                    pipeline_state=pipeline_state,
                )
                return

            log.info("%s: rule-based -> %s conf=%.2f | %s",
                     pair, rule_result["action"], rule_result["confidence"],
                     str(rule_result.get("reason", ""))[:80])

            # ── Step 2: Haiku validation ──
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
                pipeline_state["haiku_result"] = haiku_result
                pipeline_state["stage_reached"] = "haiku"

                if haiku_result["action"] == "hold":
                    log.info("%s: haiku -> hold", pair)
                    await self._record_decision(
                        pair=pair, requested_action="hold",
                        pipeline_state=pipeline_state,
                    )
                    return
            else:
                log.debug("%s: haiku rate limit reached, using rule signal", pair)
                haiku_result = rule_result
                pipeline_state["haiku_result"] = {
                    **rule_result, "_note": "rate_limited_fallback_to_rule",
                }
                pipeline_state["stage_reached"] = "haiku_rate_limited"

            if haiku_result["confidence"] < haiku_thresh:
                log.info("%s: confidence too low after haiku (%.2f < %.2f)",
                         pair, haiku_result["confidence"], haiku_thresh)
                await self._record_decision(
                    pair=pair, requested_action="hold",
                    pipeline_state=pipeline_state,
                )
                return

            # ── Step 3: Sonnet confirmation ──
            final_signal = haiku_result
            sonnet_calls = await db.get_claude_calls_today("sonnet")

            if (haiku_result["confidence"] >= sonnet_thresh
                    and sonnet_calls < limits["sonnet"]):
                sonnet_result = await _sonnet().confirm(
                    pair         = pair,
                    haiku_signal = haiku_result,
                    indicators   = rule_result.get("indicators", {}),
                )
                pipeline_state["sonnet_result"] = sonnet_result
                pipeline_state["stage_reached"] = "sonnet"

                if sonnet_result["action"] == "hold":
                    log.info("%s: sonnet -> hold", pair)
                    await self._record_decision(
                        pair=pair, requested_action="hold",
                        pipeline_state=pipeline_state,
                    )
                    return
                final_signal = sonnet_result
                log.info("%s: sonnet -> %s conf=%.2f",
                         pair, final_signal["action"], final_signal["confidence"])

            # ── Step 4: Build TradeSignal ──
            current_price = await bybit.get_price(pair)
            if current_price <= 0:
                log.warning("%s: invalid current price -- skip", pair)
                pipeline_state["stage_reached"] = "invalid_price"
                await self._record_decision(
                    pair=pair, requested_action="hold",
                    pipeline_state=pipeline_state,
                )
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

            # ── Step 5: OrderGuard ──
            approved, reason = await order_guard.approve(
                pair       = pair,
                side       = signal.action,
                amount_usd = signal.suggested_size,
                capital    = capital,
            )
            pipeline_state["guard_state"] = {"approved": approved, "reason": reason}
            pipeline_state["stage_reached"] = "guard"

            if not approved:
                log.info("%s: order rejected by guard (%s)", pair, reason)
                await self._record_decision(
                    pair=pair, requested_action="hold",
                    pipeline_state=pipeline_state,
                    proposed_size_usd=size, proposed_sl=sl, proposed_tp=tp,
                )
                return

            # ── Step 6: Orchestrator (observe-only) ──
            pipeline_state["stage_reached"] = "orchestrator"
            decision = await orchestrator.evaluate(
                pair              = pair,
                requested_action  = signal.action,
                pipeline_state    = pipeline_state,
                proposed_size_usd = size,
                proposed_sl       = sl,
                proposed_tp       = tp,
            )

            # In observe-only, decision.final_action == requested. In Phase-3
            # enforcement, may be 'hold' due to vetoes — respect it.
            if decision.final_action == "hold":
                log.info("%s: orchestrator vetoed (%s)", pair, ",".join(decision.layer_vetoes))
                return

            # Apply any size scaling from orchestrator (1.0 in observe-only)
            if decision.size_usd != size and decision.size_usd > 0:
                signal.suggested_size = decision.size_usd

            # ── Step 7: Execute ──
            trade_id = await order_manager.open_position(signal)
            if trade_id:
                log.info("%s: trade executed -> id=%s", pair, trade_id)

        except RedisACLViolation:
            raise
        except LayerZeroViolation:
            raise
        except Exception as e:
            log.error("Signal pipeline error for %s: %s", pair, e, exc_info=True)
            # Best-effort: even on exception, try to record what we know.
            pipeline_state["stage_reached"] = f"exception:{type(e).__name__}"
            try:
                await self._record_decision(
                    pair=pair, requested_action="hold",
                    pipeline_state=pipeline_state,
                )
            except RedisACLViolation:
                raise
            except LayerZeroViolation:
                raise
            except Exception:
                pass

    async def _record_decision(
        self, pair: str, requested_action: str,
        pipeline_state: dict,
        proposed_size_usd: float = 0.0,
        proposed_sl: float | None = None,
        proposed_tp: float | None = None,
    ):
        """Record a decision row for an early-exit pipeline path.

        Constraint 18: every pipeline invocation produces a row, including
        rule-hold, guard-reject, exception path, etc.
        """
        try:
            await orchestrator.evaluate(
                pair              = pair,
                requested_action  = requested_action,
                pipeline_state    = pipeline_state,
                proposed_size_usd = proposed_size_usd,
                proposed_sl       = proposed_sl,
                proposed_tp       = proposed_tp,
            )
        except RedisACLViolation:
            raise
        except LayerZeroViolation:
            raise
        except Exception as e:
            log.error("orchestrator decision recording failed for %s: %s", pair, e)

    async def monitor(self):
        await order_manager.monitor_open_trades()


signal_generator = SignalGenerator()
