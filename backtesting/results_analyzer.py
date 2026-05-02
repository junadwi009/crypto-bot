"""
backtesting/results_analyzer.py
Analisis hasil backtest dengan Opus.
Bandingkan dengan parameter live, kasih saran param changes.

PATCHED 2026-05-02:
- Model ID Opus 4.5 yang benar
- Suggestions di-feed ke strategy_params lewat opus_brain whitelist
"""

from __future__ import annotations
import json
import logging
from pathlib import Path

import anthropic

from config.settings import settings
from database.client import db

log = logging.getLogger("results_analyzer")

MODEL = "claude-opus-4-5-20251101"

INPUT_COST  = 15.00 / 1_000_000
OUTPUT_COST = 75.00 / 1_000_000

_PROMPT = """You are a quantitative analyst.
Compare these backtest results vs the current LIVE params.
Identify if the backtest config performs meaningfully better than live config.
Respond ONLY with JSON.

{
  "should_update": true | false,
  "confidence": 0.0 to 1.0,
  "params_to_change": {
    "param_name": new_value
  },
  "reasoning": "max 50 words"
}

Rules:
- should_update=true only if backtest is CLEARLY better (sharpe +0.3 OR win_rate +5pp)
- params_to_change must be exact keys from live_params (not new params)
- Keep params_to_change small — max 3 params
"""


class ResultsAnalyzer:

    def __init__(self):
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def analyze(self, pair: str, backtest_result: dict) -> dict:
        """Compare backtest vs live params, return suggestion."""
        try:
            live_params = await db.get_strategy_params(pair)

            user_msg = (
                f"Pair: {pair}\n\n"
                f"BACKTEST RESULT:\n"
                f"  Sharpe ratio:  {backtest_result.get('sharpe_ratio', 0):.2f}\n"
                f"  Win rate:      {backtest_result.get('win_rate', 0) * 100:.1f}%\n"
                f"  Max drawdown:  {backtest_result.get('max_drawdown', 0) * 100:.1f}%\n"
                f"  Total trades:  {backtest_result.get('total_trades', 0)}\n"
                f"  Net PnL pct:   {backtest_result.get('net_pnl_pct', 0) * 100:.1f}%\n\n"
                f"BACKTEST CONFIG:\n"
                f"  {json.dumps(backtest_result.get('config', {}), indent=2)}\n\n"
                f"LIVE PARAMS:\n"
                f"  rsi_period:             {live_params.rsi_period}\n"
                f"  rsi_oversold:           {live_params.rsi_oversold}\n"
                f"  rsi_overbought:         {live_params.rsi_overbought}\n"
                f"  stop_loss_pct:          {live_params.stop_loss_pct}\n"
                f"  take_profit_pct:        {live_params.take_profit_pct}\n"
                f"  atr_no_trade_threshold: {live_params.atr_no_trade_threshold}\n"
                f"  position_multiplier:    {live_params.position_multiplier}\n\n"
                f"LIVE WIN RATE (30d): {await db.get_win_rate(days=30) * 100:.1f}%\n"
            )

            response = self._client.messages.create(
                model      = MODEL,
                max_tokens = 400,
                system     = _PROMPT,
                messages   = [{"role": "user", "content": user_msg}],
            )

            raw  = response.content[0].text.strip()
            data = self._parse(raw)

            usage = response.usage
            cost  = (usage.input_tokens  * INPUT_COST +
                     usage.output_tokens * OUTPUT_COST)
            await db.log_claude_usage(
                model="opus", calls=1,
                input_tok=usage.input_tokens,
                output_tok=usage.output_tokens,
                cost=cost, purpose="backtest_analysis",
            )

            log.info("Backtest analysis %s: should_update=%s conf=%.2f cost=$%.4f",
                     pair, data.get("should_update"),
                     data.get("confidence", 0), cost)
            return data

        except Exception as e:
            log.error("Backtest analyzer error %s: %s", pair, e)
            return {"should_update": False, "confidence": 0.0,
                    "params_to_change": {}, "reasoning": f"error: {e}"}

    def _parse(self, raw: str) -> dict:
        try:
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean.rsplit("```", 1)[0]
            return json.loads(clean.strip())
        except Exception as e:
            log.warning("Backtest analyzer parse error: %s", e)
            return {"should_update": False, "confidence": 0.0,
                    "params_to_change": {}, "reasoning": "parse_error"}


results_analyzer = ResultsAnalyzer()
