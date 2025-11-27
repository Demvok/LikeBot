# Retry Worst-Case Analysis

## Overview
This document analyzes the retry behavior when an account attempts to react to a post and every action fails on the first try. It provides a detailed breakdown of retry counts, timing, and configuration options.

## Execution Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│ LAYER 1: Task.client_worker() - WorkerRetryContext                     │
│ Config: action_retries=1, error_retry_delay=60s                        │
│                                                                          │
│ For each post:                                                          │
│   Attempt 1: client.react(message_link) → Fails with ConnectionError   │
│              → ctx.retry(e) → Sleeps 60s                               │
│   Attempt 2: client.react(message_link) → Succeeds                     │
│                                                                          │
│ TOTAL LAYER 1 RETRIES: 2 attempts (1 retry)                            │
└─────────────────────────────────────────────────────────────────────────┘
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ LAYER 2: Client.react() - NO retry logic                               │
│                                                                          │
│ Steps executed (no retry wrapper):                                     │
│   1. get_message_ids(link) → parse link                                │
│   2. get_entity_cached(identifier) → with rate limit (3s)              │
│   3. _get_or_fetch_channel_data() → DB fetch or Telegram fetch         │
│   4. rate_limiter.wait_if_needed('get_messages') → 0.3s                │
│   5. client.get_messages(entity, ids) → Telegram API call              │
│   6. _react(message, entity, channel) → send reaction                  │
│                                                                          │
│ If ANY step fails → Exception propagates to LAYER 1                    │
└─────────────────────────────────────────────────────────────────────────┘
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ LAYER 3: Client._react() - Internal emoji retry loop                   │
│                                                                          │
│ NO WorkerRetryContext, but has INTERNAL emoji fallback:                │
│                                                                          │
│ If palette has N emojis:                                               │
│   For each emoji in palette:                                           │
│     Try SendReactionRequest(emoji)                                     │
│     If ReactionInvalidError → Try next emoji                           │
│     If other error → Raise (propagates to LAYER 1)                     │
│                                                                          │
│ EMOJI RETRIES: Up to N attempts (palette size)                         │
│ But this is NOT a retry of the whole operation, just emoji selection   │
└─────────────────────────────────────────────────────────────────────────┘
```

## ❌ No Nested Retry Logic

**Critical Finding**: The retry logic is **NOT nested**. Here's the architecture:

1. **WorkerRetryContext** in `Task.client_worker()` retries the **entire** `client.react()` call
2. **Client.react()** has **NO retry wrapper** - it's a straight execution path
3. **Client._react()** has emoji fallback logic but **NOT retry logic** (it's just trying different emojis sequentially)

This means:
- ✅ Clean, predictable retry behavior
- ✅ No exponential retry multiplication
- ✅ Single point of retry control
- ✅ Easy to reason about timing

## Absolute Worst-Case Retry Count

### For a Single Post with Retryable Error

When a post reaction fails with a retryable error (ConnectionError, TimeoutError, RPCError, ServerError):

| Attempt | What Happens | Delay | Config Used |
|---------|-------------|-------|-------------|
| **1** | `client.react()` called → Fails with ConnectionError | 0s | - |
| | `ctx.retry(e)` increments attempt counter | - | - |
| | Sleep before retry | **60s** | `error_retry_delay` |
| **2** | `client.react()` called → Succeeds | 0s | - |

**Summary:**
- **Total attempts**: 2 (1 original + 1 retry)
- **Total retry delay**: 60 seconds
- **Max retries**: Controlled by `action_retries = 1`

## Configuration Parameters

### Retry Control

```yaml
# Primary retry configuration (used by WorkerRetryContext)
action_retries: 1          # Max retries = 1 (so 2 total attempts)
error_retry_delay: 60      # Delay between retries = 60 seconds
```

**To modify retry behavior:**

```yaml
# Allow MORE retries per post (e.g., 3 attempts = 2 retries):
action_retries: 2

# Reduce delay between retries:
error_retry_delay: 30

