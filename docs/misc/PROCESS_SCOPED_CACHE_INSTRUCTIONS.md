# Process-Scoped Telegram Cache — Copilot Implementation Guide

**Context:** `dev-demkov` branch · December 2025

This guide describes how to evolve the existing task-scoped Telegram cache (`auxilary_logic/telegram_cache.py`) into an optional *process-scoped* cache that can be shared across consecutive tasks while keeping per-account isolation and providing an opt-out path for debugging and legacy flows.

---

## 1. Goals & Constraints

- **Reduce cold-start misses between tasks** without sacrificing account isolation.
- **Keep task-scoped cache as fallback** (configurable scope, default remains `task` for safety).
- **Allow client-scoped/standalone cache** (`CacheIntegrationMixin.init_standalone_cache`) to function independently of broader scopes.
- **Zero behavior change when scope=task** (existing tests must still pass).
- **No cross-account leakage**: cache keys continue to include `client.phone_number`.
- **Graceful shutdown**: process cache flushes during interpreter exit or manual command so long-running bots do not leak memory.

---

## 2. Configuration Changes (`config.yaml`)

1. Under `cache:` add:
   ```yaml
   cache:
     scope: "task"            # "task" (current behavior) or "process"
     process:
       max_size: 2000          # overrides task max when scope=process
       cleanup_interval: 60    # seconds between background sweeps (process scope only)
     per_account:
       max_entries: 400        # safety cap so one account cannot evict everyone
   ```
2. Document defaults in `docs/RATE_LIMITING_AND_CACHING.md` (new subsection "Cache Scopes").
3. `setup_env.py` / sample configs should include new fields to avoid KeyErrors.

---

## 3. Architecture Overview

```
┌─────────────────────────────┐
│ TelegramCacheRegistry       │
│  - scope (task/process)     │
│  - get_cache(task_id=None)  │◄───── Task._run requests here
│  - shutdown()               │
└───────────┬────────────────┘
            │
   ┌────────▼────────┐
   │ TelegramCache   │  (existing implementation, now scope-agnostic)
   └─────────────────┘
```

- **Registry** (new helper) decides whether to reuse an existing cache (process scope) or create a fresh one (task scope).
- **TelegramCache** remains per-instance but gains optional callbacks for lifecycle and background cleanup.
- **Task** requests a cache via registry instead of instantiating `TelegramCache` directly.
- **Reporter logging** receives `cache_scope` metadata for clarity.

---

## 4. Step-by-Step Coding Instructions

### 4.1 Introduce `TelegramCacheScope`

File: `auxilary_logic/telegram_cache.py`
1. Add small Enum / literal definition:
   ```python
   class TelegramCacheScope(str, Enum):
       TASK = "task"
       PROCESS = "process"
   ```
2. Export via `__all__` for reuse.
3. Load `cache.scope` from config in registry (next section).

### 4.2 Create Cache Registry Helper

File: `auxilary_logic/telegram_cache.py` (bottom) **or** new module `auxilary_logic/cache_registry.py` (preferred for clarity).
1. Implement singleton `TelegramCacheRegistry` with:
   - `__init__(self, config)`
   - `_process_cache: Optional[TelegramCache]`
   - `get_cache(task_id: int | None)`
   - `release_cache(cache)` (no-op for process scope, clears task cache after run)
   - `shutdown()` to clear process cache on exit (hooked from `utils/setup_env.py` or `atexit`).
2. `get_cache` logic:
   ```python
   if scope == TASK: return TelegramCache(task_id, max_size=task_max)
   if scope == PROCESS:
       if self._process_cache is None:
           self._process_cache = TelegramCache(task_id=None, max_size=process_max)
       return self._process_cache
   ```
3. Ensure registry enforces `per_account.max_entries` (wrap `TelegramCache` with decorated `set_entry` / `should_evict` callback, or pass limit down via constructor).
4. Provide module-level helper:
   ```python
   def get_cache_registry() -> TelegramCacheRegistry:
       ...  # lazily instantiate with global config
   ```

### 4.3 Update Task Lifecycle (`main_logic/task.py`)
1. Replace direct `TelegramCache(...)` creation with registry call:
   ```python
   from auxilary_logic.telegram_cache import get_cache_registry
   registry = get_cache_registry()
   telegram_cache = registry.get_cache(task_id=self.task_id)
   cache_scope = registry.scope
   ```
2. Reporter events should include `cache_scope` and whether cache was warm or cold (use `telegram_cache.is_warm()` for this flag).
3. In `finally`, call `registry.release_cache(telegram_cache)` instead of `telegram_cache.clear()` so:
   - Task scope → behaves exactly as before (cache cleared, object dropped).
   - Process scope → no clearing; optionally log stats before next task begins.
