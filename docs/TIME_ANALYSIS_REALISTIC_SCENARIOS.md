# Time Analysis: Realistic Scenarios
**Date:** November 30, 2025  
**Config:** Updated rate limits (get_entity: 10s, send_reaction: 6s, get_messages: 1s)  
**Focus:** 100+ accounts reacting to 1-3 posts

---

## Executive Summary

With the updated rate limits, task execution time is **heavily dependent on caching efficiency**. The first few workers experience longer delays due to rate limiting, but subsequent workers benefit dramatically from cached entities and messages.

### Quick Answer (100 Accounts, 1 Post)
- **Best Case:** ~10 minutes (full cache, only reaction delays)
- **Typical Case:** ~11-12 minutes (entity cached, messages vary)
- **Worst Case:** ~15-20 minutes (cold cache, first worker hits all delays)

---

## Updated Rate Limits (from config.yaml)

```yaml
rate_limit_get_entity: 10      # 10 seconds (was 3s)
rate_limit_get_messages: 1     # 1 second (was 0.3s)
rate_limit_send_reaction: 6    # 6 seconds (was 0.5s)
rate_limit_send_message: 10    # 10 seconds (was 0.5s)
```

### Additional Delays
```yaml
# Worker stagger (prevents simultaneous starts)
worker_start_delay_min: 5
worker_start_delay_max: 20

# Inter-reaction delays (per account, per post)
min_delay_between_reactions: 20
max_delay_between_reactions: 40

# Pre-action humanization
min_delay_before_reaction: 3
max_delay_before_reaction: 8

# Reading delays (humanisation_level: 1)
# Varies by message length, fallback: 2-5s
```

---

## Scenario 1: 100 Accounts → 1 Post

### Setup
- 100 worker coroutines (one per account)
- All workers process the same single post
- Workers run **concurrently** (asyncio.gather)
- Rate limits enforced **per method globally** (shared rate limiter)

### Best Case: Fully Cached (Previous Task on Same Post)

**Conditions:**
- Entity already cached from previous task (TTL: 5 min)
- Message already cached (TTL: 1 min)
- Channel metadata in database
- URL alias in database

**Per-Worker Timeline:**
```
0s:     Worker starts (stagger delay already completed)
0-8s:   Pre-action delay (3-8s random)
8s:     GetMessagesViewsRequest (no rate limit, instant)
8-13s:  Reading delay (~2-5s, message dependent)
13s:    get_entity_cached() → CACHE HIT (instant, no delay)
13s:    get_message_cached() → CACHE HIT (instant, no delay)
13s:    SendReactionRequest → RATE LIMITED (6s delay)
19s:    Reaction sent
19-59s: Inter-reaction delay (20-40s, for next post if any)
```

**Critical Path (Rate Limit Bottleneck):**
- All 100 workers need to send reaction (SendReactionRequest)
- Rate limit: 6 seconds per reaction
- Workers queue up sequentially for this call

**Calculation:**
```
Worker 1:   0s start + 8s delays + 6s reaction = 14s total
Worker 2:   0s start + 8s delays + 12s reaction (waited 6s) = 20s total
Worker 3:   0s start + 8s delays + 18s reaction (waited 12s) = 26s total
...
Worker 100: 0s start + 8s delays + 600s reaction (waited 594s) = 608s total

Total time: ~608 seconds ≈ 10.1 minutes
```

**Note:** Workers start with stagger (5-20s), so actual time may vary by ±20s.

**Adjusted for Stagger:**
```
Average stagger: 12.5s
Worker 1 starts at: 12.5s
Worker 100 completes at: 620s ≈ 10.3 minutes
```

### Typical Case: Warm Entity Cache, Cold Message Cache

**Conditions:**
- Entity cached (from previous task or first few workers)
- Message NOT cached yet (first time seeing this specific message)
- Channel metadata in database

