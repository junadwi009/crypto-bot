"""
news/fetcher.py
Ambil berita dari RSS feeds + CryptoPanic.
Deduplicate, filter by portfolio pairs, sanitasi, lalu kirim ke analyzer.
Dijalankan setiap 15 menit dari main.py.
"""

from __future__ import annotations
import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from config.settings import settings
from database.client import db
from news.sanitizer import sanitize_news_item
from utils.redis_client import redis

log = logging.getLogger("news_fetcher")

# RSS sources — semua gratis, tidak butuh API key
RSS_SOURCES = {
    "coindesk":       "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph":  "https://cointelegraph.com/rss",
    "decrypt":        "https://decrypt.co/feed",
    "beincrypto":     "https://beincrypto.com/feed/",
    "reddit_crypto":  "https://www.reddit.com/r/CryptoCurrency/.rss",
    "reddit_bitcoin": "https://www.reddit.com/r/Bitcoin/.rss",
    "google_crypto":  "https://news.google.com/rss/search?q=cryptocurrency+bitcoin&hl=en&gl=US&ceid=US:en",
}

# Keyword mapping: pair → keywords untuk deteksi
PAIR_KEYWORDS = {
    "BTC/USDT":  ["bitcoin", "btc", "satoshi", "lightning network"],
    "ETH/USDT":  ["ethereum", "eth", "ether", "vitalik", "eip"],
    "SOL/USDT":  ["solana", "sol"],
    "BNB/USDT":  ["binance", "bnb", "bsc", "bnb chain"],
    "AVAX/USDT": ["avalanche", "avax"],
    "UNI/USDT":  ["uniswap", "uni"],
    "LINK/USDT": ["chainlink", "link"],
    "ARB/USDT":  ["arbitrum", "arb"],
}

# Kategori berita
NEWS_CATEGORIES = {
    "regulatory": ["sec", "cftc", "regulation", "ban", "lawsuit", "legal", "approved"],
    "adoption":   ["etf", "institution", "treasury", "microstrategy", "adoption", "investment"],
    "hack_exploit": ["hack", "exploit", "breach", "stolen", "vulnerability", "attack"],
    "partnership": ["partnership", "collaboration", "integration", "announce"],
    "upgrade":    ["upgrade", "hard fork", "soft fork", "eip", "proposal", "mainnet"],
    "influencer": ["elon", "musk", "trump", "tweet", "post"],
    "macro":      ["fed", "interest rate", "inflation", "dollar", "economy", "fomc"],
    "whale":      ["whale", "large transaction", "moved", "exchange inflow", "outflow"],
}

# Cache TTL untuk deduplicate (24 jam)
_SEEN_TTL = 86400


