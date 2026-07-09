from __future__ import annotations

import hashlib
import json
import logging
import os
from functools import cache
from typing import Any, Mapping, Protocol

from redis import Redis
from redis.exceptions import RedisError

LOGGER = logging.getLogger(__name__)
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"
DEFAULT_TTL_SECONDS = 86_400


class LLMResponseCache(Protocol):
    """Storage contract used by retrieval-augmented LLM calls."""

    def get(self, query: Mapping[str, Any]) -> str | None: ...

    def set(self, query: Mapping[str, Any], response: str) -> None: ...


class RedisLLMCache:
    """Redis cache keyed by a SHA-256 digest of canonical query JSON.

    Redis failures are treated as cache misses so an unavailable cache never
    prevents the underlying LLM workflow from completing.
    """

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        ttl_seconds: int | None = None,
        namespace: str = "physician-assistant:llm:v1",
        client: Redis | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds or _cache_ttl_seconds()
        if self.ttl_seconds <= 0:
            raise ValueError("Cache TTL must be greater than zero")
        self.namespace = namespace
        self.client = client or Redis.from_url(
            redis_url or os.getenv("REDIS_URL", DEFAULT_REDIS_URL),
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )

    def get(self, query: Mapping[str, Any]) -> str | None:
        try:
            value = self.client.get(self._key(query))
        except RedisError as exc:
            LOGGER.warning("Redis cache lookup failed; continuing without cache: %s", exc)
            return None
        if not isinstance(value, str):
            return None
        try:
            cached = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            LOGGER.warning("Ignoring malformed Redis LLM cache entry")
            return None
        if not isinstance(cached, dict) or cached.get("query") != dict(query):
            return None
        response = cached.get("response")
        return response if isinstance(response, str) else None

    def set(self, query: Mapping[str, Any], response: str) -> None:
        value = json.dumps(
            {"query": dict(query), "response": response},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            self.client.set(self._key(query), value, ex=self.ttl_seconds)
        except RedisError as exc:
            LOGGER.warning("Redis cache write failed; response was not cached: %s", exc)

    def _key(self, query: Mapping[str, Any]) -> str:
        serialized = json.dumps(
            query,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return f"{self.namespace}:{digest}"


def _cache_ttl_seconds() -> int:
    raw_value = os.getenv("LLM_CACHE_TTL_SECONDS", str(DEFAULT_TTL_SECONDS))
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError("LLM_CACHE_TTL_SECONDS must be an integer") from exc


@cache
def get_llm_cache() -> RedisLLMCache:
    return RedisLLMCache()
