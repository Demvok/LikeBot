# Proxy Quick Reference

## Adding a Proxy (Python API)

```python
from database import get_db

db = get_db()

# Simple SOCKS5 proxy
await db.add_proxy({
    'proxy_name': 'my-proxy',
    'host': '1.2.3.4',
    'socks5_port': 1080,
    'username': 'user',
    'password': 'pass'  # Auto-encrypted
})

# Multi-protocol proxy (both HTTP and SOCKS5)
await db.add_proxy({
    'proxy_name': 'multi-proxy',
    'host': '1.2.3.4',
    'socks5_port': 1080,
    'http_port': 8080,
    'username': 'user',
    'password': 'pass',
    'notes': 'Tries SOCKS5 first, falls back to HTTP'
})
```

## Proxy Document Structure (MongoDB)

```javascript
{
  "proxy_name": "unique-id",      // Required: unique identifier
  "host": "1.2.3.4",              // Required: IP or hostname
  "socks5_port": 1080,            // Optional: SOCKS5 port (preferred)
  "http_port": 8080,              // Optional: HTTP port
  "port": 1080,                   // Optional: generic port fallback
  "username": "user",             // Optional: auth username
  "password_encrypted": "...",    // Auto-set: encrypted password
  "type": "socks5",               // Optional: default protocol
  "rdns": true,                   // Optional: remote DNS (default: true)
  "active": true,                 // Auto-managed: availability
    "linked_accounts_count": 0,     // Auto-managed: number of accounts referencing this proxy
  "last_error": null,             // Auto-managed: last error
  "created_at": ISODate("...")    // Auto-set: creation time
}
```

## Field Aliases

The system accepts alternative field names for compatibility:

| Standard Field | Aliases |
|---------------|---------|
| `host` | `ip`, `addr` |
| `username` | `login` |
| `socks5_port` | `socks_port` |

## Port Priority

When multiple ports are defined, candidates are tried in this order:

1. **SOCKS5**: `socks5_port` or `socks_port`
2. **HTTP**: `http_port`
3. **Generic**: `port` (uses `type` field)

## Database Operations

```python
# Get specific proxy
proxy = await db.get_proxy('proxy-name')

# Get all active proxies
proxies = await db.get_active_proxies()

# Update proxy
await db.update_proxy('proxy-name', {'active': False})

# Delete proxy
await db.delete_proxy('proxy-name')

# Account linking helpers
await db.link_proxy_to_account('+1234567890', 'proxy-name')
await db.unlink_proxy_from_account('+1234567890', 'proxy-name')

# Least-linked reporting
proxies = await db.get_least_linked_proxies(limit=5)

# Error tracking (auto-managed, but can be manual)
await db.set_proxy_error('proxy-name', 'Connection timeout')
await db.clear_proxy_error('proxy-name')
```

## Account Assignments (API)

- `GET /accounts/{phone}/proxies` – list proxies assigned to an account
- `POST /accounts/{phone}/proxies/{proxy_name}` – link proxy to account (max 5)
- `DELETE /accounts/{phone}/proxies/{proxy_name}` – unlink proxy from account
- `GET /proxies/least-linked` – inspect proxies with the fewest linked accounts

## Proxy Module Functions

```python
from proxy import build_proxy_dict, build_proxy_candidates

# Build single proxy dict from DB record
proxy_dict = build_proxy_dict(proxy_data, logger)

# Build multiple candidates from single record
candidates = build_proxy_candidates(proxy_data, logger)
```

## Connection Modes

Set in `config.yaml`:

```yaml
proxy:
  mode: soft  # or 'strict'
    max_per_account: 5
```

| Mode | Behavior |
|------|----------|
| `soft` | Falls back to direct connection if all proxy candidates fail |
| `strict` | Connection fails if proxy cannot be established |

## Common Patterns

### Single SOCKS5 Proxy
```python
{
    'proxy_name': 'socks5-1',
    'host': '1.2.3.4',
    'socks5_port': 1080,
    'username': 'user',
    'password': 'pass'
}
```

### Multi-Protocol Proxy
```python
{
    'proxy_name': 'multi-1',
    'host': '1.2.3.4',
    'socks5_port': 1080,
    'http_port': 8080,
    'username': 'user',
    'password': 'pass'
}
```

### HTTP-Only Proxy
```python
{
    'proxy_name': 'http-1',
    'host': '1.2.3.4',
    'http_port': 8080,
    'username': 'user',
    'password': 'pass'
}
```

### Public Proxy (No Auth)
```python
{
    'proxy_name': 'public-1',
    'host': 'proxy.public.com',
    'port': 8080,
    'type': 'http'
}
```

## Security Notes

1. **Never store plain passwords**: Use `add_proxy()` or `update_proxy()` API methods
2. **Passwords are auto-encrypted** using `PURPOSE_PROXY_PASSWORD` constant
3. **Passwords are auto-decrypted** when retrieving proxies
4. **Master key required**: Set `KEK` environment variable
5. **Direct DB inserts bypass encryption**: Always use API methods

## Troubleshooting

| Problem | Solution |
|---------|----------|
| No proxy selected | Check `active: true`, verify port fields exist |
| All candidates fail | Check credentials, firewall, `last_error` field |
| Password decrypt fails | Verify `KEK` env var, re-encrypt using API |
| PySocks error | Install: `pip install PySocks` |

## Example: Add Proxy via Script

```python
import asyncio
from database import get_db

async def add_my_proxy():
    db = get_db()
    
    result = await db.add_proxy({
        'proxy_name': 'production-proxy-1',
        'host': '203.0.113.45',
        'socks5_port': 1080,
        'http_port': 8080,
        'username': 'myuser',
        'password': 'mypassword',
        'rdns': True,
        'active': True,
        'notes': 'Main production proxy - Provider XYZ'
    })
    
    print(f"Proxy added: {result}")

# Run
asyncio.run(add_my_proxy())
```

## Documentation Files

- **Full Guide**: `docs/PROXY_CONFIGURATION.md`
- **Refactoring Summary**: `docs/PROXY_REFACTORING_SUMMARY.md`
- **Quick Reference**: `docs/PROXY_QUICK_REFERENCE.md` (this file)

## Related Modules

- `proxy.py` - Proxy configuration logic
- `database.py` - Proxy database operations
- `encryption.py` - Password encryption/decryption
- `agent.py` - Client connection with proxy support
