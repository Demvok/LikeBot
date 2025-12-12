# Telegram API Call & Cache Analysis

This document summarizes how the LikeBot client hits the Telegram API during core actions (reactions and comments), which calls are suppressed by caches, and how behaviour changes when many accounts act on the same posts for a long period.

## Cache layers that reduce API usage

| Layer | Data types | TTL / persistence | Scope | Effect on calls |
| --- | --- | --- | --- | --- |
| `TelegramCache.get_entity` | Channel/user entities, `InputPeer` wrappers | 24 h for entities, 7 d for input peers (TTL refreshes on every hit) | Process (`scope = process`, max ≈2,000 entries) | First account to touch a channel fetches it once; everyone else reuses the entity/input peer for an entire day (or indefinitely while active). |
| `TelegramCache.get_message` | `GetMessages` payloads | 7 d | Process | Posts are fetched once per channel/post combo per week; sequential batches within minutes or hours reuse the same message body. |
| `TelegramCache.get_full_channel` | `GetFullChannel` payload used by `ChannelDataMixin` | 12 h | Process | Warm-up cost is paid twice a day at most; DB persistence plus TTL refresh keeps settings hot. |
| Channel / post collections in DB | Channel metadata, URL aliases, validated posts | Persistent | Shared DB | Once a channel/post has been seen, further resolutions hit Mongo without calling Telegram, except for the uncached warm-up snapshot. |
| In-flight dedup | All of the above | During call | Process | If dozens of accounts ask for the same entity at once, only the first hits Telegram; the rest await the same Future and see identical data. |

Warm caches dramatically lower per-account API counts, but some calls are intentionally uncached (e.g., view increments) to mimic human behaviour.

## Reaction flow: per-stage call budget

| Stage | Telethon API | Cacheable? | Notes | Calls (cold start) | Calls (warm cache) |
| --- | --- | --- | --- | --- | --- |
| Link → IDs (`get_message_ids`) | `GetEntity` (via `client.get_entity`) | ✅ (24 h) | Only when DB lacks the channel/post. Post-validation or alias hits skip this entirely. | 1 | 0 |
| Message fetch (`get_message_cached`) | `GetMessages` | ✅ (7 d) | First account per post pays once per week; hits refresh TTL so active channels never expire. | 1 | 0 |
| Channel metadata (`_get_or_fetch_channel_data`) | `functions.channels.GetFullChannelRequest` | ✅ (12 h) | Only hits when DB lacks the channel, then refreshes twice per day at most. | 1 | 0 |
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
| Link → IDs | `GetEntity` | ✅ (24 h) | Same mechanics as reactions. | 1 | 0 |
| Message fetch | `GetMessages` | ✅ (7 d) | Reused unless nobody touches the post for a week. | 1 | 0 |
| Channel metadata (rare) | `GetFullChannel` | ✅ (12 h) | Only when DB lacks channel or TTL expires. | 1 | 0 |
| View increment | `GetMessagesViewsRequest` | ❌ | Always done to look human. | 1 | 1 |
| Discussion lookup | `messages.GetDiscussionMessageRequest` | ❌ | Required to reach the linked chat + reply id. | 1 | 1 |
| Comment send | `client.send_message` → `messages.SendMessage` | ❌ | Rate-limited (10 s) per account. | 1 | 1 |

Totals:
- **Best case:** 3 calls per comment.
- **Typical case:** still 3 (view + discussion + send) because caches usually hit.
- **Worst case (cold + private channel):** 6–7 calls (entity + message + channel metadata + standard 3).

## Multi-account, long-running scenarios

### Cache behaviour over time

- **Process scope + refresh-on-hit** keeps hot data resident indefinitely. The shared cache (max 2,000 entries per process, per-account cap 400) refreshes each entry’s TTL whenever it is touched.
- **Entity/InputPeer TTLs:** entities live for 24 h; input peers for 7 d. Repeated runs during a campaign therefore never re-fetch identities unless the bot sleeps for a day or more.
- **Message TTL (7 d):** this used to be the shortest window; now a post has to be idle for a full week before the cache expires. Sequential waves that take minutes or hours stay on the cached payload.
- **Channel metadata TTL (12 h) + Mongo persistence:** once a channel is inserted into the database its structural data is retrieved at most twice per day; the warm-up `GetFullChannel` snapshot in `_prepare_message_context` still executes intentionally every time to mimic organic behavior.

### Example: 100 accounts reacting to the same post

| Scenario | Cold start (first-ever time) | Warm batch (<24 h since last touch) | Long gap (>7 d or explicit eviction) |
| --- | --- | --- | --- |
| Cacheable calls (entity, input peer, message, channel metadata) | ~4 calls total (paid once, deduped for all waiters) | 0 new calls (TTL refreshed on every hit) | Repeated per TTL: entities every 24 h, full channel every 12 h, messages every 7 d |
| Uncached per-account calls | 5–7 each | 5–7 each | 5–7 each |
| Total calls/post | ≈ `100 × 5 + 4` = **504–704** | Same **500** calls no matter how long the batch takes | Adds back 4 cacheable calls only when TTLs truly expire |
| 3 posts/task | Multiply by 3 → **1512–2112** calls | **1500** calls | `1500` + the occasional TTL refresh |

The table assumes every account finishes its per-post work well inside the message TTL. If the fleet executes sequentially and needs ~1,000 seconds to march through 100 accounts, expect message-cache misses for the last ~40% of clients, adding roughly 40 extra `GetMessages` calls per post.

### Example: 100 accounts leaving one comment per post

- **Best / typical:** 3 uncached calls × 100 = **300** API hits per post; caches suppress all setup calls after the first account finishes.
- **Worst:** cold start adds up to ~100 × 3 + 3 cacheable = **303** calls, plus rare retries if Telegram rejects a message.

### Takeaways

1. **Process-scoped cache + DB aliasing eliminates 60–70% of API calls after the first wave.** Only humanisation steps (`GetMessagesViews`, reaction whitelist, warm-up snapshot) remain per account.
2. **Message TTL is no longer the limiting factor.** With a 7-day window (and refresh-on-hit), sequential fleets and multi-hour batches keep reusing the initial `GetMessages` payload. Explicit invalidation or week-long gaps are now the only reasons to re-fetch.
3. **Neighbor/reply warm-ups still dominate variability.** In pathological runs where every random branch fires, the uncached call count per account almost doubles, so monitoring cache stats (`TelegramCache.get_stats()`) helps confirm whether this is due to randomness or persisting random branches.
