# Quick Time Estimates
**TL;DR:** How long will my task take?

**Updated:** November 30, 2025 (reflects new rate limits: 10s, 6s, 1s)

---

## Simple Formula

```
Time (minutes) = (Workers × Posts × 0.1) + (Posts × 0.5)
```

**Explanation:**
- **0.1 min per reaction** (6 second rate limit)
- **0.5 min overhead per post** (delays, reading, lookups)

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
| **SendReactionRequest** | **6s** | ❌ Never (rate limited) |
| Inter-reaction delay | 30s | ❌ Never (spam prevention) |
| Pre-action delay | 5s | ❌ Never (humanization) |
| Reading delay | 5s | ❌ Never (humanization) |
| get_entity | 10s | ✅ Cached after first worker |
| get_message | 1s | ✅ Cached after first worker |
| GetMessagesViews | <1s | ❌ Never (must increment) |

**Total (Cold Cache):** ~40s per post  
**Total (Warm Cache):** ~18s per post

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
- **First worker:** Fetches entity/message (+11s penalty)
- **Workers 2-100:** Use cached data (0s penalty)
- **Average penalty:** 11s ÷ 100 = **0.11s per worker** (negligible)

**Insight:** Cache effectiveness is **invisible at scale** because only 1% of workers pay the cost.

---

## Comparison: Small vs Large Scale

### 1 Account, 10 Posts

```
Time per post: 40s (cold) or 18s (warm)
Total: 400s (cold) or 180s (warm) ≈ 3-7 minutes

Cache impact: 220s (55% faster with cache)
```

**Caching matters a lot** for single-account tasks.

### 100 Accounts, 1 Post

```
First worker: 40s
Workers 2-100: 18s each
Total: ~628s ≈ 10.5 minutes

Cache impact: 11s (1.7% faster)
```

**Caching barely matters** for multi-account tasks.

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

3. **Increase cache TTLs**
   - Gain: <1s average (already 90%+ hit rate)
   - Impact: Negligible
   - Risk: None

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

Cache makes a **big difference** only for:
- **Single account** tasks (100% of workers pay penalty)
- **Very small tasks** (<10 workers)
- **First time seeing a channel** (no URL alias in DB)

For your use case (100+ accounts), **ignore cache differences**.

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
