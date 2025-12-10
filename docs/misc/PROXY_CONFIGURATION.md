# Proxy Configuration Guide

This guide explains how to configure and use proxies with LikeBot.

## Overview

LikeBot supports proxy connections for Telegram clients with the following features:
- Multiple proxy types: SOCKS5, SOCKS4, HTTP
- Multiple ports per proxy (e.g., both HTTP and SOCKS5 ports from same provider)
- Account-specific proxy pools (each account can hold up to five favorites)
- Random proxy selection per connection with automatic candidate fallback
- Encrypted password storage
- Strict/soft fallback modes with detailed error tracking

## Database Schema

### Proxy Document Structure

Each proxy is stored as a single MongoDB document in the `proxies` collection:

```javascript
{
  // Required fields
  "proxy_name": "unique-proxy-identifier",  // Unique identifier
  "host": "1.2.3.4",                       // IP address or hostname
  
  // Port fields (at least one required)
  "socks5_port": 1080,                     // SOCKS5 port (preferred)
  "http_port": 8080,                       // HTTP port
  "port": 1080,                            // Generic port (fallback)
  
  // Authentication (optional)
  "username": "mylogin",                   // Username for proxy auth
  "password_encrypted": "...",             // Encrypted password (auto-encrypted on add)
  
  // Configuration
  "type": "socks5",                        // Default type: socks5, socks4, or http
  "rdns": true,                            // Remote DNS resolution (default: true)
  
  // Status tracking
  "active": true,                          // Whether proxy is currently active
  "linked_accounts_count": 0,              // Number of accounts referencing this proxy
  "last_error": null,                      // Last error message (if any)
  "last_error_time": null,                 // Timestamp of last error
  
  // Metadata
  "created_at": ISODate("2025-11-13T..."),
  "notes": "Description or tags"
}
```

### Field Descriptions

#### Required Fields

- **proxy_name** (string): Unique identifier for the proxy. Used as primary key.
- **host** (string): Proxy hostname or IP address. Alternative field names: `ip`, `addr`

#### Port Fields (at least one required)

The system supports multiple port configurations:

- **socks5_port** (int): Port for SOCKS5 connections (preferred, tried first)
- **socks_port** (int): Alternative name for SOCKS5 port
- **http_port** (int): Port for HTTP connections (tried after SOCKS5)
- **port** (int): Generic port field (fallback, used with `type` field)

**Important**: You can specify multiple ports (e.g., both `socks5_port` and `http_port`). The system will try them in preference order: SOCKS5 → HTTP → generic port.

#### Authentication Fields (optional)

- **username** (string): Authentication username. Alternative: `login`
- **password** (string): Plain password (will be encrypted automatically on insert)
- **password_encrypted** (string): Encrypted password (stored after encryption)

**Security Note**: Always use the `password` field when adding proxies via `add_proxy()`. The system automatically encrypts it and stores as `password_encrypted`. Never manually set `password_encrypted`.

#### Configuration Fields

- **type** (string): Proxy protocol type. Options: `socks5`, `socks4`, `http`. Default: `socks5`
- **rdns** (bool): Enable remote DNS resolution. Default: `true`

#### Status Tracking Fields

- **active** (bool): Whether proxy is available for use. Default: `true`
- **linked_accounts_count** (int): Number of accounts that list this proxy in their pool. Auto-managed. Default: `0`
- **last_error** (string): Last error message encountered
- **last_error_time** (datetime): Timestamp of last error

#### Metadata Fields

- **created_at** (datetime): When proxy was added
- **notes** (string): Description, tags, or other metadata

### Account Document Field

Accounts include an `assigned_proxies` array containing up to five proxy names. This is the authoritative source for proxy selection. The list can be managed via the `/accounts/{phone}/proxies` endpoints described below.

## Usage Examples

### Example 1: Proxy with Both SOCKS5 and HTTP Ports

