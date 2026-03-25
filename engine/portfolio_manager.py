"""
engine/portfolio_manager.py
Diversifikasi portfolio, cek korelasi antar pair,
dan scoring LRHR (Low Risk High Return).
"""

from __future__ import annotations
import logging
import json
from pathlib import Path

from config.settings import settings
from database.client import db

log = logging.getLogger("portfolio_manager")

_pairs_config_path = Path(__file__).parent.parent / "config" / "pairs.json"
_params_config_path = Path(__file__).parent.parent / "config" / "strategy_params.json"


class PortfolioManager:

    def _load_pairs_config(self) -> dict:
        with open(_pairs_config_path) as f:
            return json.load(f)

    def _load_lrhr_weights(self) -> dict:
        with open(_params_config_path) as f:
            data = json.load(f)
        return data.get("lrhr_weights", {
            "sharpe": 0.25, "winrate": 0.20,
            "drawdown": 0.20, "news": 0.20, "onchain": 0.15,
        })

    async def get_candidate_pairs(self, capital: float) -> list[str]:
        """Pair yang memenuhi syarat modal minimum tapi belum aktif."""
        cfg = self._load_pairs_config()
        return [
            p["pair"] for p in cfg["pairs"]
            if not p["active"] and capital >= p["min_capital_required"]
        ]

    async def calc_lrhr_score(self, pair: str, news_score: float = 0.5,
                               onchain_score: float = 0.5) -> float:
        """
        Hitung skor LRHR untuk satu pair.
        Gabungan data historis + sentimen berita + on-chain.
        """
        weights   = self._load_lrhr_weights()
        best_bt   = await db.get_best_backtest(pair)
        win_rate  = await db.get_win_rate(days=30)

        sharpe   = float(best_bt["sharpe_ratio"])  if best_bt else 0.5
        max_dd   = float(best_bt["max_drawdown"])  if best_bt else 0.15
        win_r    = float(best_bt["win_rate"])       if best_bt else win_rate

        # Normalisasi
        sharpe_norm  = min(sharpe / 2.0, 1.0)
        winrate_norm = max((win_r - 0.4) / 0.4, 0.0)
        dd_norm      = max(1 - max_dd / 0.20, 0.0)
        news_norm    = float(news_score)
        onchain_norm = float(onchain_score)

        score = (
            sharpe_norm  * weights.get("sharpe",   0.25) +
            winrate_norm * weights.get("winrate",  0.20) +
            dd_norm      * weights.get("drawdown", 0.20) +
            news_norm    * weights.get("news",     0.20) +
            onchain_norm * weights.get("onchain",  0.15)
        )
        return round(score, 3)

    async def check_category_caps(self, capital: float) -> dict[str, float]:
        """
        Hitung berapa USD yang boleh dialokasikan per kategori.
        Layer1 maks 60%, DeFi maks 20%, dll.
        """
        cfg  = self._load_pairs_config()
        caps = cfg.get("category_caps", {})
        return {cat: capital * pct for cat, pct in caps.items()}

    async def suggest_next_pair(self, capital: float) -> str | None:
        """
        Sarankan pair berikutnya yang paling worth ditambah.
        Dipanggil saat modal naik tier.
        """
        candidates = await self.get_candidate_pairs(capital)
        if not candidates:
            return None

        scores = []
        for pair in candidates:
            score = await self.calc_lrhr_score(pair)
            scores.append((pair, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        best_pair, best_score = scores[0]

        if best_score >= 0.55:
            log.info("Suggested next pair: %s (score=%.3f)", best_pair, best_score)
            return best_pair

        log.info("No pair passed LRHR threshold (best=%s score=%.3f)",
                 best_pair, best_score)
        return None

    async def get_portfolio_health(self, capital: float) -> dict:
        """Ringkasan kesehatan portfolio untuk Opus weekly report."""
        active_pairs  = await db.get_active_pairs()
        cat_caps      = await self.check_category_caps(capital)
        next_pair     = await self.suggest_next_pair(capital)

        return {
            "active_pairs":  active_pairs,
            "pair_count":    len(active_pairs),
            "category_caps": cat_caps,
            "next_candidate": next_pair,
            "diversification_score": min(len(active_pairs) * 2, 10),
        }


portfolio_manager = PortfolioManager()
