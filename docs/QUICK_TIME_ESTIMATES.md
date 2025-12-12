# Quick Time Estimates
**TL;DR:** How long will my task take?

**Updated:** December 12, 2025 (reflects 20–40 s inter-reaction gap + long-lived caches)

---

## Simple Formula

```
Time (minutes) = (Workers × Posts × 0.1) + (Posts × 0.5)
```

**Explanation:**
- **0.1 min per reaction** (global 6 second `SendReactionRequest` limit)
- **0.5 min overhead per post** (20–40 s inter-reaction gap + reading + pre-action jitter)

---

## Instant Lookup Table

### 100 Accounts

| Posts | Time | Range |
|-------|------|-------|
| 1 | **10 min** | 10-11 min |
| 2 | **21 min** | 21-22 min |
| 3 | **32 min** | 32-33 min |
| 5 | **54 min** | 54-55 min |
| 10 | **109 min** | 109-110 min |

### 200 Accounts

| Posts | Time | Range |
|-------|------|-------|
| 1 | **20 min** | 20-21 min |
| 2 | **42 min** | 42-43 min |
| 3 | **64 min** | 64-65 min |
| 5 | **108 min** | 108-110 min |
| 10 | **218 min** | 218-220 min |

### 50 Accounts

| Posts | Time | Range |
|-------|------|-------|
| 1 | **5 min** | 5-6 min |
| 2 | **11 min** | 11-12 min |
| 3 | **16 min** | 16-17 min |
| 5 | **27 min** | 27-28 min |
| 10 | **54 min** | 54-55 min |

---

## What Takes Time?

### Per Account, Per Post

| Component | Time | Skippable? |
|-----------|------|------------|
| **SendReactionRequest** | **6 s global throttle** | ❌ Never (process-wide `TelegramAPIRateLimiter`) |
| Inter-reaction delay | 20–40 s per post | ❌ Never (enforced in `Task.client_worker`, even for a single post) |
| Pre-action delay | 3–8 s | ❌ Never (humanization) |
| Reading delay | 2–41 s | ❌ Never (depends on message length via `estimate_reading_time`) |
| Worker start jitter | 5–20 s (per worker run) | ❌ No (one-time per account at run start) |
| Warm-up snapshot + media prefetch | ≈1 s total | ❌ No (GetFullChannel + GetMessages/WebPreview) |
| Reaction whitelist fetch | ≈0.35 s | ❌ No (skipped only on broadcast channels) |
| `GetMessagesViews` | <1 s | ❌ No (organic view increment) |
| `get_entity` | ≈0.35 s once per 24 h | ✅ Cached (process scope + refresh-on-hit) |
| `get_message` | ≈0.35 s once per 7 d | ✅ Cached (process scope + refresh-on-hit) |

**Per-account wall time (excluding the global queue):**
- **Cold start:** ~55–65 s for short posts, up to ~110 s for 150-word posts.
- **Warm cache:** ~27–54 s (same as above minus the first-touch cache fetch, which is <1 s and shared via in-flight dedup).

---

## Why So Long? (Bottleneck Analysis)

### 100 Accounts, 1 Post ≈ 10 Minutes

```
Reaction Queue Bottleneck:
  100 workers need to react
  Rate limit: 6 seconds per reaction
  Sequential queue: 100 × 6s = 600s = 10 minutes
  
Everything else: ~30s (3% of total time)
```

**Breakdown:**
- **95%** of time = Waiting in reaction queue
- **3%** of time = Delays (pre-action, reading)
- **2%** of time = API lookups (entity, message)

### Why Does Cache Matter So Little?

With 100 workers:
- **First worker:** Triggers `get_entity`, `get_message`, and `GetFullChannel` once (≈1 s total, and in-flight dedup shares it with everybody else).
- **Workers 2–100:** Immediately await the same Future because the cache scope is `process` and TTLs are ≥12 h (entities 24 h, messages 7 d, input peers 7 d).
- **Average penalty:** <1 s ÷ 100 ≈ **0.01 s per worker**.

