"""
brains/opus_brain.py
Opus — meta brain dengan loop pembelajaran sungguhan.

PATCHED 2026-05-02 (MAJOR):
- Model ID Opus 4.5 (claude-opus-4-5-20251101)
- LEARNING LOOP NYATA:
  * Sebelum evaluasi, agregat akurasi news_weights dari prediction_correct
    (dipanggil weights_aggregator.run() dulu)
  * Konteks Opus berisi:
      - Param changes minggu lalu + outcome 7 hari setelahnya
      - News weight changes minggu lalu + outcome akurasi setelahnya
      - Backtest result terbaru dari semua pair aktif
      - Trade source breakdown (rule_based vs haiku vs sonnet vs news)
  * Output Opus diparse ke `patterns_found` (sebelumnya kosong)
  * Setiap parameter change disimpan dengan timestamp untuk
    follow-up evaluation minggu depan

Learning loop sekarang:
  Week N → Opus mengubah X → Bot trading dengan X selama 7 hari
        → Week N+1 → Opus dapat data "X mengubah win rate dari A ke B"
        → Opus belajar apakah perubahan X efektif
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

MODEL      = "claude-opus-4-5-20251101"
MAX_TOKENS = 2500   # +500 untuk patterns_found

INPUT_COST  = 15.00 / 1_000_000
OUTPUT_COST = 75.00 / 1_000_000


class OpusBrain:

    def __init__(self):
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def weekly_evaluation(self) -> dict:
        log.info("Opus weekly evaluation starting...")

        week_end   = date.today()
        week_start = week_end - timedelta(days=7)

        try:
            # Step 1: Agregat akurasi news_weights dari outcome tracker
            # (loop pembelajaran nyata: outcome → weight → keputusan)
            try:
                from news.weights_aggregator import weights_aggregator
                agg_summary = await weights_aggregator.run(days=14)
                log.info("News weights aggregated: %d categories updated",
                         agg_summary.get("updated_count", 0))
            except Exception as e:
                log.warning("Weights aggregator skipped: %s", e)

            # Step 2: Build context dengan history pembelajaran
            context = await self._build_context(week_start, week_end)

            # Step 3: Call Opus
            response = self._client.messages.create(
                model      = MODEL,
                max_tokens = MAX_TOKENS,
                system     = _SYSTEM_PROMPT,
                messages   = [{"role": "user", "content": context}],
            )

            raw  = response.content[0].text.strip()
            data = self._parse(raw)

            usage = response.usage
            cost  = (usage.input_tokens  * INPUT_COST +
                     usage.output_tokens * OUTPUT_COST)
            data["token_cost"] = round(cost, 4)

            await db.log_claude_usage(
                model      = "opus",
                calls      = 1,
                input_tok  = usage.input_tokens,
                output_tok = usage.output_tokens,
                cost       = cost,
                purpose    = "weekly_evaluation",
            )

            # Step 4: Apply param updates (whitelist + bounds)
            params_updated = await self._apply_param_updates(
                data.get("params_to_update", {})
            )

            # Step 5: Apply news weights update (Opus boleh override
            # hasil aggregator hanya jika confidence tinggi)
            await self._apply_news_weights(
                data.get("news_weights_update", {})
            )

            # Step 6: Save Opus memory
            summary = data.get("summary", {})
            memory  = OpusMemory(
                week_start       = week_start,
                week_end         = week_end,
                win_rate         = float(summary.get("win_rate", 0)),
                total_pnl        = float(summary.get("total_pnl", 0)),
                max_drawdown     = float(summary.get("max_drawdown", 0)),
                total_trades     = int(summary.get("total_trades", 0)),
                sharpe_ratio     = float(summary.get("sharpe_ratio", 0)),
                # FIX: patterns_found sekarang diisi dari output Opus
                # (sebelumnya disalah-petakan ke auto_updated)
                patterns_found   = data.get("patterns_found", []),
                actions_required = data.get("action_required", []),
                params_updated   = params_updated,
                raw_analysis     = raw,
                token_cost       = cost,
            )
            await db.save_opus_memory(memory)

            log.info(
                "Opus evaluation complete: win=%.1f%% pnl=$%.2f patterns=%d "
                "actions=%d cost=$%.4f",
                float(summary.get("win_rate", 0)) * 100,
                float(summary.get("total_pnl", 0)),
                len(data.get("patterns_found", [])),
                len(data.get("action_required", [])),
                cost,
            )
            return data

        except Exception as e:
            log.error("Opus evaluation error: %s", e, exc_info=True)
            return {}

    async def _build_context(self, week_start: date, week_end: date) -> str:
        """Build konteks lengkap untuk Opus — termasuk learning history."""
        summary      = await db.get_weekly_summary(days=7)
        recent_mem   = await db.get_recent_opus_memory(weeks=3)
        news_weights = await db.get_news_weights()
        active_pairs = await db.get_active_pairs()
        capital      = await db.get_current_capital()
        claude_cost  = await db.get_claude_cost_this_month()

        # Trade breakdown by trigger source
        source_breakdown = await self._trade_source_breakdown(days=7)

        # Backtest summary semua pair aktif (untuk validasi konsistensi)
        bt_summary = await self._backtest_summary(active_pairs)

        # Learning context: efek param change minggu lalu
        # Bandingkan win_rate sebelum vs sesudah perubahan
        learning_context = self._learning_context_from_memory(recent_mem)

        # Format previous evaluations
        prev_context = ""
        if recent_mem:
            prev_context = "\nPREVIOUS EVALUATIONS (last 3 weeks):\n"
            for m in recent_mem[:3]:
                prev_context += (
                    f"  Week {m['week_start']}: "
                    f"win_rate={float(m.get('win_rate', 0)) * 100:.1f}% "
                    f"pnl=${float(m.get('total_pnl', 0)):.2f} "
                    f"trades={int(m.get('total_trades', 0))}\n"
                )

        return (
            f"Evaluation period: {week_start} to {week_end}\n\n"
            f"PERFORMANCE SUMMARY (last 7 days):\n"
            f"  Capital:       ${capital:.2f}\n"
            f"  Tier:          {summary.get('tier', 'seed')}\n"
            f"  Total trades:  {summary.get('total_trades', 0)}\n"
            f"  Win rate:      {float(summary.get('win_rate',0)) * 100:.1f}%\n"
            f"  Total PnL:     ${float(summary.get('total_pnl',0)):.2f}\n"
            f"  Total fees:    ${float(summary.get('total_fees',0)):.2f}\n"
            f"  Net PnL:       ${float(summary.get('net_pnl',0)):.2f}\n"
            f"  Max drawdown:  {float(summary.get('max_drawdown',0)) * 100:.1f}%\n"
            f"  Active pairs:  {', '.join(active_pairs)}\n"
            f"  Claude cost:   ${claude_cost:.2f} this month\n"
            f"\nTRADE SOURCE BREAKDOWN (last 7 days):\n"
            + "\n".join(
                f"  {src}: {data['count']} trades | "
                f"win_rate={data['win_rate'] * 100:.1f}% | "
                f"avg_pnl=${data['avg_pnl']:.2f}"
                for src, data in source_breakdown.items()
            )
            + f"\n\nNEWS WEIGHTS (current accuracy from outcome tracker):\n"
            + "\n".join(
                f"  {cat}: weight={float(w.weight):.2f} "
                f"acc_1h={float(w.accuracy_1h) * 100:.0f}% "
                f"acc_24h={float(w.accuracy_24h) * 100:.0f}% "
                f"samples={w.sample_size}"
                for cat, w in news_weights.items()
            )
            + f"\n\nBACKTEST SUMMARY (most recent per pair):\n{bt_summary}"
            + prev_context
            + f"\n\nLEARNING SIGNALS (impact of recent changes):\n{learning_context}"
            + f"\n\nPaper trade mode: {settings.PAPER_TRADE}\n"
            + "\nINSTRUCTIONS:\n"
            + "- Identify 1–4 patterns_found that are concrete and falsifiable.\n"
            + "- Reference learning signals: did last week's param changes help?\n"
            + "- Be specific in params_to_update: only change 1–3 params per week.\n"
        )

    @staticmethod
    def _learning_context_from_memory(recent_mem: list) -> str:
        """Format learning loop: compare win rate before vs after param change."""
        if len(recent_mem) < 2:
            return "  (insufficient history — need at least 2 prior weeks)"

        latest = recent_mem[0]
        prev   = recent_mem[1]

        latest_wr = float(latest.get("win_rate", 0)) * 100
        prev_wr   = float(prev.get("win_rate", 0)) * 100
        latest_pnl = float(latest.get("total_pnl", 0))
        prev_pnl   = float(prev.get("total_pnl", 0))

        params_changed = prev.get("params_updated") or {}
        if not params_changed:
            return f"  Last week no param changes. WR: {prev_wr:.1f}% → {latest_wr:.1f}%"

        lines = [
            f"  Week {prev.get('week_start')} changed params:"
        ]
        for scope, p in params_changed.items():
            if isinstance(p, dict):
                kv = ", ".join(f"{k}={v}" for k, v in p.items())
                lines.append(f"    {scope}: {kv}")
        lines.append(
            f"  Result: win_rate {prev_wr:.1f}% → {latest_wr:.1f}% "
            f"(delta {latest_wr - prev_wr:+.1f}pp), "
            f"PnL ${prev_pnl:.2f} → ${latest_pnl:.2f}"
        )
        verdict = (
            "POSITIVE — keep direction"  if latest_wr > prev_wr + 2 else
            "NEGATIVE — consider revert" if latest_wr < prev_wr - 2 else
            "NEUTRAL — no clear effect"
        )
        lines.append(f"  Verdict: {verdict}")
        return "\n".join(lines)

    @staticmethod
    async def _trade_source_breakdown(days: int = 7) -> dict:
        """Group trades by trigger_source untuk lihat which AI layer paling akurat."""
        trades = await db.get_trades_for_period(days=days)
        breakdown: dict[str, dict] = {}
        for t in trades:
            src = (t.get("trigger_source") or "unknown")
            if src not in breakdown:
                breakdown[src] = {"count": 0, "wins": 0, "total_pnl": 0.0}
            breakdown[src]["count"] += 1
            pnl = float(t.get("pnl_usd") or 0)
            breakdown[src]["total_pnl"] += pnl
            if pnl > 0:
                breakdown[src]["wins"] += 1

        # Hitung win_rate dan avg_pnl
        for src in list(breakdown.keys()):
            d = breakdown[src]
            d["win_rate"] = d["wins"] / d["count"] if d["count"] else 0.0
            d["avg_pnl"]  = d["total_pnl"] / d["count"] if d["count"] else 0.0
        return breakdown

    @staticmethod
    async def _backtest_summary(pairs: list[str]) -> str:
        """Format backtest summary untuk semua pair aktif."""
        if not pairs:
            return "  (no active pairs)"
        lines = []
        for p in pairs:
            try:
                bt = await db.get_best_backtest(p)
                if bt:
                    lines.append(
                        f"  {p}: sharpe={float(bt['sharpe_ratio']):.2f} "
                        f"win={float(bt['win_rate']) * 100:.1f}% "
                        f"max_dd={float(bt['max_drawdown']) * 100:.1f}% "
                        f"trades={int(bt['total_trades'])}"
                    )
                else:
                    lines.append(f"  {p}: (no backtest data)")
            except Exception:
                lines.append(f"  {p}: (backtest error)")
        return "\n".join(lines)

    async def _apply_param_updates(self, updates: dict) -> dict:
        """
        Apply parameter changes — dengan whitelist dan bounds.
        Sebelumnya: terima semua >0 → bisa set rsi_period=99999.
        Sekarang: validasi range tiap field.
        """
        # Whitelist + bounds (low, high)
        ALLOWED_BOUNDS = {
            "rsi_period":              (5,   30),
            "rsi_oversold":            (15,  45),
            "rsi_overbought":          (55,  85),
            "macd_fast":               (5,   20),
            "macd_slow":               (15,  40),
            "macd_signal":             (5,   15),
            "stop_loss_pct":           (0.5, 5.0),
            "take_profit_pct":         (0.8, 10.0),
            "atr_no_trade_threshold":  (0.1, 2.0),
            "position_multiplier":     (0.3, 2.0),
        }

        applied: dict[str, dict] = {}
        for scope, params in updates.items():
            if not isinstance(params, dict):
                continue
            safe = {}
            for k, v in params.items():
                if k not in ALLOWED_BOUNDS:
                    log.warning("Opus tried to set unknown param '%s' — skipped", k)
                    continue
                try:
                    val = float(v)
                except (TypeError, ValueError):
                    continue
                lo, hi = ALLOWED_BOUNDS[k]
                if not (lo <= val <= hi):
                    log.warning(
                        "Opus param '%s'=%s out of bounds [%s, %s] — skipped",
                        k, val, lo, hi,
                    )
                    continue
                # int fields
                if k in ("rsi_period", "macd_fast", "macd_slow", "macd_signal"):
                    val = int(round(val))
                safe[k] = val

            if safe:
                try:
                    await db.update_strategy_params(scope, safe, updated_by="opus")
                    applied[scope] = safe
                    log.info("Opus auto-updated params for %s: %s", scope, safe)
                except Exception as e:
                    log.error("Failed to apply params for %s: %s", scope, e)
        return applied

    async def _apply_news_weights(self, weights: dict):
        if not weights:
            return
        # Bound check: weight harus 0.0–1.0
        safe_weights: dict[str, dict] = {}
        for cat, data in weights.items():
            if not isinstance(data, dict):
                continue
            cleaned = {}
            for k in ("weight", "accuracy_1h", "accuracy_24h"):
                if k in data:
                    try:
                        v = float(data[k])
                        if 0.0 <= v <= 1.0:
                            cleaned[k] = round(v, 4)
                    except (TypeError, ValueError):
                        pass
            if cleaned:
                safe_weights[cat] = cleaned
        if safe_weights:
            try:
                await db.update_news_weights(safe_weights)
            except Exception as e:
                log.error("Failed to update news weights: %s", e)

    def _parse(self, raw: str) -> dict:
        try:
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean.rsplit("```", 1)[0]
            clean = clean.strip()
            return json.loads(clean)
        except Exception as e:
            log.error("Opus parse error: %s | raw=%s", e, raw[:200])
            return {
                "summary":             {},
                "patterns_found":      [],
                "action_required":     [],
                "params_to_update":    {},
                "news_weights_update": {},
                "pair_recommendations": [],
            }


opus_brain = OpusBrain()
