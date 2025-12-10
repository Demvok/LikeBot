# Telegram API Call & Cache Analysis

This document summarizes how the LikeBot client hits the Telegram API during core actions (reactions and comments), which calls are suppressed by caches, and how behaviour changes when many accounts act on the same posts for a long period.

## Cache layers that reduce API usage

| Layer | Data types | TTL / persistence | Scope | Effect on calls |
| --- | --- | --- | --- | --- |
| `TelegramCache.get_entity` | Channel/user entities, `InputPeer` wrappers | 300 s | Process (per `config.cache.scope`) | First account to touch a channel fetches it once; the other ~99 accounts reuse the entity + input peer for 5 minutes. |
| `TelegramCache.get_message` | `GetMessages` payloads | 60 s | Process | Message contents are fetched once per post, provided the rest of the fleet touches the post within a minute. |
| `TelegramCache.get_full_channel` | `GetFullChannel` payload used by `ChannelDataMixin` | 600 s | Process | Paying the cost of a channel lookup populates discussion/reaction settings for ten minutes. |
| Channel / post collections in DB | Channel metadata, URL aliases, validated posts | Persistent | Shared DB | Once a channel/post has been seen, most future link resolutions avoid Telegram entirely and return cached IDs straight from Mongo. |
| In-flight dedup | All of the above | During call | Process | If 30 accounts ask for the same entity simultaneously, only the first actually calls Telegram; the rest await the same Future. |

Warm caches dramatically lower per-account API counts, but some calls are intentionally uncached (e.g., view increments) to mimic human behaviour.

## Reaction flow: per-stage call budget

| Stage | Telethon API | Cacheable? | Notes | Calls (cold start) | Calls (warm cache) |
| --- | --- | --- | --- | --- | --- |
| Link → IDs (`get_message_ids`) | `GetEntity` (via `client.get_entity`) | ✅ (300 s) | Only when DB lacks the channel/post. Post-validation or alias hits skip this entirely. | 1 | 0 |
| Message fetch (`get_message_cached`) | `GetMessages` | ✅ (60 s) | First account per post pays; others reuse for 1 minute. | 1 | 0 |
| Channel metadata (`_get_or_fetch_channel_data`) | `functions.channels.GetFullChannelRequest` | ✅ (600 s) | Only hits when DB lacks the channel. | 1 | 0 |
| Warm-up snapshot | `functions.channels.GetFullChannelRequest` | ❌ | Always executed to “touch” the channel before acting. | 1 | 1 |
| Neighbor prefetch (80% probability) | `messages.GetHistoryRequest` **or** `messages.GetMessagesRequest` | ❌ | Randomized humanisation step; skipped ~20% of the time. | 1 | 0–1 |
| Media prep | `messages.GetMessagesRequest` **or** `messages.GetWebPagePreviewRequest` | ❌ | Always executed, but choice of endpoint varies. | 1 | 1 |
| Replies exploration (1–5% probability) | `messages.GetDiscussionMessageRequest` + `messages.GetRepliesRequest` | ❌ | Rare branch to simulate curiosity. | 2 | 0–2 |
| View increment | `GetMessagesViewsRequest` | ❌ | Always per account to look organic. | 1 | 1 |
| Reaction whitelist | `messages.GetMessageReactionsListRequest` | ❌ | Always per account (depends on current reaction mix). | 1 | 1 |
| Reaction send | `SendReactionRequest` | ❌ | Rate-limited (6 s) per account; palette retries multiply this count. | 1–N | 1–N |

**Per-account totals**

- **Best case (everything already cached, neighbor/replies skipped, first emoji works):** 5 API calls.
- **Typical case (cache warm, neighbor fetch happens, message cache hits, no replies):** 6 calls.
- **Worst case (DB empty, caches cold, neighbor fetch + replies trigger, palette tries 3 emojis):** 11 + extra 2 `SendReactionRequest` retries (each retry spaced by the 6 s limiter).

## Comment flow: per-stage call budget