**Insight:** Cache effectiveness is now almost invisible even for single-account scripts because the TTL refresh-on-hit keeps entries alive for days; only the very first touch after a multi-day pause pays the API cost.

---

## Comparison: Small vs Large Scale

### 1 Account, 10 Posts

```
Time per post: ~55–65 s (short text) or ~110 s (long text)
Total: 9–11 min for short posts, up to ~18 min for very long posts

Cache impact: <1 s after the very first post (TTL = 24 h+/7 d)
```

Single-account runs are dominated by the enforced 20–40 s gap between posts plus reading time; caches only matter if you truly have never seen the channel before.

### 100 Accounts, 1 Post

```
First worker reaches the limiter after ~7 s of prep
Global queue: 100 reactions × 6 s = 600 s
Tail latency: + worker start jitter (≤20 s) + final 20–40 s gap
Total: ~11 min wall clock
```

Even though every account spends ~30–50 s “doing human stuff,” the global limiter keeps total time near `#workers × 6 s`. Cache state changes that by milliseconds at best.

---

## Real-World Examples

### Example 1: Daily Reaction Campaign
**Setup:** 150 accounts react to 2 posts (morning + evening)

**Time:**
```
Formula: (150 × 2 × 0.1) + (2 × 0.5)
       = 30 + 1
       = 31 minutes per run
       = 62 minutes per day (2 runs)
```

**Daily schedule:**
- 9:00 AM: Morning run (31 min) → completes 9:31 AM
- 6:00 PM: Evening run (31 min) → completes 6:31 PM

### Example 2: Bulk Engagement Boost
**Setup:** 200 accounts react to single viral post

**Time:**
```
Formula: (200 × 1 × 0.1) + (1 × 0.5)
       = 20 + 0.5
       = 20.5 minutes
```

**Expected:** 20-21 minutes

### Example 3: Multi-Post Campaign
**Setup:** 100 accounts react to 5 related posts

**Time:**
```
Formula: (100 × 5 × 0.1) + (5 × 0.5)
       = 50 + 2.5
       = 52.5 minutes
```

**Expected:** 53-55 minutes

---

## Scaling Patterns

### Linear Scaling (Workers)

| Workers | 1 Post | 2 Posts | 3 Posts |
|---------|--------|---------|---------|
| 10 | 1.5 min | 3 min | 4.5 min |
| 50 | 5.5 min | 11 min | 16.5 min |
| 100 | 10.5 min | 21 min | 31.5 min |
| 200 | 20.5 min | 41 min | 61.5 min |
| 500 | 50.5 min | 101 min | 151.5 min |

**Pattern:** Each 100 workers adds ~10 minutes per post

### Linear Scaling (Posts)

| Posts | 10 Accts | 50 Accts | 100 Accts | 200 Accts |
|-------|----------|----------|-----------|-----------|
| 1 | 1.5 min | 5.5 min | 10.5 min | 20.5 min |
| 2 | 3 min | 11 min | 21 min | 41 min |
| 3 | 4.5 min | 16.5 min | 31.5 min | 61.5 min |
| 5 | 7.5 min | 27.5 min | 52.5 min | 102.5 min |
| 10 | 15 min | 55 min | 105 min | 205 min |

**Pattern:** Each post adds (Workers × 0.1) + 0.5 minutes

---

## How to Speed Up

### Current Bottleneck: Reaction Rate Limit (6s)

**What if we reduced it?**

| Rate Limit | 100 Accts, 1 Post | Speedup | Risk |
|------------|-------------------|---------|------|
| **6s (current)** | 10.5 min | Baseline | ✅ Safe |
| 5s | 8.8 min | 16% faster | ⚠️ Test first |
| 4s | 7.2 min | 31% faster | ⚠️ Higher risk |
| 3s | 5.5 min | 48% faster | ❌ Very risky |
| 2s | 3.8 min | 64% faster | ❌ Likely banned |

