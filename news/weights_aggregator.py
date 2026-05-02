"""
news/weights_aggregator.py
Loop pembelajaran nyata untuk news weights.

Sebelum patch ini: news_weights di-set 1× di seed schema dan tidak pernah
update otomatis. Outcome tracker menulis prediction_correct tapi tidak
ada yang menghitung ulang accuracy_1h/24h dari data tersebut.

Sekarang:
  1. Baca semua news_items 14 hari terakhir yang sudah punya outcome
  2. Group by category (pakai keyword matching seperti di fetcher)
  3. Hitung accuracy_1h, accuracy_24h, sample_size per kategori
  4. Update news_weights:
       - accuracy_* selalu di-update (data agregat)
       - weight diadjust dengan exponential smoothing toward accuracy_24h
         (alpha=0.2 — pelan biar tidak liar)
  5. Return summary supaya bisa di-log ke event log

Dijalankan di awal Opus weekly evaluation, sebelum Opus baca news_weights.
"""

from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from database.client import db

log = logging.getLogger("weights_aggregator")

# Keyword mapping — sinkron dengan news/fetcher.py
NEWS_CATEGORIES = {
    "regulatory":   ["sec", "cftc", "regulation", "ban", "lawsuit", "legal", "approved"],
    "adoption":     ["etf", "institution", "treasury", "microstrategy", "adoption", "investment"],
    "hack_exploit": ["hack", "exploit", "breach", "stolen", "vulnerability", "attack"],
    "partnership":  ["partnership", "collaboration", "integration", "announce"],
    "upgrade":      ["upgrade", "hard fork", "soft fork", "eip", "proposal", "mainnet"],
    "influencer":   ["elon", "musk", "trump", "tweet", "post"],
    "macro":        ["fed", "interest rate", "inflation", "dollar", "economy", "fomc"],
    "whale":        ["whale", "large transaction", "moved", "exchange inflow", "outflow"],
}

# Smoothing factor — berapa aggressive weight di-update
SMOOTHING_ALPHA = 0.2

# Min sample untuk dianggap valid (kurang dari ini, weight tidak berubah)
MIN_SAMPLES_FOR_UPDATE = 5


def _detect_category(headline: str) -> str:
    h = (headline or "").lower()
    for cat, keywords in NEWS_CATEGORIES.items():
        if any(kw in h for kw in keywords):
            return cat
    return "general"


def _check_directional_correctness(item: dict, prices_after: dict,
                                   horizon_label: str) -> bool | None:
    """
    Cek apakah prediksi searah (sentiment) match dengan price movement.
    Return True/False/None (None = tidak bisa dievaluasi karena neutral / no data).
    """
    price_at = item.get("price_at_news") or {}
    if not price_at or not prices_after:
        return None

    sentiment = float(item.get("haiku_sentiment") or 0)

    # Neutral sentiment → tidak dievaluasi (bukan benar/salah)
    if -0.3 <= sentiment <= 0.3:
        return None

    correct = 0
    total   = 0
    for pair, price_now in prices_after.items():
        price_then = price_at.get(pair)
        if not price_then:
            continue
        try:
            pn = float(price_now)
            pt = float(price_then)
            if pt <= 0:
                continue
            change_pct = (pn - pt) / pt
        except (TypeError, ValueError):
            continue

        total += 1
        # Threshold 1% biar noise market kecil tidak dihitung
        if sentiment > 0.3 and change_pct > 0.01:
            correct += 1
        elif sentiment < -0.3 and change_pct < -0.01:
            correct += 1

    if total == 0:
        return None
    return correct / total >= 0.5


class WeightsAggregator:

    async def run(self, days: int = 14) -> dict:
        """
        Agregat akurasi dari news_items dan update news_weights.
        Return summary untuk logging.
        """
        try:
            since_iso = (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).isoformat()

            res = (
                db._get()
                .table("news_items")
                .select("headline, haiku_sentiment, sonnet_action, "
                        "price_at_news, price_1h_after, price_24h_after, "
                        "prediction_correct")
                .gte("published_at", since_iso)
                .execute()
            )
            news_items = res.data or []

            if not news_items:
                log.info("No news items in last %d days — skip aggregation", days)
                return {"updated_count": 0, "categories_seen": 0}

            # Group by detected category, hitung correctness 1h dan 24h
            stats: Dict[str, Dict[str, int]] = {}
            for item in news_items:
                cat = _detect_category(item.get("headline", ""))
                if cat == "general":
                    continue  # Skip kategori general — tidak ada di news_weights
                if cat not in stats:
                    stats[cat] = {
                        "total":      0,
                        "correct_1h": 0, "evaluated_1h": 0,
                        "correct_24h": 0, "evaluated_24h": 0,
                    }
                stats[cat]["total"] += 1

                # Cek 1h correctness
                p1h = item.get("price_1h_after") or {}
                if p1h:
                    res1 = _check_directional_correctness(item, p1h, "1h")
                    if res1 is not None:
                        stats[cat]["evaluated_1h"] += 1
                        if res1:
                            stats[cat]["correct_1h"] += 1

                # Cek 24h correctness
                p24 = item.get("price_24h_after") or {}
                if p24:
                    res24 = _check_directional_correctness(item, p24, "24h")
                    if res24 is not None:
                        stats[cat]["evaluated_24h"] += 1
                        if res24:
                            stats[cat]["correct_24h"] += 1

            # Ambil weights existing untuk smoothing
            existing = await db.get_news_weights()

            # Build update payload
            updates: Dict[str, dict] = {}
            updated_count = 0
            for cat, s in stats.items():
                if s["total"] == 0:
                    continue

                acc_1h  = (s["correct_1h"]  / s["evaluated_1h"])  if s["evaluated_1h"]  > 0 else 0.0
                acc_24h = (s["correct_24h"] / s["evaluated_24h"]) if s["evaluated_24h"] > 0 else 0.0

                payload = {
                    "accuracy_1h":  round(acc_1h,  4),
                    "accuracy_24h": round(acc_24h, 4),
                    "sample_size":  s["total"],
                }

                # Update weight dengan exponential smoothing kalau cukup sample
                if s["evaluated_24h"] >= MIN_SAMPLES_FOR_UPDATE:
                    old_weight = float(existing[cat].weight) if cat in existing else 0.5
                    # Target weight = accuracy_24h, smoothed
                    new_weight = (1 - SMOOTHING_ALPHA) * old_weight + SMOOTHING_ALPHA * acc_24h
                    payload["weight"] = round(max(0.0, min(1.0, new_weight)), 4)
                    log.info(
                        "Category %s: weight %.3f → %.3f (acc_24h=%.1f%% n=%d)",
                        cat, old_weight, new_weight, acc_24h * 100,
                        s["evaluated_24h"],
                    )

                updates[cat] = payload
                updated_count += 1

            if updates:
                await db.update_news_weights(updates)

            return {
                "updated_count":   updated_count,
                "categories_seen": len(stats),
                "items_analyzed":  len(news_items),
            }

        except Exception as e:
            log.error("Weights aggregator error: %s", e, exc_info=True)
            return {"updated_count": 0, "error": str(e)[:100]}


weights_aggregator = WeightsAggregator()
