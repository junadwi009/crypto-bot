"""
brains/opus_brain.py
Opus — meta brain.
Evaluasi mingguan + tulis ulang parameter + weekly engineering log.
Dipanggil 1×/minggu di tier Seed, lebih sering di tier lebih tinggi.
"""

from __future__ import annotations
import json
import logging
from datetime import date, timedelta
from pathlib import Path

import anthropic

from config.settings import settings
from database.client import db
from database.models import OpusMemory

log = logging.getLogger("opus_brain")

_SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "opus_weekly.txt"
).read_text()

MODEL      = "claude-opus-4-6"
MAX_TOKENS = 2000

INPUT_COST  = 15.00 / 1_000_000
OUTPUT_COST = 75.00 / 1_000_000


class OpusBrain:

    def __init__(self):
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def weekly_evaluation(self) -> dict:
        """
        Evaluasi performa 7 hari terakhir.
        Update parameter otomatis, kirim action_required ke Telegram.
        Return hasil evaluasi lengkap.
        """
        log.info("Opus weekly evaluation starting...")

        week_end   = date.today()
        week_start = week_end - timedelta(days=7)

        try:
            # Kumpulkan semua data untuk Opus
            context = await self._build_context(week_start, week_end)

            response = self._client.messages.create(
                model      = MODEL,
                max_tokens = MAX_TOKENS,
                system     = _SYSTEM_PROMPT,
                messages   = [{"role": "user", "content": context}],
            )

            raw  = response.content[0].text.strip()
            data = self._parse(raw)

            # Hitung biaya
            usage = response.usage
            cost  = (usage.input_tokens  * INPUT_COST +
                     usage.output_tokens * OUTPUT_COST)
            data["token_cost"] = round(cost, 4)

            # Track usage
            await db.log_claude_usage(
                model      = "opus",
                calls      = 1,
                input_tok  = usage.input_tokens,
                output_tok = usage.output_tokens,
                cost       = cost,
                purpose    = "weekly_evaluation",
            )

            # ── Auto-update parameters ────────────────────────────────
            params_updated = await self._apply_param_updates(
                data.get("params_to_update", {})
            )

            # ── Auto-update news weights ──────────────────────────────
            await self._apply_news_weights(
                data.get("news_weights_update", {})
            )

            # ── Simpan ke database ────────────────────────────────────
            summary = data.get("summary", {})
            memory  = OpusMemory(
                week_start       = week_start,
                week_end         = week_end,
                win_rate         = float(summary.get("win_rate", 0)),
                total_pnl        = float(summary.get("total_pnl", 0)),
                max_drawdown     = float(summary.get("max_drawdown", 0)),
                total_trades     = int(summary.get("total_trades", 0)),
                sharpe_ratio     = float(summary.get("sharpe_ratio", 0)),
                patterns_found   = data.get("auto_updated", []),
                actions_required = data.get("action_required", []),
                params_updated   = params_updated,
                raw_analysis     = raw,
                token_cost       = cost,
            )
            await db.save_opus_memory(memory)

            log.info(
                "Opus evaluation complete: win_rate=%.1f%% pnl=$%.2f "
                "actions=%d cost=$%.4f",
                float(summary.get("win_rate", 0)) * 100,
                float(summary.get("total_pnl", 0)),
                len(data.get("action_required", [])),
                cost,
            )
            return data

        except Exception as e:
            log.error("Opus evaluation error: %s", e, exc_info=True)
            return {}

    async def _build_context(self, week_start: date, week_end: date) -> str:
        """Kumpulkan semua data yang dibutuhkan Opus."""
        summary      = await db.get_weekly_summary(days=7)
        recent_mem   = await db.get_recent_opus_memory(weeks=3)
        news_weights = await db.get_news_weights()
        active_pairs = await db.get_active_pairs()
        capital      = await db.get_current_capital()
        claude_cost  = await db.get_claude_cost_this_month()

        # Format previous evaluations untuk konteks
        prev_context = ""
        if recent_mem:
            prev_context = "\nPrevious evaluations (last 3 weeks):\n"
            for m in recent_mem[:3]:
                prev_context += (
                    f"  Week {m['week_start']}: "
                    f"win_rate={float(m.get('win_rate',0))*100:.1f}% "
                    f"pnl=${float(m.get('total_pnl',0)):.2f}\n"
                )

        return (
            f"Evaluation period: {week_start} to {week_end}\n\n"
            f"PERFORMANCE SUMMARY:\n"
            f"  Capital:       ${capital:.2f}\n"
            f"  Tier:          {summary.get('tier', 'seed')}\n"
            f"  Total trades:  {summary.get('total_trades', 0)}\n"
            f"  Win rate:      {float(summary.get('win_rate',0))*100:.1f}%\n"
            f"  Total PnL:     ${float(summary.get('total_pnl',0)):.2f}\n"
            f"  Total fees:    ${float(summary.get('total_fees',0)):.2f}\n"
            f"  Net PnL:       ${float(summary.get('net_pnl',0)):.2f}\n"
            f"  Max drawdown:  {float(summary.get('max_drawdown',0))*100:.1f}%\n"
            f"  Active pairs:  {', '.join(active_pairs)}\n"
            f"  Claude cost:   ${claude_cost:.2f} this month\n"
            f"\nNEWS WEIGHTS (current accuracy):\n"
            + "\n".join(
                f"  {cat}: weight={float(w.weight):.2f} "
                f"acc_1h={float(w.accuracy_1h)*100:.0f}% "
                f"samples={w.sample_size}"
                for cat, w in news_weights.items()
            )
            + prev_context
            + f"\n\nPaper trade mode: {settings.PAPER_TRADE}"
        )

    async def _apply_param_updates(self, updates: dict) -> dict:
        """Apply parameter changes yang Opus rekomendasikan."""
        applied = {}
        for pair_or_global, params in updates.items():
            if not isinstance(params, dict):
                continue
            try:
                # Hanya update nilai numerik yang valid
                safe_params = {
                    k: v for k, v in params.items()
                    if isinstance(v, (int, float)) and v > 0
                }
                if safe_params:
                    await db.update_strategy_params(
                        pair_or_global, safe_params, updated_by="opus"
                    )
                    applied[pair_or_global] = safe_params
                    log.info("Opus auto-updated params for %s: %s",
                             pair_or_global, safe_params)
            except Exception as e:
                log.error("Failed to apply params for %s: %s", pair_or_global, e)
        return applied

    async def _apply_news_weights(self, weights: dict):
        """Update bobot berita berdasarkan akurasi historis."""
        if not weights:
            return
        try:
            await db.update_news_weights(weights)
        except Exception as e:
            log.error("Failed to update news weights: %s", e)

    def _parse(self, raw: str) -> dict:
        try:
            clean = raw.strip().lstrip("```json").rstrip("```").strip()
            return json.loads(clean)
        except Exception as e:
            log.error("Opus parse error: %s | raw=%s", e, raw[:200])
            return {
                "summary":          {},
                "auto_updated":     [],
                "action_required":  [],
                "params_to_update": {},
                "news_weights_update": {},
                "pair_recommendations": [],
            }


opus_brain = OpusBrain()
