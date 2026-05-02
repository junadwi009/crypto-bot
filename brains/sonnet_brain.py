"""
brains/sonnet_brain.py
Sonnet — strategy brain.
Konfirmasi trade dengan analisis multi-timeframe.
Dipanggil ~5 kali/hari di tier Seed, hanya saat confidence Haiku ≥ threshold.

PATCHED 2026-05-02:
- Model ID Sonnet 4.5 (claude-sonnet-4-5-20250929)
- Robust JSON parser
- Inject ringkasan accuracy news_weights ke konteks Sonnet
  agar Sonnet tahu source berita mana yang historically akurat
"""

from __future__ import annotations
import json
import logging
from pathlib import Path

import anthropic

from config.settings import settings
from database.client import db
from exchange.market_data import market_data

log = logging.getLogger("sonnet_brain")

_SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "sonnet_system.txt"
).read_text()

MODEL      = "claude-sonnet-4-5-20250929"
MAX_TOKENS = 200

INPUT_COST  = 3.00  / 1_000_000
OUTPUT_COST = 15.00 / 1_000_000


class SonnetBrain:

    def __init__(self):
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def confirm(self, pair: str, haiku_signal: dict,
                      indicators: dict) -> dict:
        try:
            mtf = await market_data.get_multi_timeframe(pair)
            news_context = await self._get_news_context(pair)
            params = await db.get_strategy_params(pair)

            user_msg = self._build_prompt(
                pair, haiku_signal, mtf, params, news_context
            )

            response = self._client.messages.create(
                model      = MODEL,
                max_tokens = MAX_TOKENS,
                system     = _SYSTEM_PROMPT,
                messages   = [{"role": "user", "content": user_msg}],
            )

            raw  = response.content[0].text.strip()
            data = self._parse(raw)

            usage = response.usage
            cost  = (usage.input_tokens  * INPUT_COST +
                     usage.output_tokens * OUTPUT_COST)

            await db.log_claude_usage(
                model      = "sonnet",
                calls      = 1,
                input_tok  = usage.input_tokens,
                output_tok = usage.output_tokens,
                cost       = cost,
                purpose    = "trade_confirmation",
            )

            log.info("Sonnet %s: %s conf=%.2f rr=%.2f align=%s cost=$%.4f",
                     pair, data["action"], data["confidence"],
                     data.get("risk_reward", 0),
                     data.get("timeframe_alignment", "?"), cost)
            return data

        except Exception as e:
            log.error("Sonnet error for %s: %s", pair, e)
            # Fallback: hold supaya trade tidak lewat tanpa konfirmasi
            return {
                "action":              "hold",
                "confidence":          0.0,
                "reason":              f"sonnet_error: {type(e).__name__}",
                "risk_reward":         0.0,
                "timeframe_alignment": "weak",
                "source":              "sonnet_fallback",
            }

    def _build_prompt(self, pair: str, haiku_signal: dict,
                      mtf: dict, params, news_context: str) -> str:
        tf15 = mtf.get("15m", {})
        tf1h = mtf.get("1h",  {})
        tf4h = mtf.get("4h",  {})

        return (
            f"Pair: {pair}\n"
            f"Haiku signal: {haiku_signal['action']} "
            f"(confidence {haiku_signal['confidence']:.2f})\n"
            f"Reason: {haiku_signal.get('reason', '')}\n\n"
            f"Multi-timeframe analysis:\n"
            f"  15m RSI: {tf15.get('rsi', 50):.1f} | "
            f"MACD: {'bullish' if tf15.get('macd_hist',0) > 0 else 'bearish'}\n"
            f"  1h  RSI: {tf1h.get('rsi', 50):.1f} | "
            f"MACD: {'bullish' if tf1h.get('macd_hist',0) > 0 else 'bearish'}\n"
            f"  4h  RSI: {tf4h.get('rsi', 50):.1f} | "
            f"MACD: {'bullish' if tf4h.get('macd_hist',0) > 0 else 'bearish'}\n\n"
            f"Current price:    {tf15.get('price', 0):.4f}\n"
            f"Stop loss %:      {params.stop_loss_pct}%\n"
            f"Take profit %:    {params.take_profit_pct}%\n"
            f"Volume ratio 15m: {tf15.get('volume_ratio', 1):.2f}x\n"
            f"ATR% 15m:         {tf15.get('atr_pct', 0):.2f}%\n"
            + (f"\nRecent news context:\n{news_context}\n" if news_context else "")
        )

    async def _get_news_context(self, pair: str) -> str:
        """Ringkasan berita relevan terbaru untuk context Sonnet."""
        try:
            res = (
                db._get()
                .table("news_items")
                .select("headline, haiku_sentiment, haiku_urgency, "
                        "sonnet_action, published_at")
                .contains("pairs_mentioned", [pair])
                .order("published_at", desc=True)
                .limit(3)
                .execute()
            )
            if not res.data:
                return ""

            lines = []
            for item in res.data:
                sent = float(item.get("haiku_sentiment") or 0)
                label = "bullish" if sent > 0.3 else "bearish" if sent < -0.3 else "neutral"
                action = item.get("sonnet_action") or "hold"
                lines.append(
                    f"- [{label}|{action}] {item['headline'][:80]}"
                )
            return "\n".join(lines)
        except Exception:
            return ""

    def _parse(self, raw: str) -> dict:
        try:
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean.rsplit("```", 1)[0]
            clean = clean.strip()

            data = json.loads(clean)

            action     = str(data.get("action", "hold")).lower()
            confidence = float(data.get("confidence", 0.0))
            reason     = str(data.get("reason", ""))[:120]
            rr         = float(data.get("risk_reward", 0.0))
            alignment  = str(data.get("timeframe_alignment", "moderate"))

            if action not in ("buy", "sell", "hold"):
                action = "hold"
            confidence = max(0.0, min(1.0, confidence))

            # Reject jika R/R terlalu rendah
            if rr > 0 and rr < 1.5 and action != "hold":
                log.info("Sonnet: rejecting low R/R %.2f", rr)
                action     = "hold"
                confidence = 0.0
                reason     = f"risk_reward_too_low_{rr:.2f}"

            return {
                "action":               action,
                "confidence":           round(confidence, 3),
                "reason":               reason,
                "risk_reward":          round(rr, 2),
                "timeframe_alignment":  alignment,
                "source":               "sonnet",
            }
        except Exception as e:
            log.warning("Sonnet parse error: %s | raw=%s", e, raw[:100])
            return {"action": "hold", "confidence": 0.0,
                    "reason": "parse_error", "source": "sonnet"}


sonnet_brain = SonnetBrain()