class NewsFetcher:

    def __init__(self):
        self._http = httpx.AsyncClient(
            timeout = httpx.Timeout(10.0),
            headers = {"User-Agent": "Mozilla/5.0 (compatible; CryptoBot/1.0)"},
        )

    async def run(self):
        """
        Ambil berita dari semua sumber, proses, simpan ke DB.
        Entry point dipanggil dari main.py setiap 15 menit.
        """
        active_pairs = await db.get_active_pairs()
        if not active_pairs:
            return

        log.info("News fetcher running for pairs: %s", active_pairs)

        all_items = []

        # Fetch semua RSS secara paralel
        rss_results = await asyncio.gather(
            *[self._fetch_rss(name, url) for name, url in RSS_SOURCES.items()],
            return_exceptions=True,
        )
        for result in rss_results:
            if isinstance(result, list):
                all_items.extend(result)

        # Fetch CryptoPanic jika API key ada
        crypto_panic_key = await redis.get("cryptopanic_api_key") or ""
        if crypto_panic_key:
            cp_items = await self._fetch_cryptopanic(
                crypto_panic_key, active_pairs
            )
            all_items.extend(cp_items)

        # Filter, deduplicate, sanitasi
        new_items = []
        for item in all_items:
            # Deduplicate via hash headline
            h = hashlib.md5(item["headline"].lower().encode()).hexdigest()
            if await redis.get(f"news_seen:{h}"):
                continue
            await redis.setex(f"news_seen:{h}", _SEEN_TTL, "1")

            # Sanitasi
            item = sanitize_news_item(item)
            if item["injection_detected"]:
                continue

            # Detect pairs yang disebut
            item["pairs_mentioned"] = self._detect_pairs(
                item["headline"], active_pairs
            )

            # Detect kategori
            item["news_category"] = self._detect_category(item["headline"])

            new_items.append(item)

        if not new_items:
            log.debug("No new news items")
            return

        log.info("Fetched %d new news items", len(new_items))

        # Kirim ke analyzer untuk diproses Claude
        from news.analyzer import news_analyzer
        await news_analyzer.process_batch(new_items)

    async def _fetch_rss(self, source: str, url: str) -> list[dict]:
        """Ambil dan parse RSS feed."""
        try:
            resp = await self._http.get(url)
            feed = feedparser.parse(resp.text)
            items = []
            for entry in feed.entries[:10]:  # 10 terbaru per sumber
                headline = entry.get("title", "").strip()
                if not headline:
                    continue

                # Parse published date
                published = datetime.now(timezone.utc)
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    import time
                    published = datetime.fromtimestamp(
                        time.mktime(entry.published_parsed), tz=timezone.utc
                    )

                # Lewati berita > 24 jam
                if (datetime.now(timezone.utc) - published).total_seconds() > 86400:
                    continue

                items.append({
                    "headline":     headline,
                    "summary":      entry.get("summary", "")[:500],
                    "url":          entry.get("link", ""),
                    "source":       source,
                    "published_at": published.isoformat(),
                })
            return items

        except Exception as e:
            log.debug("RSS error %s: %s", source, e)
            return []

    async def _fetch_cryptopanic(self, api_key: str,
                                  pairs: list[str]) -> list[dict]:
        """Ambil berita dari CryptoPanic dengan community sentiment."""
        try:
            coins = ",".join(p.split("/")[0] for p in pairs)
            resp  = await self._http.get(
                "https://cryptopanic.com/api/v1/posts/",
                params={
                    "auth_token": api_key,
                    "currencies": coins,
                    "filter":     "hot",
                    "public":     "true",
                },
            )
            data = resp.json()
            items = []
            for post in data.get("results", []):
                headline = post.get("title", "").strip()
                if not headline:
                    continue

                votes = post.get("votes", {})
                community_sentiment = (
                    float(votes.get("positive", 0)) -
                    float(votes.get("negative", 0))
                ) / max(float(votes.get("positive", 0)) +
                        float(votes.get("negative", 0)), 1)

                items.append({
                    "headline":            headline,
                    "summary":             "",
                    "url":                 post.get("url", ""),
                    "source":              "cryptopanic",
                    "published_at":        post.get("published_at",
                                           datetime.now(timezone.utc).isoformat()),
                    "community_sentiment": round(community_sentiment, 3),
                })
            return items

        except Exception as e:
            log.debug("CryptoPanic error: %s", e)
            return []

    @staticmethod
    def _detect_pairs(headline: str, active_pairs: list[str]) -> list[str]:
        """Deteksi pair yang disebutkan dalam headline."""
        headline_lower = headline.lower()
        found = []
        for pair in active_pairs:
            keywords = PAIR_KEYWORDS.get(pair, [pair.split("/")[0].lower()])
            if any(kw in headline_lower for kw in keywords):
                found.append(pair)
        return found

    @staticmethod
    def _detect_category(headline: str) -> str:
        """Klasifikasi kategori berita."""
        headline_lower = headline.lower()
        for category, keywords in NEWS_CATEGORIES.items():
            if any(kw in headline_lower for kw in keywords):
                return category
        return "general"

    async def close(self):
        await self._http.aclose()


news_fetcher = NewsFetcher()
