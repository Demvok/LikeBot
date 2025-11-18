# Subscription Checks and Channel Data Management

## Overview

This document describes the new subscription checking and channel data management features added to the `Client` class in `agent.py`. These changes minimize Telegram API calls and add safety checks to prevent account bans.

## Key Changes

### 1. Channel Data Fetching (`_get_or_fetch_channel_data`)

**Purpose**: Efficiently get channel metadata from database or Telegram API when a chat_id is known.

**How it works**:
1. First checks if channel exists in database
2. If found, returns existing Channel object (no API call)
3. If not found, fetches channel data from Telegram using provided entity (if available) or fetching it
4. Extracts full channel information (name, privacy, reactions, discussion group)
5. Saves to database for future use
6. Returns Channel object

**API Call Optimization**:
- Reuses entity object if already fetched by caller (avoids redundant `get_entity` call)
- Only makes `GetFullChannelRequest` for new channels not in database
- Caches result in database for future lookups

**Usage**:
```python
# Entity is optional - will reuse if provided
channel = await client._get_or_fetch_channel_data(chat_id, entity=entity)
```

### 2. Subscription Checking (`_check_subscription`)

**Purpose**: Verify if account is subscribed to a specific channel.

**How it works**:
- Checks account's `subscribed_to` list for the chat_id
- Returns `True` if subscribed, `False` otherwise
- No API calls - uses cached subscription data

**Usage**:
```python
is_subscribed = await client._check_subscription(chat_id)
if not is_subscribed:
    # Handle unsubscribed case
```

### 3. Enhanced Reaction Logic (`_react`)

**Purpose**: Add safety checks and warnings for reactions on unsubscribed channels.