**Recommendation:** Test 5s rate limit on small scale (10-20 accounts) for 1000+ reactions. If no FloodWaitErrors, gradually roll out.

### Other Optimizations (Minimal Gain)

1. **Reduce inter-reaction delay** (20-40s → 10-20s)
   - Gain: ~10s per post (after first)
   - Impact: <2% for typical tasks
   - Risk: Possible spam detection

2. **Reduce reading delay** (disable humanization_level)
   - Gain: ~5s per post
   - Impact: <1% for typical tasks
   - Risk: Less human-like behavior

3. **Cache tuning**
  - Gain: <0.5 s (entity/message TTLs already 24 h / 7 d with refresh-on-hit)
  - Impact: None unless the process restarts daily
  - Risk: None (but also no measurable upside)

**Bottom line:** Reaction rate limit is 95% of the time. Other optimizations are noise.

---

## When Will My Task Finish?

### Quick Estimator

**Your config:**
- Workers: `W`
- Posts: `P`

**Formula:**
```
Minutes = (W × P × 0.1) + (P × 0.5)
```

**Examples:**
```
120 workers, 2 posts:
  = (120 × 2 × 0.1) + (2 × 0.5)
  = 24 + 1
  = 25 minutes

80 workers, 3 posts:
  = (80 × 3 × 0.1) + (3 × 0.5)
  = 24 + 1.5
  = 25.5 minutes

300 workers, 1 post:
  = (300 × 1 × 0.1) + (1 × 0.5)
  = 30 + 0.5
  = 30.5 minutes
```

---

## Best/Typical/Worst Case Summary

### Reality Check

For tasks with **100+ workers**, the difference between best/typical/worst is **negligible**:

| Scenario | 1 Post | 2 Posts | 3 Posts |
|----------|--------|---------|---------|
| Best (full cache) | 10.3 min | 21.3 min | 32.2 min |
| Typical (partial cache) | 10.4 min | 21.3 min | 32.2 min |
| Worst (cold cache) | 10.4 min | 21.3 min | 32.2 min |
| **Difference** | **6 sec** | **10 sec** | **15 sec** |

**Why?** First worker pays the penalty (<1% of total time), rest ride free.

### When Cache Actually Matters

Cache only pops up on the radar when:
- You hit a channel/post for the **first time ever** (no DB alias, no cached entity/input peer yet).
- The process slept long enough for TTLs to expire (≥24 h for entities, ≥7 d for messages/input peers).
- You run ad-hoc scripts without calling `Client.init_standalone_cache()`, forcing every call to bypass caching entirely.

Under normal task execution (process-scoped cache + refresh-on-hit), cache misses amount to <<1% of the total runtime—even for single-account jobs.

---

## Final Answer: Realistic Time Estimates

### 100 Accounts (Most Common)

| Posts | Time | What to Expect |
|-------|------|----------------|
| 1 | **10-11 min** | Quick engagement boost |
| 2 | **21-22 min** | Daily campaign (2 posts) |
| 3 | **32-33 min** | Multi-post engagement |

### 150 Accounts (Heavy Usage)

| Posts | Time | What to Expect |
|-------|------|----------------|
| 1 | **15-16 min** | Single viral post |
| 2 | **31-32 min** | Standard campaign |
| 3 | **47-48 min** | Extended campaign |

### 200 Accounts (Maximum Scale)

| Posts | Time | What to Expect |
|-------|------|----------------|
| 1 | **20-21 min** | Aggressive boost |
| 2 | **41-42 min** | Large campaign |
| 3 | **62-64 min** | ~1 hour commitment |

**Rule of Thumb:** 
- Each 100 accounts ≈ 10 minutes per post
- Each post adds another cycle

**Variability:** ±30 seconds due to random delays (negligible)
