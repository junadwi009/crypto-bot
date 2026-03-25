"""
utils/redis_client.py
Koneksi ke Upstash Redis.
Semua state ephemeral (session, rate limit, flag) disimpan di sini.
"""

import logging
from redis.asyncio import Redis as AsyncRedis
from config.settings import settings

log = logging.getLogger("redis")


class RedisClient:
    def __init__(self):
        self._client: AsyncRedis | None = None

    def _get(self) -> AsyncRedis:
        if self._client is None:
            self._client = AsyncRedis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
            )
        return self._client

    async def ping(self):
        client = self._get()
        await client.ping()
        log.info("Redis: ping OK")

    async def get(self, key: str) -> str | None:
        return await self._get().get(key)

    async def set(self, key: str, value: str):
        await self._get().set(key, value)

    async def setex(self, key: str, ttl: int, value: str):
        await self._get().setex(key, ttl, value)

    async def delete(self, key: str):
        await self._get().delete(key)

    async def incr(self, key: str) -> int:
        return await self._get().incr(key)

    async def expire(self, key: str, ttl: int):
        await self._get().expire(key, ttl)

    async def ttl(self, key: str) -> int:
        return await self._get().ttl(key)

    async def incrbyfloat(self, key: str, amount: float):
        await self._get().incrbyfloat(key, amount)


redis = RedisClient()