| Stage | Telethon API | Cacheable? | Notes | Calls (cold start) | Calls (warm cache) |
| --- | --- | --- | --- | --- | --- |
| Link → IDs | `GetEntity` | ✅ (300 s) | Same mechanics as reactions. | 1 | 0 |
| Message fetch | `GetMessages` | ✅ (60 s) | Reused if <60 s. | 1 | 0 |
| Channel metadata (rare) | `GetFullChannel` | ✅ (600 s) | Only when DB lacks channel. | 1 | 0 |
| View increment | `GetMessagesViewsRequest` | ❌ | Always done to look human. | 1 | 1 |
| Discussion lookup | `messages.GetDiscussionMessageRequest` | ❌ | Required to reach the linked chat + reply id. | 1 | 1 |
| Comment send | `client.send_message` → `messages.SendMessage` | ❌ | Rate-limited (10 s) per account. | 1 | 1 |

Totals:
- **Best case:** 3 calls per comment.
- **Typical case:** still 3 (view + discussion + send) because caches usually hit.
- **Worst case (cold + private channel):** 6–7 calls (entity + message + channel metadata + standard 3).

## Multi-account, long-running scenarios

### Cache behaviour over time

- **Process scope** means all 100 accounts share one `TelegramCache`, so once a channel is “touched” the entity/input peer/message objects are global until their TTL expires.
- **Entity/InputPeer TTL (300 s):** as long as every account hits the same channel within five minutes, only the very first one calls `GetEntity`. Nightly jobs that revisit the same channels every few hours will re-hit Telegram once per channel per run.
- **Message TTL (60 s):** this is the tightest window. If 100 accounts react sequentially and need ~10 seconds each, the tail of the batch will miss the cache and re-fetch the message. Running accounts concurrently (or increasing the TTL) keeps this at a single call per post.
- **Channel metadata TTL (600 s) plus database persistence** keeps most `GetFullChannel` calls at onboarding-time only. After the first success the data is in Mongo permanently; future sessions straight-up read from DB and skip Telegram entirely, except for the warm-up snapshot (which is intentionally uncached).

### Example: 100 accounts reacting to the same post

| Scenario | Cold start (first-ever time) | Warm, concurrent batch (<60 s window) | Long gap (>5 min between batches) |
| --- | --- | --- | --- |
| Cacheable calls (entity, input peer, message, channel metadata) | ~4 calls total (paid once) | 0 new calls | Repeated once per gap |
| Uncached per-account calls | 5–7 each | 5–7 each | 5–7 each |
| Total calls/post | ≈ `100 × 5 + 4` = **504–704** | ≈ `100 × 5` = **500** | Same as warm batch, but pay the cacheable 4 calls again |
| 3 posts/task | Multiply by 3 → **1512–2112** calls | **1500** calls | `1500 + occasional refetches` |

The table assumes every account finishes its per-post work well inside the message TTL. If the fleet executes sequentially and needs ~1,000 seconds to march through 100 accounts, expect message-cache misses for the last ~40% of clients, adding roughly 40 extra `GetMessages` calls per post.

### Example: 100 accounts leaving one comment per post

- **Best / typical:** 3 uncached calls × 100 = **300** API hits per post; caches suppress all setup calls after the first account finishes.
- **Worst:** cold start adds up to ~100 × 3 + 3 cacheable = **303** calls, plus rare retries if Telegram rejects a message.

### Takeaways

1. **Process-scoped cache + DB aliasing eliminates 60–70% of API calls after the first wave.** Only humanisation steps (`GetMessagesViews`, reaction whitelist, warm-up snapshot) remain per account.
2. **Message TTL is the limiting factor** for very long runs; consider bumping `cache.message_ttl` if sequential fleets routinely exceed 60 seconds per post.
3. **Neighbor/reply warm-ups dominate variability.** In pathological runs where every random branch fires, the uncached call count per account almost doubles, so monitoring cache stats (`TelegramCache.get_stats()`) helps confirm whether this is due to randomness or cache expiry.