**First Worker Timeline (Pays the Cost):**
```
0s:     Worker starts
0-8s:   Pre-action delay (3-8s)
8s:     GetMessagesViewsRequest (instant)
8-13s:  Reading delay (~5s)
13s:    get_entity() → CACHE MISS → RATE LIMITED (10s delay)
23s:    Entity fetched and cached
23s:    get_message() → CACHE MISS → RATE LIMITED (1s delay)
24s:    Message fetched and cached
24s:    SendReactionRequest → RATE LIMITED (6s delay)
30s:    First worker completes
```

**Workers 2-100 Timeline (Cache Hits):**
```
0s:     Worker starts
0-8s:   Pre-action delay (3-8s)
8s:     GetMessagesViewsRequest (instant)
8-13s:  Reading delay (~5s)
13s:    get_entity_cached() → CACHE HIT (instant)
13s:    get_message_cached() → CACHE HIT (instant, Worker 1 cached it)
13s:    SendReactionRequest → RATE LIMITED (6s delay, sequential queue)
```

**Calculation:**
```
Worker 1: 30s (fetches entity + message)
Worker 2: 13s + 12s (6s*2 reaction wait) = 25s
Worker 3: 13s + 18s (6s*3 reaction wait) = 31s
...
Worker 100: 13s + 600s (6s*100 reaction wait) = 613s ≈ 10.2 minutes
```

**With Stagger:**
```
First worker starts: 12.5s (average stagger)
Last worker completes: 625s ≈ 10.4 minutes
```

**Key Insight:** Typical case is only ~18 seconds slower than best case (30s vs 14s for first worker), because subsequent workers still benefit from cache!

### Worst Case: Cold Cache, New Channel

**Conditions:**
- Entity NOT cached (first time seeing this channel)
- Message NOT cached
- Channel NOT in database (need GetFullChannelRequest)

**First Worker Timeline:**
```
0s:     Worker starts
0-8s:   Pre-action delay
8s:     GetMessagesViewsRequest (instant)
8-13s:  Reading delay
13s:    get_entity() → CACHE MISS → RATE LIMITED (10s delay)
23s:    Entity fetched
23s:    GetFullChannelRequest → RATE LIMITED (10s delay, uses get_entity limit)
33s:    Channel info fetched and stored in DB
33s:    get_message() → CACHE MISS → RATE LIMITED (1s delay)
34s:    Message fetched
34s:    SendReactionRequest → RATE LIMITED (6s delay)
40s:    First worker completes
```

**Workers 2-100 Timeline:**
```
Same as typical case: 13s + (worker_number * 6s) for reaction queue
```

**Calculation:**
```
Worker 1: 40s (fetches everything)
Worker 2-100: Same as typical case (cache hits)
Last worker: 13s + 600s = 613s ≈ 10.2 minutes
```

**With Stagger:**
```
Total time: ~625s ≈ 10.4 minutes
```

**Key Insight:** Worst case is only ~10 seconds slower than typical case because only the first worker pays the full cost!

---

## Scenario 2: 100 Accounts → 2 Posts

### Best Case: Fully Cached

**Assumptions:**
- Both posts from the same channel (entity cached)
- Both messages cached
- No stagger between posts (workers process sequentially)

**Per-Worker Timeline:**
```
Post 1:
  0-8s:   Pre-action delay
  8-13s:  Reading delay
  13s:    Cached lookups (instant)
  13s:    SendReactionRequest → RATE LIMITED (6s)
  19s:    Post 1 complete
  
  19-59s: Inter-reaction delay (20-40s, avg 30s)
  
Post 2:
  49s:    Start Post 2
  49-57s: Pre-action delay
  57-62s: Reading delay
  62s:    Cached lookups (instant)
  62s:    SendReactionRequest → RATE LIMITED (6s)
  68s:    Post 2 complete
```

**Critical Path:**
```
Worker 1:   68s (fastest possible)
Worker 2:   68s base + 12s reaction waits (6s*2 posts) = 80s
Worker 3:   68s base + 24s reaction waits = 92s
...
Worker 100: 68s base + 1200s reaction waits (6s*100*2) = 1268s ≈ 21.1 minutes
```

**With Stagger:**
```
Total time: ~1280s ≈ 21.3 minutes
```

