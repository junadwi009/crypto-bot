"""
news/outcome_tracker.py
Catat harga 1h dan 24h setelah berita masuk + hitung prediction_correct.

PATCHED 2026-05-02 (revisi 3):
- BUG FIX (CRITICAL): _check_prediction sebelumnya return None langsung
  kalau price_at_news = {} (line 115 lama). Akibatnya 100% data lama
  punya prediction_correct = NULL → learning loop tidak punya data input.
- FALLBACK BASELINE: kalau price_at_news kosong (data lama atau gagal
  capture), pakai price_1h_after sebagai baseline approximation.
  Akurasi turun (kita compare 1h-vs-24h bukan 0h-vs-24h), tapi data
  ada vs tidak ada. Lebih baik approximate daripada NULL.
- Logic neutral sentiment dipindahkan ke awal supaya tidak return None
  prematurely di tengah loop pair.
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
        Cari berita yang belum punya outcome, isi sekarang kalau cukup waktu.
        Dipanggil scheduler tiap jam.
        """
        try:
            res = (
                db._get()
                .table("news_items")
                .select("id, pairs_mentioned, published_at, "
                        "price_at_news, price_1h_after, price_24h_after, "
                        "haiku_sentiment, sonnet_action, prediction_correct")
                .gte("published_at", (
                    datetime.now(timezone.utc) - timedelta(hours=72)
                ).isoformat())
                .execute()
            )

            if not res.data:
                return

            now = datetime.now(timezone.utc)
            updated_count = 0
            for item in res.data:
                if await self._update_item(item, now):
                    updated_count += 1
            if updated_count > 0:
                log.info("Outcome tracker updated %d news items", updated_count)

        except Exception as e:
            log.error("Outcome tracker error: %s", e)

    async def _update_item(self, item: dict, now: datetime) -> bool:
        """Update satu berita. Return True kalau ada perubahan."""
        try:
            pub_str = item.get("published_at", "")
            if not pub_str:
                return False

            from dateutil import parser as dtparse
            pub_dt = dtparse.parse(pub_str)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)

            pairs = item.get("pairs_mentioned", [])
            if not pairs:
                return False

            elapsed = (now - pub_dt).total_seconds()
            updates: dict = {}

            # Isi price_1h_after setelah 1 jam
            existing_1h = item.get("price_1h_after")
            if elapsed >= 3600 and (not existing_1h or existing_1h == {}):
                prices = await self._fetch_prices(pairs)
                if prices:
                    updates["price_1h_after"] = prices

            # Isi price_24h_after setelah 24 jam
            existing_24h = item.get("price_24h_after")
            if elapsed >= 86400 and (not existing_24h or existing_24h == {}):
                prices_24 = await self._fetch_prices(pairs)
                if prices_24:
                    updates["price_24h_after"] = prices_24

            # Hitung prediction_correct kalau:
            #   - belum di-set, DAN
            #   - sudah ada price_24h_after (existing atau yang baru di-update)
            already_has_pred = item.get("prediction_correct") is not None
            price_24h = updates.get("price_24h_after") or existing_24h
            if not already_has_pred and price_24h and price_24h != {}:
                correct = self._check_prediction(item, price_24h)
                if correct is not None:  # None = neutral, tidak dievaluasi
                    updates["prediction_correct"] = correct

            if updates:
                db._get().table("news_items").update(updates).eq(
                    "id", item["id"]
                ).execute()
                return True
            return False

        except Exception as e:
            log.debug("Outcome update error: %s", e)
            return False

    async def _fetch_prices(self, pairs: list[str]) -> dict:
        """Fetch harga sekarang untuk semua pair, dengan fallback kline."""
        prices: dict[str, float] = {}
        for pair in pairs:
            # Try live ticker
            try:
                p = await bybit.get_price(pair)
                if p > 0:
                    prices[pair] = p
                    continue
            except Exception:
                pass
            # Fallback kline
            try:
                candles = await bybit.get_ohlcv(pair, interval="1", limit=1)
                if candles:
                    last = float(candles[-1].get("close", 0))
                    if last > 0:
                        prices[pair] = last
            except Exception as e:
                log.debug("Failed to fetch price for %s: %s", pair, e)
        return prices

    def _check_prediction(self, item: dict, prices_24h: dict) -> bool | None:
        """
        Hitung apakah prediksi sentimen searah dengan pergerakan harga 24h.

        Baseline price priority:
          1. price_at_news (ideal — diambil saat berita masuk)
          2. price_1h_after (fallback — kalau baseline kosong, approximation)

        Return:
          True  = prediksi benar (sentimen searah dengan pergerakan)
          False = prediksi salah (lawan arah)
          None  = tidak bisa dievaluasi (sentimen neutral, atau tidak ada baseline)
        """
        sentiment = float(item.get("haiku_sentiment") or 0)

        # Neutral sentiment — tidak ada arah untuk diverifikasi
        if -0.3 <= sentiment <= 0.3:
            return None

        # Pilih baseline
        baseline = item.get("price_at_news") or {}
        baseline_source = "price_at_news"

        if not baseline or baseline == {}:
            # Fallback: pakai price_1h_after sebagai baseline
            baseline = item.get("price_1h_after") or {}
            baseline_source = "price_1h_after_fallback"

        if not baseline or baseline == {}:
            return None  # Benar-benar tidak ada baseline

        if not prices_24h:
            return None

        correct_count = 0
        total_count   = 0

        for pair, price_24 in prices_24h.items():
            price_then = baseline.get(pair)
            if not price_then:
                continue

            try:
                pt = float(price_then)
                pn = float(price_24)
                if pt <= 0:
                    continue
                change_pct = (pn - pt) / pt
            except (TypeError, ValueError):
                continue

            total_count += 1
            # Threshold 1% — abaikan noise market kecil
            if sentiment > 0.3 and change_pct > 0.01:
                correct_count += 1
            elif sentiment < -0.3 and change_pct < -0.01:
                correct_count += 1

        if total_count == 0:
            return None

        result = correct_count / total_count >= 0.5
        log.debug(
            "Prediction check (%s): sentiment=%.2f, baseline_src=%s, "
            "correct=%d/%d → %s",
            (item.get("id") or "")[:8], sentiment, baseline_source,
            correct_count, total_count, result
        )
        return result


outcome_tracker = OutcomeTracker()