"""
brains/haiku_brain.py
Haiku — fast brain.
Validasi sinyal rule-based dengan cepat dan murah.
Dipanggil ~30 kali/hari di tier Seed.
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

# Biaya per token Haiku
INPUT_COST  = 0.80  / 1_000_000
OUTPUT_COST = 4.00  / 1_000_000


class HaikuBrain:

    def __init__(self):
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def validate(self, pair: str, rule_signal: dict,
                       indicators: dict) -> dict:
        """
        Validasi sinyal dari rule-based engine.
        Return dict dengan action, confidence, reason.
        """
        params = await db.get_strategy_params(pair)

        system = _SYSTEM_PROMPT.replace(
            "{atr_threshold}", str(params.atr_no_trade_threshold)
        )

        user_msg = (
            f"Pair: {pair}\n"
            f"Rule signal: {rule_signal['action']} (confidence {rule_signal['confidence']:.2f})\n"
            f"Rule reason: {rule_signal.get('reason', '')}\n\n"
            f"Indicators:\n"
            f"  Price:        {indicators.get('price', 0):.4f}\n"
            f"  RSI:          {indicators.get('rsi', 50):.1f}\n"
            f"  MACD hist:    {indicators.get('macd_hist', 0):.6f}\n"
            f"  ATR%:         {indicators.get('atr_pct', 0):.2f}\n"
            f"  Volume ratio: {indicators.get('volume_ratio', 1):.2f}\n"
            f"  BB position:  price={'above_mid' if indicators.get('price',0) > indicators.get('bb_mid',0) else 'below_mid'}\n"
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

            # Track usage
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
            # Fallback — kembalikan sinyal rule-based
            return rule_signal

    def _parse(self, raw: str) -> dict:
        """Parse JSON response dengan fallback."""
        try:
            # Bersihkan markdown jika ada
            clean = raw.strip().lstrip("```json").rstrip("```").strip()
            data  = json.loads(clean)

            action     = data.get("action", "hold").lower()
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
