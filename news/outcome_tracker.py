"""
news/outcome_tracker.py
Catat harga 1h dan 24h setelah berita masuk.
Data ini yang dipakai Opus untuk belajar akurasi prediksi per kategori.
Dijalankan dari scheduler harian.
"""

from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone

from database.client import db
from exchange.bybit_client import bybit

log = logging.getLogger("outcome_tracker")


class OutcomeTracker:

    async def update_pending_outcomes(self):
        """
        Cari berita yang belum punya price_1h_after atau price_24h_after,
        lalu isi sekarang kalau sudah waktunya.
        Dipanggil dari scheduler setiap jam.
        """
        try:
            # Ambil berita 48 jam terakhir yang belum punya outcome lengkap
            res = (
                db._get()
                .table("news_items")
                .select("id, pairs_mentioned, published_at, "
                        "price_at_news, price_1h_after, price_24h_after, "
                        "haiku_sentiment, sonnet_action")
                .gte("published_at", (
                    datetime.now(timezone.utc) - timedelta(hours=48)
                ).isoformat())
                .execute()
            )

            if not res.data:
                return

            now = datetime.now(timezone.utc)

            for item in res.data:
                await self._update_item(item, now)

        except Exception as e:
            log.error("Outcome tracker error: %s", e)

    async def _update_item(self, item: dict, now: datetime):
        """Update satu berita dengan harga outcome."""
        try:
            pub_str = item.get("published_at", "")
            if not pub_str:
                return

            from dateutil import parser as dtparse
            pub_dt = dtparse.parse(pub_str)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)

            pairs = item.get("pairs_mentioned", [])
            if not pairs:
                return

            elapsed = (now - pub_dt).total_seconds()

            updates = {}

            # Isi price_1h_after setelah 1 jam
            if elapsed >= 3600 and not item.get("price_1h_after"):
                prices = {}
                for pair in pairs:
                    try:
                        prices[pair] = await bybit.get_price(pair)
                    except Exception:
                        pass
                if prices:
                    updates["price_1h_after"] = prices

            # Isi price_24h_after setelah 24 jam
            if elapsed >= 86400 and not item.get("price_24h_after"):
                prices = {}
                for pair in pairs:
                    try:
                        prices[pair] = await bybit.get_price(pair)
                    except Exception:
                        pass
                if prices:
                    updates["price_24h_after"] = prices

                    # Hitung apakah prediksi benar
                    correct = self._check_prediction(item, prices)
                    updates["prediction_correct"] = correct

            if updates:
                db._get().table("news_items").update(updates).eq(
                    "id", item["id"]
                ).execute()
                log.debug("Outcome updated for news %s", item["id"][:8])

        except Exception as e:
            log.debug("Outcome update error: %s", e)

    def _check_prediction(self, item: dict, prices_24h: dict) -> bool | None:
        """
        Cek apakah prediksi Sonnet terbukti benar setelah 24 jam.
        Bandingkan harga saat berita dengan harga 24 jam kemudian.
        """
        price_at   = item.get("price_at_news", {})
        action     = item.get("sonnet_action")
        sentiment  = float(item.get("haiku_sentiment") or 0)

        if not price_at or not prices_24h:
            return None

        # Cek setiap pair
        correct_count = 0
        total_count   = 0

        for pair, price_now in prices_24h.items():
            price_then = price_at.get(pair)
            if not price_then:
                continue

            price_then = float(price_then)
            price_now  = float(price_now)
            change_pct = (price_now - price_then) / price_then

            total_count += 1

            # Prediksi bullish (sentiment > 0.3) → harga naik
            if sentiment > 0.3 and change_pct > 0.01:
                correct_count += 1
            # Prediksi bearish (sentiment < -0.3) → harga turun
            elif sentiment < -0.3 and change_pct < -0.01:
                correct_count += 1
            # Neutral — tidak dihitung
            elif -0.3 <= sentiment <= 0.3:
                return None

        if total_count == 0:
            return None

        return correct_count / total_count >= 0.5


outcome_tracker = OutcomeTracker()
