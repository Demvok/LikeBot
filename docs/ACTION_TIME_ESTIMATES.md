# Action Time Scenarios

Updated reaction/comment timing guidance for the current LikeBot runtime: 100 accounts per worker, process-scoped cache, strict global rate limiting, and the new 20–40 s inter-reaction gap baked into `Task.client_worker`.

## Assumptions (current code)

- **Global rate limiter:** `TelegramAPIRateLimiter` is a module-level singleton. It enforces ≥6 s between any two `SendReactionRequest`s and ≥10 s between `send_message` calls across the entire process, not per account.
- **Per-account pacing:** After every post (even if there is only one) `Task.client_worker` sleeps a random 20–40 s (`min_delay_between_reactions` / `max_delay_between_reactions`). Worker start jitter adds another 5–20 s before the very first action.
- **Humaniser model:** `apply_reading_delay` samples WPM from 160–300 (skewed to ~230). Representative lengths stay: fallback text (2–5 s), 60 words (≈16 s), 150 words (≈39–41 s).
- **Warm-up branches:** `_prepare_message_context` always runs `GetFullChannel`, media prefetch, and often (80%) a neighbor fetch; replies fire with a 1–5% chance.
- **Cache scope & TTLs:** The process-scoped `TelegramCache` (max 2,000 entries, per-account cap 400) refreshes TTLs on every hit and deduplicates in-flight fetches. Current TTLs from `config.yaml`:
	- Entities/InputPeer: 24 h / 7 d
	- Messages: 7 d
	- Full channel + discussion metadata: 12 h
- **Reading level:** `delays.humanisation_level = 1`, so reading delay always runs when text is available; fallback delays only trigger on empty captions.

## Reaction timing

| Scenario | Key drivers | Per account (1 post) | Per account (3 posts) | 100 accounts, parallel (1p / 3p) | 100 accounts, sequential (3p) |
| --- | --- | --- | --- | --- | --- |
| **Best** | Cache warm, neighbor + reply branches skipped, empty post (fallback 2 s), pre-action min 3 s, single emoji accepted immediately | ~27 s (≈7 s pipeline + 20 s enforced gap) | ~81 s | ≈11 min / ≈33 min (600 or 1,800 `SendReactionRequest`s × 6 s + start/finish jitter) | ~8,100 s (~135 min) |
| **Typical** | Neighbor fetch hits (adds ≈0.7 s jitter + 1 RPC), 60-word post (≈16 s reading), pre-action avg 5.5 s, 5–6 RPCs, average 30 s post gap | ~54 s | ~162 s | ≈12 min / ≈35 min (queue stays saturated, palette succeeds on first try) | ~16,200 s (~270 min) |
| **Worst** | Cold start for the first actor on a channel, neighbor + replies + media prefetch all fire, 150-word post (≈41 s reading), palette tries four emojis (3 extra limiter waits), 11 RPCs + 40 s post gap | ~112 s | ~336 s | ≈40 min / ≈120 min (400 / 1,200 reaction attempts when everyone retries 3 emojis) | ~33,600 s (~560 min) |

**How to read the table**

- Per-account numbers now include the mandatory 20–40 s `random_delay` that happens after every post. Even a “single post” task pays this delay before the worker exits.
- The parallel column reports total wall-clock for 100 accounts on the same worker host. Because the limiter is global, one `SendReactionRequest` is allowed every 6 s, so throughput is bounded by `#reactions × 6 s` regardless of cache state. Additional emoji retries consume additional 6 s slots.
- Sequential mode imagines processing accounts one-by-one. It multiplies the per-account “3 posts” value by 100, illustrating why sequential fallbacks are unacceptable.

**Call counts per account**

- **Best case (everything already cached, optional branches skipped):** 5 uncached RPCs → `GetFullChannel` warm-up, media prefetch, `GetMessagesViews`, `GetMessageReactionsList`, `SendReaction`.
- **Typical case:** add one neighbor fetch so 6 calls.
- **Worst case:** cache misses + replies branch raise that to 11 `functions.messages/*` + up to 4 `SendReactionRequest`s if the palette keeps failing.
- Since cache TTLs are ≥12 h (24 h for entities, 7 d for messages/input peers) and hits refresh TTLs, only the first account touching a channel/post in a multi-day window pays the cacheable calls. Everyone else awaits the same in-flight Future.

## Comment timing (current limitations)

- LikeBot already implements `_comment`/`comment`, but `Task.client_worker` still emits a “Comment actions are not implemented yet” warning. The table below therefore describes *per-account* timings when `Client.comment()` is called directly (scripts/tests), not via the task orchestrator.
- Components: `GetMessagesViews` → reading delay → `GetDiscussionMessageRequest` → anti-spam delay (1–3 s) → `rate_limiter.wait_if_needed('send_message')` (global 10 s) → `client.send_message`.

| Scenario | Key drivers | Per account (1 post) | Notes |
| --- | --- | --- | --- |
| **Best** | Empty/very short post (fallback 2 s), anti-spam min 1 s, first send_message in >10 s window | ~13 s (2 s read + 1 s anti-spam + 10 s limiter) | Warm caches skip entity/message fetch; throughput is still capped at one comment every 10 s globally. |
| **Typical** | 60-word post (≈16 s reading), anti-spam avg 2 s | ~28 s | Reading dominates; limiter rarely adds extra wait because the human delay already exceeds 10 s. |
| **Worst** | Rapid-fire short comments after another account just sent (limiter fully triggers) | ~41 s | Short content (<5 s to read) hits the 10 s limiter head-on; additional retries would add another 10 s each. |

## Practical implications for 100-account batches

1. **Global limiter first, per-account delay second.** Expect ≈10–11 min per post for 100 accounts because 600 reactions × 6 s dominate. The 20–40 s per-account gap mainly affects sequential fallbacks and single-account runs.
2. **Emoji retries have multiplicative cost.** A single palette miss consumes another global 6 s slot *and* forces the account to sit idle while still holding its inter-reaction delay afterwards. Keep palettes tightly curated.
3. **Caches are effectively long-lived.** With entity/input-peer TTLs at 24 h/7 d and messages at 7 d (with refresh-on-hit), sequential waves within a campaign reuse the same objects. Missing caches now only happens on first-touch channels or after explicit invalidation.
4. **Message TTL is no longer the bottleneck.** The earlier 60 s TTL risk is gone; `cache.message_ttl = 604800` s. Sequential batches spanning minutes or even hours still reuse cached posts, so API pressure stays low.
5. **Cold start penalties are tiny.** Even if the very first account pays the cache miss, in-flight dedup ensures the remaining 99 accounts simply await that Future, so the wall-clock impact is under one RPC round-trip (~0.35 s).