### Typical Case: Entity Cached, Messages Fresh

**First Worker:**
```
Post 1: 30s (fetch entity + message)
Post 2: 34s (message fetch only, entity cached) + 30s delay = 64s
Total: 94s
```

**Workers 2-100:**
```
Post 1: 19s (cache hits)
Post 2: 19s (cache hits) + 30s delay = 49s
Total: 68s base + reaction waits
```

**Calculation:**
```
Worker 1: 94s
Worker 100: 68s + 1200s = 1268s ≈ 21.1 minutes
```

**With Stagger:**
```
Total time: ~1280s ≈ 21.3 minutes
```

**Key Insight:** Typical case converges to best case very quickly (only first worker differs by ~26s).

### Worst Case: Cold Cache

**First Worker:**
```
Post 1: 40s (fetch entity + channel + message)
Post 2: 34s (entity/channel cached, message fresh) + 30s delay = 64s
Total: 104s
```

**Workers 2-100:**
```
Same as typical/best case: 68s base + reaction waits
```

**Calculation:**
```
Worker 1: 104s
Worker 100: 68s + 1200s = 1268s ≈ 21.1 minutes
```

**With Stagger:**
```
Total time: ~1280s ≈ 21.3 minutes
```

---

## Scenario 3: 100 Accounts → 3 Posts

### Best Case

**Per-Worker Base Time:**
```
Post 1: 19s (pre-action + reading + reaction delay)
Delay:  30s (inter-reaction)
Post 2: 19s
Delay:  30s
Post 3: 19s
Total:  117s base
```

**Reaction Queue Wait:**
```
100 workers × 3 posts × 6s = 1800s of queued reactions
Worker 100 waits: 1800s ≈ 30 minutes
```

**Calculation:**
```
Worker 1: 117s ≈ 2 minutes
Worker 100: 117s + 1800s = 1917s ≈ 32 minutes
```

**With Stagger:**
```
Total time: ~1930s ≈ 32.2 minutes
```

### Typical Case

**First Worker:**
```
Post 1: 30s (fetch entity + message)
Delay:  30s
Post 2: 24s (entity cached, message fresh)
Delay:  30s
Post 3: 24s
Total:  138s
```

**Workers 2-100:**
```
Base: 117s (all cache hits)
Wait: 1800s (reaction queue)
Total: 1917s ≈ 32 minutes
```

**With Stagger:**
```
Total time: ~1930s ≈ 32.2 minutes
```

### Worst Case

**First Worker:**
```
Post 1: 40s (fetch everything)
Delay:  30s
Post 2: 24s (cached)
Delay:  30s
Post 3: 24s
Total:  148s
```

**Workers 2-100:**
```
Same as typical: 1917s ≈ 32 minutes
```

**With Stagger:**
```
Total time: ~1930s ≈ 32.2 minutes
```

---

## Summary Tables

### 100 Accounts Execution Time

| Posts | Best Case | Typical Case | Worst Case | Difference |
|-------|-----------|--------------|------------|------------|
| **1** | 10.3 min | 10.4 min | 10.4 min | ~6 seconds |
| **2** | 21.3 min | 21.3 min | 21.3 min | ~10 seconds |
| **3** | 32.2 min | 32.2 min | 32.2 min | ~15 seconds |

**Key Insight:** With 100 workers, the difference between best and worst case is **negligible** (~15 seconds for 3 posts) because:
1. Only the first worker hits cache misses
2. Remaining 99 workers benefit from cache
3. The reaction queue (6s per reaction) dominates total time

### Breakdown: Where Does Time Go?

#### 1 Post, 100 Accounts (Typical Case)

| Component | Time (First Worker) | Time (Worker 100) | Notes |
|-----------|--------------------:|------------------:|-------|
| Stagger delay | 12.5s | 12.5s | Random 5-20s |
| Pre-action delay | 5.5s | 5.5s | Random 3-8s |
| Reading delay | 5s | 5s | Message-dependent |
| get_entity | 10s | 0s | **Cached after first worker** |
| get_message | 1s | 0s | **Cached after first worker** |
| GetMessagesViewsRequest | <1s | <1s | Not rate limited |
| SendReactionRequest delay | 6s | 600s | **Reaction queue bottleneck** |
| **TOTAL** | **~40s** | **~628s** | **10.5 min** |