# Be more aggressive (NOT recommended - may trigger spam detection):
action_retries: 3
error_retry_delay: 15
```

⚠️ **Warning**: Increasing `action_retries` beyond 2-3 may trigger Telegram's spam detection algorithms!

### Rate Limiting (Not Retry)

These control delays between API calls but **do NOT control retries**:

```yaml
rate_limit_get_entity: 3        # Delay between entity lookups
rate_limit_get_messages: 0.3    # Delay between message fetches
rate_limit_send_reaction: 0.5   # Delay between reactions
rate_limit_send_message: 0.5    # Delay between messages
rate_limit_default: 0.2         # Default for other API calls
```

## Error Categories

### 1. Retryable Errors (Use WorkerRetryContext)

These errors trigger the retry logic with `error_retry_delay`:

- **ConnectionError** - Network connection failed
- **TimeoutError** - Request timed out
- **errors.RPCError** - Telegram RPC error
- **errors.ServerError** - Telegram server error
- **Exception** with `mapping['action'] == 'retry'` - Mapped to retry by error handler

**Behavior**: Retry up to `action_retries` times with `error_retry_delay` seconds between attempts.

### 2. FloodWaitError (Special Handling)

```python
wait_seconds = e.seconds  # From Telegram (typically 30-300+ seconds)
required_sleep = wait_seconds + 5  # Add safety buffer
await asyncio.sleep(required_sleep)  # Custom sleep
await ctx.retry(e, delay=False)  # Skip normal error_retry_delay
```

**Behavior**:
- **Retries**: Same as retryable errors (1 retry by default)
- **Delay**: `wait_seconds + 5` (dictated by Telegram, NOT `error_retry_delay`)
- **Config override**: Ignores `error_retry_delay`, uses Telegram's mandatory wait time

### 3. STOP Errors (Immediate Failure, 0 Retries)

These errors immediately stop the worker without any retries:

| Error | Reason | Action |
|-------|--------|--------|
| `SessionPasswordNeededError` | 2FA required | Stop worker |
| `PhoneCodeInvalidError` | Invalid verification code | Stop worker |
| `PhoneCodeExpiredError` | Verification code expired | Stop worker |
| `PhoneNumberBannedError` | Account banned | Stop worker, update status |
| `UserDeactivatedBanError` | Account deactivated/banned | Stop worker, update status |
| `AuthKeyUnregisteredError` | Session invalid | Stop worker, update status |
| `AuthKeyInvalidError` | Session invalid | Stop worker, update status |
| `SessionRevokedError` | Session revoked | Stop worker, update status |

**Behavior**: `return ctx.stop(e, ...)` - Worker exits immediately, no retries.

### 4. SKIP Errors (Skip Post, Move to Next, 0 Retries)

These errors skip the current post and move to the next one:

| Error | Reason | Action |
|-------|--------|--------|
| `UserNotParticipantError` | Not subscribed to channel | Skip post |
| `ChatAdminRequiredError` | Admin privileges required | Skip post |
| `ChannelPrivateError` | Cannot access private channel | Skip post |
| `MessageIdInvalidError` | Message ID is invalid | Skip post |
| `ValueError` ("Could not find input entity") | Entity resolution failed | Skip post |

**Behavior**: `ctx.skip(e, ...)` - Current post skipped, worker continues with next post.

## Complete Timing Breakdown

### Worst-Case Timeline for 1 Post with ConnectionError

Assuming:
- `humanisation_level = 1`
- Message has ~50 words (≈15s reading time)
- All random delays use average values

```
T+0s:     Worker starts
T+0s:       Worker stagger delay (random 2-10s, avg 6s)
T+6s:     Attempt 1 starts
T+6s:       rate_limiter.wait_if_needed('get_entity') = 3s
T+9s:       rate_limiter.wait_if_needed('get_messages') = 0.3s
T+9.3s:     GetMessagesViewsRequest (increment view counter)
T+9.3s:     Reading time delay (estimated ~15s for 50 words)
T+24.3s:    Pre-reaction delay (random 2-5s, avg 3.5s)
T+27.8s:    rate_limiter.wait_if_needed('send_reaction') = 0.5s
T+28.3s:    SendReactionRequest → ❌ FAILS with ConnectionError

T+28.3s:  ctx.retry(e) called
T+28.3s:    Increment attempt counter (attempt = 1)
T+28.3s:    Sleep for error_retry_delay = 60s

T+88.3s:  Attempt 2 starts (retry)
T+88.3s:    rate_limiter.wait_if_needed('get_entity') = 3s
T+91.3s:    rate_limiter.wait_if_needed('get_messages') = 0.3s
T+91.6s:    GetMessagesViewsRequest (increment view counter)
T+91.6s:    Reading time delay (~15s)
T+106.6s:   Pre-reaction delay (avg 3.5s)
T+110.1s:   rate_limiter.wait_if_needed('send_reaction') = 0.5s
T+110.6s:   SendReactionRequest → ✅ SUCCESS

T+110.6s: Inter-reaction delay (random 15-30s, avg 22.5s) before next post
T+133.1s: Ready for next post