**Behavior**:
- **Subscribed to channel**: Proceeds normally (no warnings)
- **Not subscribed to channel**: 
  - ⚠️  Logs WARNING about ban risk
  - Proceeds with reaction (user's choice, but warned)

**Warning Message**:
```
⚠️  DANGER: Account {phone} is NOT subscribed to channel {chat_id}.
Reacting to posts from unsubscribed channels significantly increases ban risk.
Telegram may flag this as spam behavior.
```

**Rationale**: 
- Reactions from unsubscribed accounts can be flagged as spam
- Warning allows user to make informed decision
- Still permits the action (user may have legitimate reasons)

### 4. Enhanced Comment Logic (`_comment`)

**Purpose**: Enforce subscription requirements for commenting based on channel privacy and discussion group settings.

**Behavior Matrix**:

| Channel Status | Subscribed to Channel | Subscribed to Discussion | Result |
|----------------|----------------------|--------------------------|--------|
| Private | ✓ Yes | N/A | ✓ Proceed |
| Private | ❌ No | N/A | ❌ Error: Cannot comment on private channel |
| Public + Discussion | ✓ Yes | N/A | ✓ Proceed |
| Public + Discussion | ❌ No | ✓ Yes | ✓ Proceed (with info log) |
| Public + Discussion | ❌ No | ❌ No | ❌ Error: Must subscribe to discussion |
| Public + No Discussion | ✓ Yes | N/A | ✓ Proceed |
| Public + No Discussion | ❌ No | N/A | ⚠️  Warning, attempt anyway |

**Error Messages**:

*Private channel, not subscribed*:
```
Cannot comment on private channel {chat_id}: account {phone} is not subscribed to this channel.
Private channels require subscription to comment.
```

*Public channel, not subscribed to discussion group*:
```
Cannot comment on channel {chat_id}: account {phone} is not subscribed to the discussion group (chat_id: {discussion_id}).
You must subscribe to the discussion group to comment on posts from unsubscribed channels.
```

**Rationale**:
- Private channels absolutely require subscription to comment
- Public channels with discussion groups require discussion group subscription (comments go there)
- Prevents "access denied" errors from Telegram

### 5. Updated Action Methods

All action methods now follow this pattern:

```python
async def react(self, message_link: str):
    # 1. Extract chat_id, message_id, entity from link
    chat_id, message_id, entity = await self.get_message_ids(message_link)
    
    # 2. Get or fetch entity (if not already provided)
    if entity is None:
        entity = await self.get_entity_cached(chat_id)
    
    # 3. Get or fetch channel data (reuses entity to minimize API calls)
    channel = await self._get_or_fetch_channel_data(chat_id, entity=entity)
    
    # 4. Fetch message
    message = await self.client.get_messages(entity, ids=message_id)
    
    # 5. Perform action with channel context
    await self._react(message, entity, channel=channel)
```

**Methods updated**:
- `react()` - Adds subscription warning
- `comment()` - Adds subscription enforcement
- `undo_reaction()` - Fetches channel data for consistency
- `undo_comment()` - Fetches channel data for consistency

## API Call Optimization Strategy

### Before Changes
```
react(message_link):
  └─ get_message_ids(link)
      └─ get_entity_cached(username)  [API CALL 1]
  └─ get_entity_cached(chat_id)      [API CALL 2 - duplicate if username]
  └─ get_messages(...)                [API CALL 3]
  └─ _react()
      └─ GetMessagesViewsRequest      [API CALL 4]
      └─ SendReactionRequest          [API CALL 5]

Total: ~5 API calls (with potential duplicate entity fetch)
```

### After Changes
```
react(message_link):
  └─ get_message_ids(link)
      └─ get_entity_cached(username)       [API CALL 1]
      └─ returns entity (reused!)
  └─ _get_or_fetch_channel_data(chat_id, entity=entity)
      └─ DB lookup (no API call if exists)
      └─ OR GetFullChannelRequest         [API CALL 2 - only for new channels]
  └─ get_messages(...)                     [API CALL 3]
  └─ _react()
      └─ _check_subscription (no API call)
      └─ GetMessagesViewsRequest           [API CALL 4]
      └─ SendReactionRequest               [API CALL 5]

Total: ~5 API calls for new channels, ~4 for known channels
Plus: Entity reuse eliminates duplicate fetches
Plus: Channel data cached in DB for all future operations
```

### Key Optimizations
1. **Entity Reuse**: `get_message_ids()` returns entity, which is reused by `_get_or_fetch_channel_data()`
2. **Database Caching**: Channel data is fetched once and stored in DB
3. **Subscription Cache**: Account's `subscribed_to` list is checked locally (no API call)
4. **Smart Fetching**: Only fetches full channel details for channels not in database

## Error Handling

### ValueError Exceptions

**Private channel, not subscribed (comment)**:
```python
raise ValueError(
    f"Cannot comment on private channel {chat_id}: "
    f"account {phone} is not subscribed to this channel. "
    f"Private channels require subscription to comment."
)
```

**Public channel, not subscribed to discussion group (comment)**:
```python
raise ValueError(
    f"Cannot comment on channel {chat_id}: "
    f"account {phone} is not subscribed to the discussion group (chat_id: {discussion_id}). "
    f"You must subscribe to the discussion group to comment on posts from unsubscribed channels."
)
```

### Warning Logs

**Reaction on unsubscribed channel**:
```python
logger.warning(
    f"⚠️  DANGER: Account {phone} is NOT subscribed to channel {chat_id}. "
    f"Reacting to posts from unsubscribed channels significantly increases ban risk. "
    f"Telegram may flag this as spam behavior."
)
```

**Comment on public channel without discussion group info**:
```python
logger.warning(
    f"Account {phone} is not subscribed to channel {chat_id}. "
    f"No discussion group info available. Attempting to comment anyway."
)
```

## Testing

Run the test script to verify functionality:

```bash
python test_subscription_checks.py
```

The test script verifies:
1. Channel data is fetched correctly from DB or Telegram
2. Subscription checks work properly
3. Reaction logic includes warnings for unsubscribed channels
4. Comment logic enforces subscription requirements

## Migration Notes

### Existing Code Compatibility

All changes are backward compatible:
- New parameters are optional with default values
- Existing calls to `react()`, `comment()`, etc. work without modification
- Channel data is fetched automatically when needed

### Database Updates

No schema changes required:
- Uses existing `channels` collection
- Uses existing `subscribed_to` field in accounts
- Channel records are created automatically as needed

## Best Practices

### For Account Safety

1. **Always sync subscriptions before tasks**:
   ```python
   await client.fetch_and_update_subscribed_channels()
   ```

2. **Monitor warnings**: Watch logs for "DANGER" warnings about unsubscribed reactions

3. **Subscribe before interacting**: Minimize ban risk by subscribing to channels before reacting/commenting

### For Performance

1. **Batch operations**: The system automatically caches channel data, so repeated operations on the same channel are efficient

2. **Entity reuse**: When calling multiple actions on the same channel, the entity is cached and reused

3. **Database lookups**: Channel data lookups are fast (indexed by chat_id)

## Future Improvements

Potential enhancements:
1. Automatic subscription before first interaction (configurable)
2. Subscription verification via Telegram API (if cached data is stale)
3. Rate limiting for subscription checks
4. Analytics on subscription patterns vs ban rates
