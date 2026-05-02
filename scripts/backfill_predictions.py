"""
scripts/backfill_predictions.py
Repair script untuk data lama: hitung prediction_correct dari news_items
yang sudah punya price_1h_after + price_24h_after tapi prediction_correct
masih NULL.

Jalankan SEKALI dari Render Shell setelah deploy versi baru:
    python -m scripts.backfill_predictions

Akan:
1. Ambil semua news_items dengan prediction_correct IS NULL
2. Untuk tiap baris, panggil _check_prediction() dengan logic yang sama
   dengan outcome_tracker (pakai price_1h_after sebagai fallback baseline
   kalau price_at_news kosong)
3. Update prediction_correct kalau bisa di-evaluasi

Setelah ini selesai, weights_aggregator akan punya data input untuk
update news_weights mingguan.
"""

from __future__ import annotations
import asyncio
import logging
import sys
import os

# Path setup — supaya bisa di-jalankan dari root project
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.client import db
from news.outcome_tracker import outcome_tracker

log = logging.getLogger("backfill_predictions")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")


async def main():
    print("=" * 60)
    print("Backfill prediction_correct for legacy news_items")
    print("=" * 60)

    # Ambil semua baris yang belum punya prediction_correct
    # tapi sudah punya price_24h_after
    res = (
        db._get()
        .table("news_items")
        .select("id, pairs_mentioned, haiku_sentiment, sonnet_action, "
                "price_at_news, price_1h_after, price_24h_after, "
                "prediction_correct")
        .is_("prediction_correct", "null")
        .execute()
    )

    rows = res.data or []
    print(f"\nTotal candidate rows: {len(rows)}")

    if not rows:
        print("Nothing to backfill.")
        return

    stats = {
        "evaluated_correct":   0,
        "evaluated_incorrect": 0,
        "skipped_no_24h":      0,
        "skipped_neutral":     0,
        "skipped_no_baseline": 0,
        "errors":              0,
    }

    for i, row in enumerate(rows):
        if i > 0 and i % 100 == 0:
            print(f"  Progress: {i}/{len(rows)}")

        try:
            # Skip kalau tidak ada price_24h_after — tidak bisa dievaluasi
            price_24h = row.get("price_24h_after")
            if not price_24h or price_24h == {}:
                stats["skipped_no_24h"] += 1
                continue

            # Skip kalau sentimen neutral
            sentiment = float(row.get("haiku_sentiment") or 0)
            if -0.3 <= sentiment <= 0.3:
                stats["skipped_neutral"] += 1
                continue

            # Cek baseline tersedia
            baseline = row.get("price_at_news") or {}
            if not baseline or baseline == {}:
                baseline = row.get("price_1h_after") or {}
            if not baseline or baseline == {}:
                stats["skipped_no_baseline"] += 1
                continue

            # Hitung prediction_correct pakai logic outcome_tracker
            result = outcome_tracker._check_prediction(row, price_24h)

            if result is None:
                stats["skipped_neutral"] += 1
                continue

            # Update DB
            db._get().table("news_items").update({
                "prediction_correct": result
            }).eq("id", row["id"]).execute()

            if result:
                stats["evaluated_correct"] += 1
            else:
                stats["evaluated_incorrect"] += 1

        except Exception as e:
            stats["errors"] += 1
            print(f"  Error on {row.get('id', 'unknown')[:8]}: {e}")

    print("\n" + "=" * 60)
    print("BACKFILL COMPLETE")
    print("=" * 60)
    total_evaluated = stats["evaluated_correct"] + stats["evaluated_incorrect"]
    if total_evaluated > 0:
        accuracy = stats["evaluated_correct"] / total_evaluated * 100
    else:
        accuracy = 0
    print(f"  Evaluated correct:   {stats['evaluated_correct']}")
    print(f"  Evaluated incorrect: {stats['evaluated_incorrect']}")
    print(f"  Overall accuracy:    {accuracy:.1f}%")
    print(f"  Skipped (no 24h):    {stats['skipped_no_24h']}")
    print(f"  Skipped (neutral):   {stats['skipped_neutral']}")
    print(f"  Skipped (no base):   {stats['skipped_no_baseline']}")
    print(f"  Errors:              {stats['errors']}")
    print()
    print("Next step: trigger weights aggregator untuk update news_weights:")
    print("  python -m scripts.run_weights_aggregator")
    print()


if __name__ == "__main__":
    asyncio.run(main())
