# New API Endpoints Summary

This document summarizes the new endpoints added to the LikeBot API for enhanced management capabilities.

## User Management Endpoints (Admin Only)

All user management endpoints require admin authentication via `Depends(get_current_admin_user)`.

### GET /users
**Summary:** Get all users  
**Tags:** User Management  
**Authentication:** Admin only  
**Description:** Returns list of all users with their roles, verification status, and timestamps. Password hashes are excluded for security.  
**Response:** List of user objects (without password_hash)

### PUT /users/{username}/role
**Summary:** Update user role  
**Tags:** User Management  
**Authentication:** Admin only  
**Parameters:**
- `username` (path): Username to update
- `role` (query): New role to assign (admin, user, guest)

**Restrictions:**
- Cannot change your own role
- Requires another admin to change admin roles

**Response:**
```json
{
  "message": "User {username} role updated to {role}",
  "username": "string",
  "new_role": "string"
}
```

### PUT /users/{username}/verify
**Summary:** Update user verification status  
**Tags:** User Management  
**Authentication:** Admin only  
**Parameters:**
- `username` (path): Username to update
- `is_verified` (query): Verification status (boolean)

**Description:** Verified users can access the API; unverified users are blocked after login.

**Response:**
```json
{
  "message": "User {username} verification status updated",
  "username": "string",
  "is_verified": true
}
```

### DELETE /users/{username}
**Summary:** Delete user  
**Tags:** User Management  
**Authentication:** Admin only  
**Parameters:**
- `username` (path): Username to delete

**Restrictions:**
- Cannot delete yourself
- Cannot delete the last verified admin user

**Response:**
```json
{
  "message": "User {username} deleted successfully",
  "username": "string"
}
```

---

## Proxy Management Endpoints

All proxy endpoints require authentication via `Depends(get_current_user)`.

### GET /proxies
**Summary:** Get all proxies  
**Tags:** Proxies  
**Authentication:** Required  
**Query Parameters:**
- `proxy_name` (optional): Filter by specific proxy name
- `active_only` (optional): Filter by active status

**Description:** Returns proxy configurations with decrypted passwords for authenticated users.  
**Response:** List of proxy objects

### GET /proxies/{proxy_name}
**Summary:** Get proxy by name  
**Tags:** Proxies  
**Authentication:** Required  
**Parameters:**
- `proxy_name` (path): Proxy name to retrieve

**Response:** Proxy object with decrypted password

### POST /proxies
**Summary:** Create new proxy  
**Tags:** Proxies  
**Authentication:** Required  
**Status Code:** 201  
**Query Parameters:**
- `proxy_name` (required): Unique proxy name/identifier
- `host` (required): Proxy hostname or IP address
- `port` (required): Proxy port (1-65535)
- `proxy_type` (optional): Proxy type (socks5, socks4, http) - default: socks5
- `username` (optional): Proxy authentication username
- `password` (optional): Proxy authentication password (will be encrypted)
- `rdns` (optional): Resolve DNS remotely - default: true
- `active` (optional): Is proxy active? - default: true
- `notes` (optional): Optional notes about the proxy

**Description:** Password is automatically encrypted before storage.  
**Response:** Created proxy object (without password for security)

### PUT /proxies/{proxy_name}
**Summary:** Update proxy  
**Tags:** Proxies  
**Authentication:** Required  
**Parameters:**
- `proxy_name` (path): Proxy name to update
- Query parameters (all optional): host, port, proxy_type, username, password, rdns, active, notes

**Description:** Only provided fields will be updated. Password is automatically encrypted.  
**Response:** Updated proxy object (without password for security)

### DELETE /proxies/{proxy_name}
**Summary:** Delete proxy  
**Tags:** Proxies  
**Authentication:** Required  
**Parameters:**
- `proxy_name` (path): Proxy name to delete

**Restrictions:** Cannot delete proxy if currently connected to any accounts.

**Response:**
```json
{
  "message": "Proxy '{proxy_name}' deleted successfully",
  "proxy_name": "string"
}
```

