"""
backtesting/results_analyzer.py
Analisis hasil backtest dan kirim ke Opus untuk interpretasi.
Dijalankan saat setup awal (sebelum paper trade) dan setiap kali ada pair baru.
"""

from __future__ import annotations
import logging

from database.client import db

log = logging.getLogger("results_analyzer")


class ResultsAnalyzer:

    async def analyze_and_brief_opus(self, results: list) -> dict:
        """
        Ambil hasil backtest, format sebagai context,
        kirim ke Opus untuk rekomendasi parameter awal.
        """
        if not results:
            return {}

        context = self._format_for_opus(results)

        try:
            import anthropic
            from config.settings import settings

            client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

            response = client.messages.create(
                model      = "claude-opus-4-6",
                max_tokens = 1000,
                system     = (
                    "You are a trading strategy optimizer. "
                    "Analyze backtest results and recommend optimal starting parameters. "
                    "Respond ONLY with JSON. No prose outside JSON.\n\n"
                    "{\n"
                    '  "best_pair": "XXX/USDT",\n'
                    '  "best_strategy": "strategy_name",\n'
                    '  "recommended_params": {"pair": {"param": value}},\n'
                    '  "pairs_to_activate": ["XXX/USDT"],\n'
                    '  "pairs_to_skip": ["XXX/USDT"],\n'
                    '  "key_insights": ["insight 1", "insight 2"],\n'
                    '  "confidence": 0.0\n'
                    "}"
                ),
                messages = [{"role": "user", "content": context}],
            )

            import json
            raw  = response.content[0].text.strip()
            data = json.loads(raw.lstrip("```json").rstrip("```").strip())

            # Log usage
            usage = response.usage
            cost  = (usage.input_tokens  * 15.0 / 1_000_000 +
                     usage.output_tokens * 75.0 / 1_000_000)
            await db.log_claude_usage(
                model="opus", calls=1,
                input_tok=usage.input_tokens,
                output_tok=usage.output_tokens,
                cost=cost, purpose="backtest_analysis",
            )

            # Apply rekomendasi parameter
            for pair, params in data.get("recommended_params", {}).items():
                if isinstance(params, dict):
                    safe = {k: v for k, v in params.items()
                            if isinstance(v, (int, float)) and v > 0}
                    if safe:
                        await db.update_strategy_params(pair, safe, "opus")

            log.info(
                "Opus backtest brief: best=%s insights=%d",
                data.get("best_pair"), len(data.get("key_insights", []))
            )
            return data

        except Exception as e:
            log.error("Opus briefing error: %s", e)
            return self._fallback_analysis(results)

    def _format_for_opus(self, results: list) -> str:
        lines = ["Backtest results summary:\n"]
        for r in results:
            lines.append(
                f"Pair: {r.pair} | Strategy: {r.strategy}\n"
                f"  Return: {r.total_return*100:.1f}% | "
                f"Win rate: {r.win_rate*100:.1f}% | "
                f"Sharpe: {r.sharpe_ratio:.2f} | "
                f"Max DD: {r.max_drawdown*100:.1f}% | "
                f"Trades: {r.total_trades}\n"
            )
        lines.append(
            "\nRecommend optimal starting parameters and "
            "which pairs to activate first."
        )
        return "\n".join(lines)

    def _fallback_analysis(self, results: list) -> dict:
        """Fallback jika Opus tidak tersedia."""
        if not results:
            return {}
        best = max(results, key=lambda r: r.sharpe_ratio)
        return {
            "best_pair":     best.pair,
            "best_strategy": best.strategy,
            "recommended_params": {},
            "pairs_to_activate": [best.pair],
            "key_insights": [
                f"Best pair by Sharpe: {best.pair} ({best.sharpe_ratio:.2f})"
            ],
            "confidence": 0.6,
        }


results_analyzer = ResultsAnalyzer()