TOTAL TIME: ~133 seconds for 1 post with 1 retry
```

### Breakdown by Category

| Phase | Time | Config Key |
|-------|------|------------|
| **Worker stagger** | 6s | `worker_start_delay_min/max` |
| **Rate limits (attempt 1)** | 3.8s | `rate_limit_*` |
| **Humanization (attempt 1)** | 18.5s | `humanisation_level`, `min/max_delay_before_reaction` |
| **Retry delay** | 60s | `error_retry_delay` |
| **Rate limits (attempt 2)** | 3.8s | `rate_limit_*` |
| **Humanization (attempt 2)** | 18.5s | Same as attempt 1 |
| **Inter-post delay** | 22.5s | `min/max_delay_between_reactions` |
| **Total** | **133s** | |

## Multiple Posts Worst-Case

### Formula

For **P posts**, all failing once with retryable errors:

```
Total Time = Worker_Stagger 
           + P × (Rate_Limits + Humanization + Reaction)  [First attempts]
           + P × error_retry_delay                        [Retry delays]
           + P × (Rate_Limits + Humanization + Reaction)  [Retry attempts]
           + (P-1) × Inter_Post_Delay                     [Between posts]
```

### Example: 10 Posts, All Fail Once

```
Worker stagger:          6s
First attempts:     10 × 22s = 220s
Retry delays:       10 × 60s = 600s
Retry attempts:     10 × 22s = 220s
Inter-post delays:   9 × 22s = 198s
─────────────────────────────────
TOTAL:                    1,244s ≈ 20.7 minutes
```

### Comparison Table

| Posts | Success (no retries) | 1 Retry Each | 2 Retries Each |
|-------|---------------------|--------------|----------------|
| 1 | 50s | 133s | 216s |
| 5 | 180s | 600s | 1,020s (17 min) |
| 10 | 350s (6 min) | 1,244s (21 min) | 2,138s (36 min) |
| 20 | 690s (11.5 min) | 2,534s (42 min) | 4,378s (73 min) |
| 50 | 1,710s (28.5 min) | 6,360s (106 min) | 11,010s (184 min) |

**Key Insight**: Retry delays dominate for large post counts!

## Emoji Fallback (Not Retry Logic)

The emoji fallback in `Client._react()` is **NOT a retry mechanism**. It's a selection algorithm:

### How It Works

```python
# Palette: ['👍', '❤️', '🔥', '😊']

# Ordered mode: Try in sequence
for emoji in ['👍', '❤️', '🔥', '😊']:
    try:
        SendReactionRequest(emoji)
        return  # Success, exit
    except ReactionInvalidError:
        continue  # This emoji not allowed, try next
    except OtherError:
        raise  # Propagate to Layer 1 retry

# Random mode: Try in shuffled order
shuffled = shuffle(['👍', '❤️', '🔥', '😊'])
# Same logic as ordered
```

### Key Points

- ✅ Tries different emojis if one is restricted
- ✅ No delay between emoji attempts (instant fallback)
- ❌ **NOT a retry** - it's just emoji selection
- ❌ If ConnectionError happens during SendReactionRequest, entire `_react()` fails and propagates to Layer 1

**Example**: If palette has 5 emojis and first 2 fail with `ReactionInvalidError`, it tries emoji #3. Total emoji attempts: 3 (not retries, just selection).

## Summary

### Quick Reference

| Metric | Value | Config |
|--------|-------|--------|
| **Max retry attempts per post** | 2 | `action_retries = 1` |
| **Delay between retries** | 60s | `error_retry_delay` |
| **FloodWait retry delay** | Telegram's value + 5s | N/A (Telegram dictates) |
| **Emoji fallback attempts** | Palette size (NOT retries) | N/A |
| **Nested retry layers** | 0 (single layer only) | N/A |

### Recommendations

#### Conservative (Default)
```yaml
action_retries: 1          # 2 total attempts
error_retry_delay: 60      # 60s between retries
```
- ✅ Safe from spam detection
- ✅ Handles transient errors
- ⚠️ Slow for large post batches

#### Balanced
```yaml
action_retries: 2          # 3 total attempts
error_retry_delay: 45      # 45s between retries
```
- ✅ Better success rate
- ⚠️ Longer total time
- ⚠️ Slight spam risk increase

#### Aggressive (Not Recommended)
```yaml
action_retries: 3          # 4 total attempts
error_retry_delay: 30      # 30s between retries
```
- ⚠️ High spam detection risk
- ⚠️ May trigger FloodWaitError
- ❌ Not recommended for production

### Architecture Benefits

1. **Single Retry Layer** - No nested retries, predictable behavior
2. **Config-Driven** - Easy to adjust without code changes
3. **Error-Specific Handling** - STOP/SKIP/RETRY based on error type
4. **FloodWait Aware** - Respects Telegram's mandatory wait times
5. **Humanization First** - Delays prevent spam detection even without retries

### Key Takeaway

**The retry system is simple, single-layered, and predictable.** With `action_retries=1`, you get exactly 2 attempts per post with a 60-second delay between them. There is no retry nesting or multiplication - what you configure is what you get.
