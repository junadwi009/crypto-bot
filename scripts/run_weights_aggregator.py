"""
scripts/run_weights_aggregator.py
Trigger weights aggregator manual. Berguna setelah backfill_predictions
untuk recompute news_weights tanpa tunggu cron 02:00 WIB.

Jalankan dari Render Shell:
    python -m scripts.run_weights_aggregator
"""

from __future__ import annotations
import asyncio
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from news.weights_aggregator import weights_aggregator

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


async def main():
    print("Running weights aggregator...")
    result = await weights_aggregator.run(days=30)  # 30 hari biar dapat banyak sample
    print(f"\nResult: {result}")
    print()
    print("Cek news_weights di Supabase — accuracy_24h harus terupdate.")


if __name__ == "__main__":
    asyncio.run(main())
