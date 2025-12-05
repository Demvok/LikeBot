# Retry Handling Optimization Summary

## Overview
This document summarizes the retry handling optimizations made to standardize and centralize retry logic across the LikeBot codebase.

## Retry Pattern Decision Tree
```
┌─────────────────────────────────────────────────────────────┐
│ Do you need retry logic?                                     │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
        ┌─────────────────────────────────────┐
        │ Is it a simple function?            │
        │ (single operation, uniform retry)   │
        └─────────────────────────────────────┘
                │                    │
               YES                  NO
                │                    │
                ▼                    ▼
    ┌───────────────────┐   ┌──────────────────────────────┐
    │ @async_retry      │   │ Does each item need different│
    │ decorator         │   │ outcomes (RETRY/SKIP/STOP)?  │
    └───────────────────┘   └──────────────────────────────┘
                                    │              │
                                   YES            NO
                                    │              │
                                    ▼              ▼
                        ┌──────────────────┐  ┌─────────────────┐
                        │WorkerRetryContext│  │  RetryContext   │
                        │(for task workers)│  │(manual control) │
                        └──────────────────┘  └─────────────────┘
                        
    ┌─────────────────────────────────────────────────────────┐
    │ SPECIAL CASE: MongoDB ID allocation race conditions     │
    │ → Use manual for-loop with NO DELAY (immediate retry)   │
    └─────────────────────────────────────────────────────────┘
```

## Changes Made

### 1. Rate Limiter Configuration (humaniser.py)
**Before:**
- Hardcoded delays in `TelegramAPIRateLimiter.__init__()`:
  ```python
  self._min_delay = {
      'get_entity': 3,
      'get_messages': 0.3,
      'send_reaction': 0.5,
      'send_message': 0.5,
      'default': 0.2
  }
  ```

**After:**
- Config-based delays loaded from `config.yaml`:
  ```python
  def _ensure_delays_loaded(self):
      if self._min_delay is None:
          self._min_delay = {
              'get_entity': get_delay_config('rate_limit_get_entity', 3.0),
              'get_messages': get_delay_config('rate_limit_get_messages', 0.3),
              'send_reaction': get_delay_config('rate_limit_send_reaction', 0.5),
              'send_message': get_delay_config('rate_limit_send_message', 0.5),
              'default': get_delay_config('rate_limit_default', 0.2)
          }
  ```

**Benefits:**
- ✅ Consistent with rest of codebase (all delays in config.yaml)
- ✅ Easy to adjust rate limits without code changes
- ✅ Lazy-loading avoids circular import issues
- ✅ Maintains backward compatibility with sensible defaults

### 2. Config.yaml Additions
Added new rate limiter configuration section:
```yaml
delays:
  # === RATE LIMITER CONFIGURATION ===
  # Minimum delays between Telegram API calls (prevents flood errors)
  rate_limit_get_entity: 3  # Seconds between entity lookups
  rate_limit_get_messages: 0.3  # Seconds between message fetches
  rate_limit_send_reaction: 0.5  # Seconds between reactions
  rate_limit_send_message: 0.5  # Seconds between messages
  rate_limit_default: 0.2  # Default for other API calls
```

### 3. Enhanced Documentation (retry.py)
Added comprehensive module-level documentation covering:
- **4 Retry Patterns** with use cases and examples
- **Configuration guide** for config.yaml
- **Usage examples** for each pattern
- **Best practices** for choosing the right pattern

#### The 4 Retry Patterns:

| Pattern | Use Case | Example |
|---------|----------|---------|
| **@async_retry decorator** | Simple functions needing retry | `Client.connect()` |
| **RetryContext** | Manual control with custom logic | Connection with session invalidation |
| **WorkerRetryContext** | Complex workflows with RETRY/SKIP/STOP | `Task.client_worker()` |
| **Manual loops** | Immediate retries, NO delay | Database ID allocation |

## Review Findings

### Optimal Patterns Already in Use
The codebase already uses retry handling optimally:

1. **Task Workers** ✅
   - `WorkerRetryContext` in `Task.client_worker()` 
   - Correctly handles RETRY/SKIP/STOP outcomes
   - Proper use of `error_retry_delay` (60s) for transient errors

2. **Client Connection** ✅
   - `RetryContext` in `Client.connect()` and `Client.disconnect()`
   - Handles session invalidation with immediate retry (no delay)
   - Uses `connection_retries` (5) and `reconnect_delay` (3s)

3. **Database Operations** ✅
   - Manual `for` loops in `add_post()`, `add_task()` 
   - **Should NOT be converted** - handles MongoDB race conditions with immediate retry
   - Adding delay would hurt performance without benefit

4. **Reporter** ✅
   - Uses `get_delay_config('batch_error_delay', 0.2)` for writer loop errors
   - Already standardized to use config

## Configuration Strategy

### Retry Counts
```yaml
session_creation_retries: 2   # Login code attempts
connection_retries: 5          # Network connection attempts
action_retries: 1              # Telegram actions (react, comment)
```

