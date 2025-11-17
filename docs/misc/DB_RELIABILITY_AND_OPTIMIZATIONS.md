# Database Reliability & Optimization Suggestions

This document collects actionable suggestions to improve reliability, performance, and maintainability of the MongoDB logic in `database.py`. Items are grouped by criticality to help prioritize implementation.

---

## Critical

1. Use atomic counters instead of max-scan ID allocation
   - Rationale: The current max-sort / scan approach to compute numeric `post_id` / `task_id` is race-prone under concurrent writers and can be expensive on large collections.
   - Action: Add a small `counters` collection and obtain numeric IDs via a single atomic `find_one_and_update` using `$inc` (ReturnDocument.AFTER). Replace the existing fallback scan logic in `add_post` and `add_task`.
   - File: `database.py` (methods: `add_post`, `add_task`)

2. Avoid blocking the event loop in `ensure_async`
   - Rationale: `ensure_async` currently calls synchronous functions directly inside the async wrapper which may block the event loop.
   - Action: Use `asyncio.to_thread` for CPU/blocking-bound sync helpers or ensure the decorator only wraps truly non-blocking helpers. Replace the wrapper with a safe call to `await asyncio.to_thread(func, *args, **kwargs)` when needed.
   - File: `database.py` (function: `ensure_async`)

3. Do not perform expensive duplicate-cleanup on startup
   - Rationale: Inline duplicate scanning and deletion during `_ensure_indexes` can be very costly on large collections and may delay startup.
   - Action: Move heavy duplicate cleanup to a migration script or admin task that runs outside normal startup and can be throttled.
   - File: `database.py` (function: `_ensure_indexes`), create a new migration script if needed

---

## High

1. Add retries with exponential backoff for client creation and initial `ping`
   - Rationale: Transient network or DNS blips can cause immediate startup failure.
   - Action: Wrap `AsyncIOMotorClient(...)` creation and the `await cls._client.admin.command("ping")` in a retry loop (3 attempts, exponential backoff + jitter). Use a small, configurable timeout.
   - File: `database.py` (`_init`, `_ensure_indexes`)

2. Implement a minimal health-check API / function
   - Rationale: Useful for readiness/liveness probes in containerized deployments and for monitoring.
   - Action: Add `MongoStorage.is_healthy()` that performs a cheap ping and returns status and latency.
   - File: `database.py`

3. Normalize and index `message_link`
   - Rationale: `get_post_by_link` currently attempts exact match then a fallback; normalizing and indexing (strip scheme, lowercase) enables direct single-query lookups.
   - Action: On insert/update, store a normalized `message_link_norm` and add an index; update `get_post_by_link` to query that field.
   - File: `database.py` (methods: `add_post`, `update_post`, `get_post_by_link`)

---

## Medium

1. Centralize retry decorator for transient DB errors
   - Rationale: Many operations should handle `AutoReconnect`, `NetworkTimeout`, etc., with limited retries.
   - Action: Implement a small `@retry_on_transient` decorator (configurable attempts/backoff) and apply it to index creation and other write-heavy paths.
   - File: new helper module or inside `database.py`.

2. Tune Mongo client options
   - Rationale: Control pool sizes and timeouts to match app concurrency.
   - Action: Expose and document env variables for `maxPoolSize`, `minPoolSize`, `socketTimeoutMS`, `connectTimeoutMS`, `waitQueueTimeoutMS`. Default sensibly.
   - File: `database.py` (`_init`)

3. Avoid loading full documents for simple checks
   - Rationale: Many methods should project only necessary fields (e.g., `.find({}, {"post_id": 1})`).
   - Action: Audit methods and replace `load_all_*` usages used purely for id/seed generation with projection-based queries or using counters.
   - File: `database.py` (multiple locations)

---

## Low

1. Reduce verbose logging further and add timings
   - Rationale: Logging entire records may expose sensitive data and make logs noisy.
   - Action: Standardize logs to include only minimal identifying fields (phone, account_id, post_id, task_id, proxy_name) and operation durations. Add debug-level timings for slow operations.
   - File: `database.py` (various methods)

2. Add TTL indexes for ephemeral events (if applicable)
   - Rationale: Automatic cleanup of old events reduces storage and improves query performance.
   - Action: If events are ephemeral, add a TTL index on `ts` or `created_at` and make TTL duration configurable.
   - File: migration script or `_ensure_indexes` with safe checks

3. Document process-level client behavior
   - Rationale: Clarify that the `AsyncIOMotorClient` should be created per process (not shared across forked processes) and add a `close()` helper.
   - Action: Add `MongoStorage.close()` and document usage in README/deployment notes.
   - File: `database.py`, `README.md`

---

## Suggested immediate implementation plan (small, high-value changes)
1. Implement atomic `counters` for `post_id`/`task_id` (replaces max-scan).
2. Convert `ensure_async` to use `asyncio.to_thread` for sync helpers.
3. Normalize `message_link` and add index + update `get_post_by_link` to use the normalized field.
4. Add a basic retry wrapper for `_ensure_indexes` ping.

If you want, I can implement items 1–3 now. Tell me which to start with and I'll create a PR-style patch, run quick static checks, and report results.

---

End of document.
