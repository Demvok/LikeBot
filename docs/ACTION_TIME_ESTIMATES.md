# Action Time Scenarios

Estimated runtimes for reactions and comments, covering best / typical / worst cases for a fleet of ~100 accounts handling 1–3 posts per task.

## Assumptions

- Average Telegram RPC latency (including Telethon serialization) ≈ **0.35 s** per call.
- Reading delay uses `humaniser.estimate_reading_time` (≈230 wpm skewed distribution). We model three representative message lengths: empty (fallback), 60 words, and 150 words.
- Random warm-up jitters from `ActionsMixin` are summed per stage; probabilistic branches (neighbor posts, replies) appear in the typical/worst scenarios only.
- `rate_limiter.wait_if_needed('send_reaction')` enforces 6 s between `SendReactionRequest`s; `send_message` is limited to 10 s. These cool-downs only add time if the business logic otherwise runs faster than the limiter.
- All 100 accounts run in parallel on the same worker host (as the code already does via `asyncio.gather`). A "sequential" column is included to illustrate the cost if accounts were processed one-by-one.

## Reaction timing

| Scenario | Key drivers | Per account (1 post) | Per account (3 posts) | 100 accounts, parallel | 100 accounts, sequential |
| --- | --- | --- | --- | --- | --- |
| **Best** | Cache warm, neighbor/reply branches skipped, empty message (fallback 2 s), pre-action min 3 s, single emoji succeeds | ~7 s | ~21 s | ~21 s (entire fleet finishes when the slowest account completes 3 posts) | ~2,100 s (~35 min) |
| **Typical** | Neighbor fetch fires (adds 1 RPC + 0.4 s jitter), 60-word post (≈16 s reading), pre-action avg 5.5 s, 5–6 RPCs | ~24 s | ~72 s | ~72 s | ~7,200 s (~2 h) |
| **Worst** | Cold caches, neighbor + replies warm-up, 150-word post (≈41 s reading), 4 emoji attempts (3 extra waits × 6 s), 11 RPCs | ~72 s | ~216 s | ~216 s | ~21,600 s (~6 h) |

Notes:
- Even in the best case the per-reaction pipeline already exceeds the 6 s reaction rate limit, so the limiter rarely stalls additional posts. Extra emoji retries, however, incur 6 s per retry.
- With caches warm the only irreducible calls per account are: `GetFullChannel` (warm-up), media prefetch, `GetMessagesViews`, `GetMessageReactionsList`, and `SendReaction`.

## Comment timing

| Scenario | Key drivers | Per account (1 post) | Per account (3 posts) | 100 accounts, parallel | 100 accounts, sequential |
| --- | --- | --- | --- | --- | --- |
| **Best** | Cache warm, empty/very short post (fallback 2 s), anti-spam min 1 s, initial send not rate-limited | ~4 s | ~12 s | ~12 s | ~1,200 s (~20 min) |
| **Typical** | 60-word post (≈16 s reading), anti-spam avg 2 s, 3 RPCs (views, discussion, send) | ~19 s | ~57 s | ~57 s | ~5,700 s (~95 min) |
| **Worst** | 150-word post (≈41 s reading), anti-spam max 3 s, prior comment <10 s ago so rate limiter forces full 10 s gap (adds ~7 s wait), same 3 RPCs | ~52 s | ~156 s | ~156 s | ~15,600 s (~4.3 h) |

Notes:
- When comments are long, the natural reading delay already exceeds the 10 s message limiter, so no extra wait occurs. The worst case assumes very short content on rapid-fire tasks, making the limiter dominate.
- `_comment` does not run the heavy warm-up sequence, so its runtime variance comes mainly from reading delay and the anti-spam jitter.

## Practical implications for 100-account batches

1. **Parallelism keeps wall-clock dominated by per-account latency.** As long as the orchestrator awaits all accounts concurrently, total task duration is effectively the per-account number in the tables above.
2. **Sequential fallbacks are expensive.** If a safety mode ever processes accounts one-by-one, even the "typical" reaction scenario balloons to ~2 hours for 3 posts, purely due to humanisation delays.
3. **Cache warm-up matters only at the beginning.** The timing tables already assume caches are warm; cold starts add ~1–2 s per reaction (extra RPC + latency) but only for the first account that touches a channel/post in a five-minute window.
4. **Message TTL (60 s) is the main risk for sequential waves.** If orchestration ever becomes mostly sequential, increase `cache.message_ttl` or prefetch posts per shard so that later accounts reuse the initial `GetMessages` response instead of paying the latency again.