### Retry Delays
```yaml
reconnect_delay: 3             # Between connection retries
action_retry_delay: 30         # Between action retries
error_retry_delay: 60          # After transient errors (ConnectionError, TimeoutError, RPCError)
```

**Rationale:**
- **Actions** (`action_retry_delay: 30s`): Quick retry for user operations
- **Errors** (`error_retry_delay: 60s`): Longer delay for network issues
- **Connections** (`reconnect_delay: 3s`): Medium delay for connection issues

## Testing Verification

✅ **Syntax Check**: Both modified files compile without errors
```bash
python -m py_compile auxilary_logic/humaniser.py
python -m py_compile utils/retry.py
```

✅ **Import Check**: All imports work correctly
```python
from auxilary_logic.humaniser import TelegramAPIRateLimiter, rate_limiter
from utils.retry import async_retry, get_retry_config, get_delay_config, RetryContext, WorkerRetryContext
```

✅ **Runtime Check**: Rate limiter loads config correctly
```python
rate_limiter._ensure_delays_loaded()
# Output: {'get_entity': 3.0, 'get_messages': 0.3, 'send_reaction': 0.5, 'send_message': 0.5, 'default': 0.2}
```

## No Breaking Changes

All changes are **backward compatible**:
- Config keys have sensible defaults
- Lazy-loading prevents import issues
- Existing retry logic unchanged (only centralized)
- No API changes to existing functions

## Future Recommendations

### Consider for Future Work:
1. **Exponential Backoff**: For connection retries (already supported by `@async_retry`)
   ```python
   @async_retry(exponential_backoff=True, backoff_multiplier=2.0, max_delay=60.0)
   async def connect_with_backoff():
       ...
   ```

2. **Retry Metrics**: Track retry counts/success rates for monitoring
   - Could be added to Reporter events
   - Useful for identifying problematic accounts/channels

3. **Jitter**: Add randomness to retry delays to prevent thundering herd
   ```python
   delay = base_delay + random.uniform(0, jitter)
   ```

### Do NOT Change:
- ❌ Database ID allocation loops (optimal as-is)
- ❌ WorkerRetryContext delay keys (correctly uses `error_retry_delay`)
- ❌ Flood wait handling (already has custom logic with `wait_seconds + 5`)

## Summary

**Total Changes**: 3 files modified
- `auxilary_logic/humaniser.py`: Config-based rate limiting
- `config.yaml`: Added rate limiter configuration section
- `utils/retry.py`: Enhanced documentation

**Lines Changed**: ~50 lines
**Breaking Changes**: None
**Test Coverage**: Syntax, imports, runtime verified

**Result**: 
✅ Standardized retry configuration
✅ Improved maintainability  
✅ Better documentation
✅ No performance impact
✅ Backward compatible

---

## Quick Reference

### When to Use Each Pattern

```python
# 1. DECORATOR - Simple function with uniform retry
@async_retry(retries_key='connection_retries', delay_key='reconnect_delay')
async def connect_to_service():
    await service.connect()

# 2. CONTEXT MANAGER - Custom logic between retries
async with RetryContext(retries_key='connection_retries') as ctx:
    while ctx.should_retry():
        try:
            session = await create_session(force_new=force_new_session)
            await client.connect(session)
            ctx.success()
        except AuthError:
            force_new_session = True  # Custom logic
            await ctx.failed(e, delay=False)

# 3. WORKER CONTEXT - Complex workflows with multiple outcomes
ctx = WorkerRetryContext(logger=logger)
for item in items:
    ctx.reset_for_item()
    while ctx.should_retry():
        try:
            await process(item)
            ctx.success()
        except SkipError as e:
            ctx.skip(e, "Invalid item")
        except FatalError as e:
            return ctx.stop(e, "Fatal error")
        except RetryableError as e:
            await ctx.retry(e, "Retrying...")

# 4. MANUAL LOOP - Race condition handling (NO DELAY)
for attempt in range(retries):
    try:
        await db.insert_with_id(doc, id)
        break
    except DuplicateKeyError:
        id = get_next_id()  # Immediate retry
        continue
```

### Config Keys Reference

| Key | Default | Description |
|-----|---------|-------------|
| `rate_limit_get_entity` | 3.0 | Delay between entity lookups |
| `rate_limit_get_messages` | 0.3 | Delay between message fetches |
| `rate_limit_send_reaction` | 0.5 | Delay between reactions |
| `rate_limit_send_message` | 0.5 | Delay between messages |
| `rate_limit_default` | 0.2 | Default API call delay |
| `connection_retries` | 5 | Connection retry count |
| `reconnect_delay` | 3 | Delay between connection retries |
| `action_retries` | 1 | Action retry count |
| `action_retry_delay` | 30 | Delay between action retries |
| `error_retry_delay` | 60 | Delay after transient errors |

### Helper Functions

```python
from utils.retry import get_retry_config, get_delay_config, random_delay

# Get retry count from config
retries = get_retry_config('action_retries')  # Returns int

# Get delay from config
delay = get_delay_config('error_retry_delay')  # Returns float

# Sleep for random duration between min/max config values
await random_delay('min_delay_key', 'max_delay_key', logger, "Reason")
```