### GET /proxies/stats/summary
**Summary:** Get proxy statistics  
**Tags:** Proxies  
**Authentication:** Required  
**Description:** Returns comprehensive statistics about proxies in the system.

**Response:**
```json
{
  "total_proxies": 10,
  "active_proxies": 8,
  "inactive_proxies": 2,
  "total_connected_accounts": 45,
  "least_used_proxy": {
    "proxy_name": "proxy1",
    "connected_accounts": 2
  },
  "most_used_proxy": {
    "proxy_name": "proxy5",
    "connected_accounts": 12
  }
}
```

---

## Channel Management Endpoints

All channel endpoints require authentication via `Depends(get_current_user)`.

### GET /channels
**Summary:** Get all channels  
**Tags:** Channels  
**Authentication:** Required  
**Query Parameters:**
- `chat_id` (optional): Filter by exact Telegram chat ID
- `tag` (optional): Filter by specific tag
- `name` (optional): Search by channel name (partial match, case-insensitive)

**Description:** Supports multiple filtering options. Accepts both normalized and -100 prefixed chat IDs.  
**Response:** List of channel objects

### GET /channels/{chat_id}
**Summary:** Get channel by chat_id  
**Tags:** Channels  
**Authentication:** Required  
**Parameters:**
- `chat_id` (path): Telegram chat ID

**Description:** Accepts both normalized and -100 prefixed chat IDs.  
**Response:** Channel object

### POST /channels
**Summary:** Create new channel  
**Tags:** Channels  
**Authentication:** Required  
**Status Code:** 201  
**Query Parameters:**
- `chat_id` (required): Telegram chat ID (unique identifier)
- `channel_name` (optional): Channel name/title
- `is_private` (optional): Is the channel private? - default: false
- `has_enabled_reactions` (optional): Does the channel have reactions enabled? - default: true
- `reactions_only_for_subscribers` (optional): Are reactions only for subscribers? - default: false
- `discussion_chat_id` (optional): Discussion group chat ID if exists
- `tags` (optional): Comma-separated list of tags

**Response:** Created channel object

### PUT /channels/{chat_id}
**Summary:** Update channel  
**Tags:** Channels  
**Authentication:** Required  
**Parameters:**
- `chat_id` (path): Telegram chat ID to update
- Query parameters (all optional): channel_name, is_private, has_enabled_reactions, reactions_only_for_subscribers, discussion_chat_id, tags

**Description:** Only provided fields will be updated.  
**Response:** Updated channel object

### DELETE /channels/{chat_id}
**Summary:** Delete channel  
**Tags:** Channels  
**Authentication:** Required  
**Parameters:**
- `chat_id` (path): Telegram chat ID to delete

**Note:** This deletes only the local database entry, not the actual Telegram channel. Associated posts remain in the database.

**Response:**
```json
{
  "message": "Channel with chat_id {chat_id} deleted successfully",
  "chat_id": 123456789
}
```

### GET /channels/stats/summary
**Summary:** Get channel statistics  
**Tags:** Channels  
**Authentication:** Required  
**Description:** Returns comprehensive statistics about channels.

**Response:**
```json
{
  "total_channels": 50,
  "private_channels": 12,
  "public_channels": 38,
  "channels_with_reactions": 45,
  "tag_distribution": {
    "news": 15,
    "entertainment": 8,
    "tech": 12
  }
}
```

### GET /channels/with-post-counts
**Summary:** Get channels with post counts  
**Tags:** Channels  
**Authentication:** Required  
**Description:** Returns all channels with an additional 'post_count' field indicating how many posts exist for each channel.

**Response:** List of channel objects with post_count field

---

## Account Subscription Endpoints

### GET /accounts/{phone_number}/channels
**Summary:** Get account's subscribed channels  
**Tags:** Accounts  
**Authentication:** Required  
**Parameters:**
- `phone_number` (path): Phone number of the account

**Description:** Returns list of Channel objects based on the account's subscribed_to list.  
**Response:** List of channel objects the account is subscribed to

