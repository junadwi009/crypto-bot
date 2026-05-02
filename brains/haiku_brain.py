"""
brains/haiku_brain.py
Haiku — fast brain.
Validasi sinyal rule-based dengan cepat dan murah.
Dipanggil ~30 kali/hari di tier Seed.

PATCHED 2026-05-02:
- Model ID Haiku 4.5 sudah benar (claude-haiku-4-5-20251001)
- Pricing diperbarui: $1/M input, $5/M output (Haiku 4.5 rate)
- Robust JSON parser: handle markdown fences dengan aman
- Error fallback tidak lagi pakai rule_signal mentah agar
  tidak bypass safety check Haiku
"""

from __future__ import annotations
import json
import logging
from pathlib import Path

import anthropic

from config.settings import settings
from database.client import db

log = logging.getLogger("haiku_brain")

_SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "haiku_system.txt"
).read_text()

MODEL    = "claude-haiku-4-5-20251001"
MAX_TOKENS = 120

# Pricing Haiku 4.5
INPUT_COST  = 1.00 / 1_000_000
OUTPUT_COST = 5.00 / 1_000_000


class HaikuBrain:

    def __init__(self):
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def validate(self, pair: str, rule_signal: dict,
                       indicators: dict) -> dict:
        params = await db.get_strategy_params(pair)

        system = _SYSTEM_PROMPT.replace(
            "{atr_threshold}", str(params.atr_no_trade_threshold)
        )

        # BB position helper — guard against zero/missing bb_mid
        price_now = float(indicators.get("price", 0) or 0)
        bb_mid    = float(indicators.get("bb_mid", 0) or 0)
        bb_label  = "above_mid" if (bb_mid > 0 and price_now > bb_mid) else "below_mid"

        user_msg = (
            f"Pair: {pair}\n"
            f"Rule signal: {rule_signal['action']} (confidence {rule_signal['confidence']:.2f})\n"
            f"Rule reason: {rule_signal.get('reason', '')}\n\n"
            f"Indicators:\n"
            f"  Price:        {price_now:.4f}\n"
            f"  RSI:          {indicators.get('rsi', 50):.1f}\n"
            f"  MACD hist:    {indicators.get('macd_hist', 0):.6f}\n"
            f"  ATR%:         {indicators.get('atr_pct', 0):.2f}\n"
            f"  Volume ratio: {indicators.get('volume_ratio', 1):.2f}\n"
            f"  BB position:  price={bb_label}\n"
        )

        try:
            response = self._client.messages.create(
                model      = MODEL,
                max_tokens = MAX_TOKENS,
                system     = system,
                messages   = [{"role": "user", "content": user_msg}],
            )

            raw  = response.content[0].text.strip()
            data = self._parse(raw)

            usage = response.usage
            cost  = (usage.input_tokens * INPUT_COST +
                     usage.output_tokens * OUTPUT_COST)

            await db.log_claude_usage(
                model      = "haiku",
                calls      = 1,
                input_tok  = usage.input_tokens,
                output_tok = usage.output_tokens,
                cost       = cost,
                purpose    = "signal_validation",
            )

            log.debug("Haiku %s: %s conf=%.2f cost=$%.5f",
                      pair, data["action"], data["confidence"], cost)
            return data

        except Exception as e:
            log.error("Haiku error for %s: %s", pair, e)
            # Fallback: hold signal — jangan teruskan rule signal mentah,
            # itu memungkinkan trade lewat saat Haiku error (tidak aman).
            return {
                "action":     "hold",
                "confidence": 0.0,
                "reason":     f"haiku_error: {type(e).__name__}",
                "source":     "haiku_fallback",
            }

    def _parse(self, raw: str) -> dict:
        try:
            clean = raw.strip()
            # Bersihkan code fence dengan tepat
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean.rsplit("```", 1)[0]
            clean = clean.strip()

            data = json.loads(clean)

            action     = str(data.get("action", "hold")).lower()
            confidence = float(data.get("confidence", 0.0))
            reason     = str(data.get("reason", ""))[:100]

            if action not in ("buy", "sell", "hold"):
                action = "hold"
            confidence = max(0.0, min(1.0, confidence))

            return {
                "action":     action,
                "confidence": round(confidence, 3),
                "reason":     reason,
                "source":     "haiku",
            }
        except Exception as e:
            log.warning("Haiku parse error: %s | raw=%s", e, raw[:100])
            return {"action": "hold", "confidence": 0.0,
                    "reason": "parse_error", "source": "haiku"}


haiku_brain = HaikuBrain()