**Bottleneck:** SendReactionRequest queue (100 workers × 6s = 600s)

#### 3 Posts, 100 Accounts (Typical Case)

| Component | Time (First Worker) | Time (Worker 100) | Notes |
|-----------|--------------------:|------------------:|-------|
| Stagger delay | 12.5s | 12.5s | One-time |
| Post 1 (full) | 30s | 19s | First worker fetches |
| Inter-delay | 30s | 30s | Between posts |
| Post 2 (cached) | 24s | 19s | Entity cached |
| Inter-delay | 30s | 30s | Between posts |
| Post 3 (cached) | 24s | 19s | Entity cached |
| Reaction queue | 18s | 1800s | **3 posts × 600s** |
| **TOTAL** | **~168s** | **~1929s** | **32.2 min** |

**Bottleneck:** Still the reaction queue (dominates for large worker counts)

---

## Critical Factors Affecting Time

### 1. Reaction Queue (Dominant Factor)
**Formula:** `(Number of Workers × Number of Posts × 6s)`

| Workers | Posts | Queue Time | % of Total |
|---------|-------|------------|------------|
| 100 | 1 | 600s (10 min) | ~95% |
| 100 | 2 | 1200s (20 min) | ~94% |
| 100 | 3 | 1800s (30 min) | ~93% |

**Observation:** Reaction rate limit (6s) is the primary time sink for large-scale tasks.

### 2. Inter-Reaction Delay (Secondary Factor)
**Formula:** `((Number of Posts - 1) × 30s avg delay)`

| Posts | Inter-Delay | Impact |
|-------|-------------|--------|
| 1 | 0s | None |
| 2 | 30s | +30s per worker |
| 3 | 60s | +60s per worker |

**Observation:** Adds linearly with post count, independent of worker count.

### 3. Cache Miss Penalties (Minimal for 100+ Workers)

| Cache State | First Worker Penalty | Impact on Total |
|-------------|---------------------|-----------------|
| Best (full cache) | 0s | 0s |
| Typical (partial) | +11s | <0.3% of total |
| Worst (cold) | +21s | <0.5% of total |

**Observation:** With 100 workers, first-worker penalties are negligible (<1% of total time).

### 4. Worker Stagger (One-Time Overhead)

| Stagger Range | Average Delay | Impact |
|---------------|---------------|--------|
| 5-20s | 12.5s | One-time overhead |

**Observation:** Fixed overhead regardless of post count.

---

## Scaling Analysis

### Impact of Worker Count

#### 1 Post, Varying Worker Counts

| Workers | Best Case | Typical Case | Worst Case |
|---------|-----------|--------------|------------|
| **10** | ~1.3 min | ~1.4 min | ~1.5 min |
| **50** | ~5.3 min | ~5.4 min | ~5.5 min |
| **100** | ~10.3 min | ~10.4 min | ~10.4 min |
| **200** | ~20.3 min | ~20.4 min | ~20.4 min |
| **500** | ~50.3 min | ~50.4 min | ~50.4 min |

**Formula:** `Time ≈ (Workers × 6s) + 20s overhead`

**Insight:** Time scales **linearly** with worker count due to reaction queue.

### Impact of Post Count

#### 100 Workers, Varying Post Counts

| Posts | Best Case | Typical Case | Worst Case |
|-------|-----------|--------------|------------|
| **1** | 10.3 min | 10.4 min | 10.4 min |
| **2** | 21.3 min | 21.3 min | 21.3 min |
| **3** | 32.2 min | 32.2 min | 32.2 min |
| **5** | 54.2 min | 54.2 min | 54.2 min |
| **10** | 109.2 min | 109.2 min | 109.2 min |

**Formula:** `Time ≈ Posts × [(Workers × 6s) + (Posts - 1) × 30s]`