```javascript
{
  "proxy_name": "provider-1-multi",
  "host": "proxy.example.com",
  "socks5_port": 1080,
  "http_port": 8080,
  "username": "myuser",
  "password": "mypassword",  // Will be encrypted on insert
  "rdns": true,
  "active": true,
  "linked_accounts_count": 0,
  "notes": "Provider XYZ - supports both protocols"
}
```

**Behavior**: System will try SOCKS5 (port 1080) first. If that fails, it will try HTTP (port 8080).

### Example 2: SOCKS5-only Proxy

```javascript
{
  "proxy_name": "socks5-only-proxy",
  "host": "192.168.1.100",
  "socks5_port": 1080,
  "username": "admin",
  "password": "secret123",
  "active": true,
  "linked_accounts_count": 0
}
```

**Behavior**: System will only attempt SOCKS5 connection on port 1080.

### Example 3: HTTP-only Proxy (no auth)

```javascript
{
  "proxy_name": "http-public",
  "host": "proxy.public.com",
  "http_port": 8080,
  "active": true,
  "linked_accounts_count": 0,
  "notes": "Public HTTP proxy, no authentication"
}
```

**Behavior**: System will attempt HTTP connection on port 8080 without credentials.

### Example 4: Generic Port with Type Specification

```javascript
{
  "proxy_name": "generic-proxy",
  "host": "10.0.0.50",
  "port": 1080,
  "type": "socks5",
  "username": "user",
  "password": "pass",
  "active": true,
  "linked_accounts_count": 0
}
```

**Behavior**: System will use the `port` field with the specified `type`.

## Adding Proxies via API

### Python Example

```python
from database import get_db

db = get_db()

# Add proxy with both ports
proxy_data = {
    'proxy_name': 'my-proxy-1',
    'host': '1.2.3.4',
    'socks5_port': 1080,
    'http_port': 8080,
    'username': 'myuser',
    'password': 'mypassword',  # Will be auto-encrypted
    'rdns': True,
    'active': True,
    'notes': 'Production proxy'
}

await db.add_proxy(proxy_data)
```

### Bulk Import via Upload

Upload a provider dump (`host:port:username:password`) and let the API parse
each line into proxy records.

```bash
curl -X POST "https://likebot.example.com/proxies/import" \
  -H "Authorization: Bearer <token>" \
  -F "proxy_file=@\u041f\u0440\u043e\u043a\u0441\u0456.csv" \
  -F "dry_run=false"
```

Optional form/query parameters:

- `proxy_type`: Force proxy type if header is missing (defaults to socks5)
- `base_name`: Prefix for generated proxy names
- `dry_run`: Validate/parses file without inserting anything (defaults to false)

The endpoint returns how many rows were imported, skipped (already existing)
or failed with detailed reasons.

### MongoDB Shell Example

```javascript
db.proxies.insertOne({
  "proxy_name": "direct-insert-proxy",
  "host": "proxy.server.com",
  "socks5_port": 1080,
  "username": "admin",
  // Note: Direct DB insert bypasses encryption
  // Use API for proper password encryption
  "active": true,
  "linked_accounts_count": 0,
  "rdns": true,
  "created_at": new Date()
})
```

**Warning**: Direct MongoDB insertion bypasses password encryption. Always use the `add_proxy()` API method.

## Connection Behavior

### Candidate Selection

When a client connects, the system:

1. Reads the account's `assigned_proxies` list (up to five entries)
2. If the list is empty, logs a warning and:
  - **Strict mode**: aborts the connection with an error
  - **Soft mode**: connects without a proxy
3. Randomly shuffles the assigned proxies and picks the first usable proxy (active + valid ports)
4. Builds multiple proxy candidates from that record:
  - Candidate 1: SOCKS5 connection (if `socks5_port` or `socks_port` present)
  - Candidate 2: HTTP connection (if `http_port` present)
  - Candidate 3: Generic connection (if `port` present, using `type` field)
5. Tries each candidate in order until one succeeds
6. If all assigned proxies fail:
  - **Strict mode**: Connection fails with error
  - **Soft mode**: Retries without proxy

