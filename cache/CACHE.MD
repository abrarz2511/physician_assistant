# LLM response cache

The retrieval-augmented coding workflow uses Redis as a read-through cache.
Before calling the LLM, it creates a canonical query from the model name,
system prompt, and complete retrieved encounter context. A SHA-256 digest of
that query is used as the Redis key, so clinical query text is not exposed in
key names. The value stores both the canonical query and its associated raw LLM
response.

On a hit, the stored LLM response is validated and used without making a model
request. On a miss, the LLM runs and its validated response is stored in Redis.
Entries expire after `LLM_CACHE_TTL_SECONDS` (24 hours by default). Configure
the server with `REDIS_URL` (default: `redis://127.0.0.1:6379/0`). Redis errors
are handled as misses so cache downtime does not stop coding recommendations.

Cached values can contain clinical information. Production Redis deployments
must use authentication, encrypted transport and storage, restricted network
access, and an appropriate retention period.