**Insight:** Time scales **linearly** with post count.

---

## Optimization Opportunities

### Current Bottleneck: Reaction Rate Limit (6s)

**Problem:** With 100 workers, 95% of time is spent waiting in reaction queue.

**Potential Solutions:**

1. **Reduce rate_limit_send_reaction** (if safe)
   - Current: 6s
   - Aggressive: 3s → 50% faster
   - Risky: <3s → potential FloodWaitError

2. **Batch reactions** (not supported by Telegram API)
   - Would require API changes

3. **Distribute across multiple bot instances**
   - 2 bots with 50 workers each = 50% faster
   - Requires infrastructure changes

4. **Accept the time cost**
   - 10 minutes for 100 accounts is reasonable
   - Prioritizes safety over speed

### Secondary Optimizations (Minimal Gain)

1. **Pre-warm cache** before workers start
   - Saves: ~10-20s (first worker penalty)
   - Benefit: <1% for 100 workers

2. **Reduce inter-reaction delay**
   - Current: 20-40s
   - Could reduce to: 10-20s
   - Benefit: ~30s per 2+ posts (still <2% of total)

3. **Parallel reaction batching** (if API allowed)
   - Not currently possible with Telegram

---

## Realistic Expectations

### Production Scenario: 150 Accounts, 2 Posts

**Calculation:**
```
Base time per worker: 68s (2 posts with delays)
Reaction queue: 150 workers × 2 posts × 6s = 1800s
Stagger: +12.5s average
Total: ~1812s ≈ 30.2 minutes
```

**Range (accounting for randomness):**
- Best case: 29.5 minutes (lucky delays)
- Typical case: 30.2 minutes (average delays)
- Worst case: 31.0 minutes (unlucky delays + cold cache)

**Variability:** ±30 seconds due to random delays

### Production Scenario: 200 Accounts, 1 Post

**Calculation:**
```
Base time per worker: 19s (1 post)
Reaction queue: 200 workers × 1 post × 6s = 1200s
Stagger: +12.5s
Total: ~1212s ≈ 20.2 minutes
```

**Range:**
- Best case: 19.8 minutes
- Typical case: 20.2 minutes
- Worst case: 20.6 minutes

---

## Conclusion

### Key Takeaways

1. **Reaction queue dominates** (95% of execution time for 100+ workers)
2. **Cache efficiency matters little** at scale (<1% difference between cold/warm)
3. **Time scales linearly** with both worker count and post count
4. **Inter-reaction delays** add ~30s per additional post (per worker)

### Realistic Time Estimates (100 Accounts)

| Posts | Time (Best) | Time (Typical) | Time (Worst) | Range |
|-------|-------------|----------------|--------------|-------|
| 1 | 10.3 min | 10.4 min | 10.4 min | ±10s |
| 2 | 21.3 min | 21.3 min | 21.3 min | ±20s |
| 3 | 32.2 min | 32.2 min | 32.2 min | ±30s |

**Simple Formula (100 workers):**
```
Time (minutes) ≈ Posts × 10.5 + (Posts - 1) × 0.5
```

### Realistic Time Estimates (200 Accounts)

| Posts | Time (Best) | Time (Typical) | Time (Worst) |
|-------|-------------|----------------|--------------|
| 1 | 20.2 min | 20.3 min | 20.3 min |
| 2 | 42.2 min | 42.3 min | 42.3 min |
| 3 | 64.2 min | 64.3 min | 64.3 min |

**Simple Formula (200 workers):**
```
Time (minutes) ≈ Posts × 21 + (Posts - 1) × 0.5
```

### Performance vs Safety Trade-Off

Current settings prioritize **account safety** over speed:
- Rate limits prevent FloodWaitError
- Inter-reaction delays prevent spam detection
- Worker stagger prevents simultaneous API hits

**Recommendation:** Maintain current rate limits unless you experience no FloodWaitErrors over extended testing period (1000+ reactions). Then consider gradual reduction (6s → 5s → 4s) with monitoring.