### Proxy Mode Configuration

Set in `config.yaml`:

```yaml
proxy:
  mode: soft  # or 'strict'
  max_per_account: 5  # Maximum number of proxies per account
  desired_per_account: 3  # Target number of proxies auto-assignment will try to maintain
```

- **soft**: Falls back to direct connection if all proxy candidates fail
- **strict**: Connection fails if proxy cannot be established
- **max_per_account**: Upper bound of proxies stored in `assigned_proxies` (default 5)
- **desired_per_account**: Target number of proxies to maintain per account when calling the auto-assignment helper (default 3, capped by `max_per_account`)

### Account Proxy Pools & Linking

- Each account can hold up to **five** proxy names in `assigned_proxies`.
- Use the API endpoints to manage assignments:
  - `GET /accounts/{phone}/proxies` – view current assignments.
  - `POST /accounts/{phone}/proxies/{proxy}` – add a proxy to the pool (requires proxy to be active).
  - `DELETE /accounts/{phone}/proxies/{proxy}` – remove a proxy assignment.
  - `POST /accounts/{phone}/proxies/auto-assign` – automatically link the least-linked proxies until the configured `desired_per_account` count (or an overridden `desired_count` query param) is reached.
- The `linked_accounts_count` field on every proxy tracks how many accounts reference it. Use `/proxies/least-linked` or `/proxies/stats/summary` to identify underused proxies.
- Runtime selection is driven solely by the account's pool; `linked_accounts_count` is informational and never affects which proxy a client picks.

### Automatic Assignment Helper

The auto-assignment endpoint relies on the new `MongoStorage.auto_assign_proxies` helper. It selects proxies ordered by `linked_accounts_count` (favoring underused entries) and links them to the account until it reaches either:

1. The `desired_count` query parameter (if supplied), or
2. The `proxy.desired_per_account` configuration value (default 3).

Assignments never exceed `proxy.max_per_account`. The endpoint response includes the proxies that were added, the full assigned list, and how many proxies (if any) are still needed. If there are not enough eligible proxies (e.g., not enough active proxies remain), the response includes a message noting the shortfall so you can provision more proxies before retrying.

### Connectivity Testing

To verify that a proxy actually tunnels traffic, call the new endpoint:

```
POST /proxies/{proxy_name}/test
```

- By default it performs an HTTPS request to `https://2ip.ua/api/index.php?type=json` (through the proxy) and parses the IP/hostname/provider/location fields returned by 2ip.
- You can override the URL via the `test_url` query parameter if you want to hit the plain-text landing page (`https://2ip.ua/`) or any other diagnostic host.
- `timeout_seconds` (default 15, max 120) controls how long the request may take before it is considered a failure.

Sample response:

```json
{
  "proxy_name": "alpha-proxy",
  "endpoint": "socks5://user:******@127.0.0.1:9050",
  "target_url": "https://2ip.ua/api/index.php?type=json",
  "latency_ms": 843.21,
  "status_code": 200,
  "details": {
    "ip": "62.244.1.225",
    "hostname": "62.244.1.225.ip.internetspace.com.ua",
    "provider": "Lucky Net Ltd",
    "location": "Hlevakha, Ukraine",
    "raw": {
      "ip": "62.244.1.225",
      "hostname": "62.244.1.225.ip.internetspace.com.ua",
      "provider": "Lucky Net Ltd",
      "country": "Ukraine",
      "city": "Hlevakha"
    }
  }
}
```

This mirrors the manual check you would do with:

```powershell
curl.exe --socks5-hostname 127.0.0.1:9050 https://2ip.ua
```

If the reported IP/hostname/provider/location match what you expect from the proxy vendor, the tunnel is considered healthy. Otherwise, the endpoint returns an error (including the failed endpoints) so you can troubleshoot credentials or reachability issues.

### Error Handling

When a proxy connection fails:
- `last_error` is set to the error message
- `last_error_time` is updated
- Other candidates are tried before giving up
- In soft mode, system falls back to direct connection