### POST /accounts/{phone_number}/channels/sync
**Summary:** Sync account's subscribed channels from Telegram  
**Tags:** Accounts  
**Authentication:** Required  
**Parameters:**
- `phone_number` (path): Phone number of the account

**Description:** Connects to Telegram, fetches all subscribed channels, updates the account's `subscribed_to` field, and upserts channel data to the channels collection. Requires account to have a valid session.

**Response:**
```json
{
  "message": "Successfully synced 15 channels for account +1234567890",
  "phone_number": "+1234567890",
  "channels_count": 15,
  "chat_ids": [-1001234567890, -1009876543210],
  "synced_at": "2025-01-01T12:00:00Z"
}
```

### GET /channels/{chat_id}/subscribers
**Summary:** Get accounts subscribed to a channel  
**Tags:** Channels  
**Authentication:** Required  
**Parameters:**
- `chat_id` (path): Telegram chat ID

**Description:** Returns all accounts that are subscribed to the specified channel.  
**Response:** List of account objects (secure format, without passwords)

### POST /channels/bulk
**Summary:** Get multiple channels by chat_ids  
**Tags:** Channels  
**Authentication:** Required  
**Request Body:** Array of chat_ids (e.g., `[-1001234567890, 123456789]`)

**Description:** Get multiple channels in a single request. Returns only channels that exist.  
**Response:** List of channel objects

---

## Enhanced Posts Endpoint

### GET /posts (Enhanced)
**Summary:** Get all posts  
**Tags:** Posts  
**Authentication:** Required  
**Query Parameters:**
- `post_id` (optional): Filter by post ID
- `chat_id` (optional): Filter by chat ID (**optimized with database query**)
- `validated_only` (optional): Filter by validation status

**Enhancement:** The chat_id filter now uses the optimized database method `get_posts_by_chat_id()` for better performance instead of in-memory filtering.

---

## Database Method Additions

### MongoStorage.get_all_users()
**Description:** Retrieve all users from the database (without _id field).  
**Returns:** List of user dictionaries  
**Usage:** Used by GET /users endpoint

### MongoStorage.delete_user(username)
**Description:** Delete a user from the database.  
**Parameters:** username (str)  
**Returns:** True if successful, False otherwise  
**Usage:** Used by DELETE /users/{username} endpoint

---

## Security Considerations

1. **User Management:**
   - All user management endpoints require admin role
   - Cannot modify own role or delete own account
   - Cannot delete last admin user
   - Password hashes are never returned in responses

2. **Proxy Management:**
   - Passwords are automatically encrypted before storage (AES-GCM with purpose-specific KEK)
   - Passwords are excluded from responses for security
   - Cannot delete proxies that are currently in use

3. **Channel Management:**
   - Chat IDs are normalized to handle -100 prefix variations
   - Deletion is local only, doesn't affect actual Telegram channels

4. **Authentication:**
   - All new endpoints require valid JWT authentication
   - Admin-only endpoints verify admin role via dependency injection

---

## Testing Recommendations

1. **User Management:**
   - Test role change restrictions (cannot change own role)
   - Test deletion restrictions (cannot delete self or last admin)
   - Verify password hashes are never exposed

2. **Proxy Management:**
   - Test password encryption/decryption
   - Test proxy usage tracking
   - Verify deletion restrictions when proxies are in use
   - Test statistics calculation accuracy

3. **Channel Management:**
   - Test chat_id normalization (both forms should work)
   - Test tag filtering and search functionality
   - Verify post count aggregation
   - Test subscription retrieval for accounts

4. **Performance:**
   - Verify GET /posts uses database query for chat_id filter
   - Test pagination for large result sets (consider adding in future)

---

## Future Enhancements

1. **Pagination:** Add offset/limit parameters for large result sets
2. **Bulk Operations for Proxies:** Add bulk endpoints for proxy management
3. **Export/Import:** Add endpoints to export/import channel and proxy configurations
4. **Analytics:** Enhanced statistics with date ranges and trends
5. **Webhooks:** Notifications when proxy errors occur or channels are updated
