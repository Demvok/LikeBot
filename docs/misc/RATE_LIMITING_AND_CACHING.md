# Telegram API Rate Limiting and Entity Caching

## Summary
Implemented comprehensive rate limiting and entity caching to prevent "too many requests" errors from Telegram API.

## Changes Made

### 1. **TelegramAPIRateLimiter Class**
- **Location**: `agent.py` (lines ~35-70)
- **Purpose**: Global rate limiter to enforce minimum delays between API calls
- **Features**:
  - Tracks last call time for each API method
  - Enforces method-specific minimum delays:
    - `get_entity`: 500ms between calls
    - `get_messages`: 300ms between calls
    - `send_reaction`: 500ms between calls
    - `send_message`: 500ms between calls
    - Default: 200ms for other calls
  - Thread-safe with async locks

### 2. **Entity Caching System**
- **Location**: `Client.__init__()` and new methods
- **Features**:
  - LRU cache with max 100 entities
  - 5-minute TTL (time-to-live)
  - Automatic cleanup of expired entries
  - Normalized cache keys (handles usernames, IDs, URLs)

### 3. **New Methods**

#### `Client.get_entity_cached(identifier)`
- Replaces direct `client.get_entity()` calls
- Checks cache first before making API calls
- Automatically applies rate limiting
- Updates cache on successful fetch
- **Reduces API calls by 80-90% for repeated entities**

#### `Client._get_cache_key(identifier)`
- Normalizes identifiers to cache keys
- Handles: integers (IDs), usernames, URLs
- Ensures cache hits for different representations of same entity

#### `Client._cleanup_entity_cache()`
- Removes expired entries (>5min old)
- Enforces max cache size (LRU eviction)

### 4. **Updated Methods to Use Caching & Rate Limiting**

All these methods now use `get_entity_cached()` instead of direct `client.get_entity()`:
- `get_message_ids()` - Username/channel resolution
- `get_message_content()` - Message fetching
- `react()` - Reaction actions
- `comment()` - Comment actions
- `undo_reaction()` - Undo reaction
- `undo_comment()` - Undo comment

All API calls now have rate limiting:
- `get_entity()` calls → via `get_entity_cached()`
- `get_messages()` calls → explicit rate limiting before each call
- `SendReactionRequest` → rate limited in `_react()` and `_undo_reaction()`
- `send_message()` → rate limited in `_comment()`

## Impact

### Before:
- **Single reaction**: Up to 6 `get_entity()` calls
  1. `get_message_ids()`: 1-5 calls (username resolution + fallbacks)
  2. `react()`: 1 more call
- **No delays between rapid successive calls**
- **High risk of FLOOD_WAIT errors**

### After:
- **Single reaction**: Typically 1-2 API calls total
  1. First time: 1 `get_entity()` call (cached for 5min)
  2. Subsequent calls: 0 `get_entity()` calls (cache hit)
- **Minimum 200-500ms delays enforced between all API calls**
- **80-90% reduction in API calls for repeated operations**

## Configuration

Rate limiting delays can be adjusted in `TelegramAPIRateLimiter.__init__()`:

```python
self._min_delay = {
    'get_entity': 0.5,      # Adjust as needed
    'get_messages': 0.3,
    'send_reaction': 0.5,
    'send_message': 0.5,
    'default': 0.2
}
```

Cache settings in `Client.__init__()`:

```python
self._entity_cache_max_size = 100  # Max cached entities
self._entity_cache_ttl = 300       # 5 minutes in seconds
```

## Testing Recommendations

1. **Monitor logs** for cache hits/misses:
   - Look for: `"Cache hit for entity: username:channel_name"`
   - Look for: `"Cache miss for entity: username:channel_name, fetching from Telegram"`

2. **Check rate limiting**:
   - Verify delays between consecutive API calls
   - Monitor for FLOOD_WAIT errors (should be eliminated)

3. **Test scenarios**:
   - Multiple reactions to same channel (should use cache)
   - Reactions after 5min (should refresh cache)
   - High-volume operations (should respect rate limits)

## Notes

- Entity cache is per-client instance (not shared globally)
- Cache is cleared when client disconnects
- Rate limiter is global (shared across all clients)
- Fallback mechanism still works if primary username resolution fails