When a connection succeeds:
- `last_error` and `last_error_time` are cleared

## Database Operations

### Add a Proxy

```python
await db.add_proxy(proxy_data)
```

Automatically encrypts password and sets defaults.

### Get a Proxy

```python
proxy = await db.get_proxy('proxy-name')
```

Returns proxy with decrypted password.

### Get All Active Proxies

```python
proxies = await db.get_active_proxies()
```

Returns list of active proxies with decrypted passwords.

### Update a Proxy

```python
await db.update_proxy('proxy-name', {
    'active': False,
    'notes': 'Disabled due to poor performance'
})
```

### Delete a Proxy

```python
await db.delete_proxy('proxy-name')
```

### Manual Error Tracking

```python
# Set error (usually done automatically)
await db.set_proxy_error('proxy-name', 'Connection timeout')

# Clear error (done automatically on successful connection)
await db.clear_proxy_error('proxy-name')
```

## Migration from Old Format

If you have proxies stored with different field names:

### Old Format
```javascript
{
  "name": "old-proxy",
  "ip": "1.2.3.4",
  "login": "user",
  "password": "plain"
}
```

### Migration Script
```python
from database import get_db

db = get_db()

# Fetch old proxy
old_proxy = await db._proxies.find_one({'name': 'old-proxy'})

# Convert to new format
new_proxy = {
    'proxy_name': old_proxy['name'],
    'host': old_proxy['ip'],
    'port': old_proxy.get('port', 1080),
    'type': 'socks5',
    'username': old_proxy.get('login'),
    'password': old_proxy.get('password'),  # Will be encrypted
    'active': True,
    'linked_accounts_count': 0
}

# Add using API (encrypts password)
await db.add_proxy(new_proxy)

# Delete old format
await db._proxies.delete_one({'name': 'old-proxy'})
```

## Troubleshooting

### Proxy Not Selected

**Symptom**: Clients connect without proxy even though proxies exist

**Solutions**:
- Check `active: true` is set on proxy
- Verify proxy has at least one valid port field
- Check logs for "No active proxies available"

### All Candidates Fail

**Symptom**: "All proxy candidates failed" error

**Solutions**:
- Verify proxy credentials are correct
- Check firewall/network allows proxy connections
- Test proxy manually: `curl --proxy socks5://user:pass@host:port https://api.telegram.org`
- Check `last_error` field in database for specific error

### Password Decryption Fails

**Symptom**: "Failed to decrypt password for proxy" in logs

**Solutions**:
- Verify `KEK` environment variable is set correctly
- Ensure same master key is used across all instances
- If migrating, re-encrypt passwords using `add_proxy()` or `update_proxy()`

### PySocks Not Installed

**Symptom**: "PySocks not installed" error

**Solution**:
```bash
pip install PySocks
```

## Best Practices

1. **Use API Methods**: Always use `add_proxy()` and `update_proxy()` instead of direct MongoDB operations to ensure proper password encryption

2. **Specify Multiple Ports**: If your provider offers both HTTP and SOCKS5, include both ports to maximize connection success rate

3. **Monitor Usage**: Check `linked_accounts_count` (and `/proxies/least-linked`) to see which proxies are underused

4. **Set Descriptive Names**: Use clear proxy names like `provider-location-protocol` (e.g., `acme-us-socks5`)

5. **Tag with Notes**: Use `notes` field for metadata like provider name, region, purchase date, etc.

6. **Regular Health Checks**: Periodically review `last_error` fields and disable problematic proxies

7. **Prefer SOCKS5**: When available, SOCKS5 is generally more reliable for Telegram traffic than HTTP proxies

8. **Secure Master Key**: Store the `KEK` environment variable securely (use secret managers in production)

## Related Files

- `proxy.py` - Proxy configuration and candidate building logic
- `database.py` - Proxy CRUD operations with encryption
- `encryption.py` - Password encryption/decryption
- `agent.py` - Client connection logic using proxies
- `config.yaml` - Proxy mode configuration