4. On SIGTERM / graceful shutdown (existing cleanup path), call `registry.shutdown()` so process cache flushes.

### 4.4 Enhance `TelegramCache`
1. Accept new kwargs:
   - `scope: TelegramCacheScope`
   - `per_account_max_entries: Optional[int]`
   - `enable_background_cleanup: bool`
   - `cleanup_interval: int`
2. Track per-account entry counts:
   ```python
   self._account_entry_counts = collections.Counter()
   ```
   When inserting new entries, increment; when evicting/invalidation, decrement.
3. Enforce per-account max before storing:
   - If count >= limit, evict LRU entries for that account first.
4. Background cleanup (process scope):
   - Spawn `asyncio.create_task(self._periodic_cleanup())` only when `scope == PROCESS`.
   - Task loops with `await asyncio.sleep(cleanup_interval)` and calls `self._remove_expired_entries()`.
   - Provide `async def shutdown(self)` to cancel the task when registry shuts down.
5. Instrument logging to include scope and account ID for easier debugging.

### 4.5 Keep Client-Scoped Cache Independent
1. `CacheIntegrationMixin.init_standalone_cache()` should explicitly request `TelegramCache(task_id=None, scope=TelegramCacheScope.TASK, max_size=...)` to avoid touching global registry.
2. Add explanatory log message: "Initialized standalone cache (isolated from task/process scopes)."
3. Export helper constructor method `TelegramCache.create_isolated(max_size)` if you prefer to encapsulate this pattern.

### 4.6 Extend Tests
- `tests/old/test_telegram_cache.py`:
  - Add fixture toggling `cache.scope` to `process` using `monkeypatch` (or new config override helper).
  - Verify that consecutive `registry.get_cache()` calls return same instance when scope=process.
  - Ensure `release_cache` does not clear stats until `shutdown()`.
- `tests/old/test_task_cache_integration.py`:
  - Simulate two pseudo tasks fetching the same entity; confirm second task hits cache when scope=process.
- New tests for per-account limits & background cleanup (use fake time / short intervals).

---

## 5. Client-Scoped Cache Recommendation

- **Keep standalone cache independent.** Debuggers expect `client.init_standalone_cache()` to yield a disposable cache that does not modify process-global state; therefore the implementation should *not* consult the registry.
- Rationale: process cache may outlive debug runs, and standalone usage often occurs without reporter or shutdown hooks, so coupling would lead to leaked tasks.
- Implementation detail: call `TelegramCache(scope=TelegramCacheScope.TASK, task_id=None, ...)` directly so behavior stays deterministic.

---

## 6. Telemetry & Monitoring

1. Extend `cache_stats` reporter event payload with `scope`, `warm_start`, `per_account_max_entries`, `was_invalidated`.
2. Add log line before worker creation: `Using {cache_scope} cache (warm_start={warm})`.
3. For process scope, optionally push metrics to database via existing `log_cache_metrics` helper (see `docs/misc/CACHING_AND_API_OPTIMIZATION_ANALYSIS.md`).

---

## 7. Rollout Plan

1. **Phase 0 (PR 1):** Land registry + scope plumbing, default scope remains `task`.
2. **Phase 1 (PR 2):** Enable `scope=process` in staging config, watch cache hit rate and memory usage.
3. **Phase 2:** After validation, flip default to `process` if metrics show consistent benefits, but keep `task` as documented fallback.
4. **Phase 3:** Update README / docs to describe new knob and when to choose each scope.

---

## 8. Obsolescence Notes

- Task-scoped cache is **not obsolete**: it remains the safe option for short-lived CLI runs, debugging, or memory-constrained environments. The new scope merely augments behavior when explicitly selected via config.
- Keep existing task-clearing logic inside `registry.release_cache` so switching scopes only affects a single codepath (the registry), minimizing regression risk.

---

## 9. Quick Reference Checklist

- [ ] Add config options (`scope`, `process.*`, `per_account.*`).
- [ ] Introduce `TelegramCacheScope` enum.
- [ ] Implement `TelegramCacheRegistry` (singleton, `get_cache`, `release_cache`, `shutdown`).
- [ ] Update `Task._run` to use registry and log scope info.
- [ ] Enhance `TelegramCache` ctor (scope, per-account limits, cleanup task).
- [ ] Keep `init_standalone_cache()` isolated from registry.
- [ ] Extend reporter metrics + docs.
- [ ] Add/adjust tests for new flows.

---

**Ready:** Follow this document sequentially to implement the process-scoped cache without breaking current task-scoped behavior or standalone client workflows. Good luck!
