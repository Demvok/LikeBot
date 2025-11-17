# Proxy Configuration Guide

This guide explains how to configure and use proxies with LikeBot.

## Overview

LikeBot supports proxy connections for Telegram clients with the following features:
- Multiple proxy types: SOCKS5, SOCKS4, HTTP
- Multiple ports per proxy (e.g., both HTTP and SOCKS5 ports from same provider)
- Automatic candidate selection and fallback
- Encrypted password storage
- Load balancing across proxies
- Usage tracking and error handling

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
  "connected_accounts": 0,                 // Number of accounts using this proxy
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
- **connected_accounts** (int): Current number of connected accounts. Auto-managed. Default: `0`
- **last_error** (string): Last error message encountered
- **last_error_time** (datetime): Timestamp of last error

#### Metadata Fields

- **created_at** (datetime): When proxy was added
- **notes** (string): Description, tags, or other metadata

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
  "connected_accounts": 0,
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
  "connected_accounts": 0
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
  "connected_accounts": 0,
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
  "connected_accounts": 0
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
  "connected_accounts": 0,
  "rdns": true,
  "created_at": new Date()
})
```

**Warning**: Direct MongoDB insertion bypasses password encryption. Always use the `add_proxy()` API method.

## Connection Behavior

### Candidate Selection

When a client connects, the system:

1. Selects the least-used active proxy from the database
2. Builds multiple proxy candidates from that record:
   - Candidate 1: SOCKS5 connection (if `socks5_port` or `socks_port` present)
   - Candidate 2: HTTP connection (if `http_port` present)
   - Candidate 3: Generic connection (if `port` present, using `type` field)
3. Tries each candidate in order until one succeeds
4. If all fail:
   - **Strict mode**: Connection fails with error
   - **Soft mode**: Retries without proxy

### Proxy Mode Configuration

Set in `config.yaml`:

```yaml
proxy:
  mode: soft  # or 'strict'
```

- **soft**: Falls back to direct connection if all proxy candidates fail
- **strict**: Connection fails if proxy cannot be established

### Load Balancing

The system automatically balances load across proxies by selecting the proxy with the lowest `connected_accounts` value. Usage counters are incremented when a connection succeeds and decremented when it disconnects.

### Error Handling

When a proxy connection fails:
- `last_error` is set to the error message
- `last_error_time` is updated
- Other candidates are tried before giving up
- In soft mode, system falls back to direct connection

When a connection succeeds:
- `last_error` and `last_error_time` are cleared
- `connected_accounts` is incremented

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
    'connected_accounts': 0
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

3. **Monitor Usage**: Check `connected_accounts` field to ensure load is balanced

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
