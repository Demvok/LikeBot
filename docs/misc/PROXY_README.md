# Proxy System - README

## Overview

The LikeBot proxy system provides secure, load-balanced proxy support for Telegram client connections with automatic fallback and encrypted credential storage.

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Environment Variables

```bash
# MongoDB connection
export db_url="mongodb://localhost:27017"

# Encryption key (generate once, keep secure)
export KEK="your-base64-encoded-master-key"
```

Generate a new encryption key:
```python
from encryption import generate_master_key_base64
print(generate_master_key_base64())
```

### 3. Add a Proxy

```python
import asyncio
from database import get_db

async def add_proxy():
    db = get_db()
    
    await db.add_proxy({
        'proxy_name': 'my-proxy',
        'host': '1.2.3.4',
        'socks5_port': 1080,
        'http_port': 8080,
        'username': 'user',
        'password': 'pass',  # Auto-encrypted
        'notes': 'My first proxy'
    })
    print("Proxy added successfully!")

asyncio.run(add_proxy())
```

### 4. Connect Client

```python
from agent import Client, Account

# Client automatically uses least-used proxy
client = Client(account)
await client.connect()  # Proxy selected automatically
```

## Features

✅ **Multi-Protocol Support**
- SOCKS5 (preferred)
- SOCKS4
- HTTP

✅ **Multi-Port Support**
- Store both HTTP and SOCKS5 ports in single record
- Automatic fallback between protocols

✅ **Security**
- Automatic password encryption
- Encrypted storage with master key
- No plain passwords in database

✅ **Load Balancing**
- Automatic selection of least-used proxy
- Connection tracking
- Usage counters

✅ **Error Handling**
- Automatic error tracking
- Fallback modes (strict/soft)
- Connection retry logic

✅ **Compatibility**
- Field name aliases (host/ip, username/login)
- Backward compatible with old records
- No breaking changes

## File Structure

```
LikeBot/
├── proxy.py                              # Proxy logic module
├── agent.py                              # Client with proxy support
├── database.py                           # Proxy CRUD operations
├── encryption.py                         # Password encryption
├── test_proxy.py                         # Test/demo script
├── requirements.txt                      # Dependencies (includes PySocks)
└── docs/
    ├── PROXY_CONFIGURATION.md            # Full configuration guide
    ├── PROXY_QUICK_REFERENCE.md          # Quick reference
    ├── PROXY_REFACTORING_SUMMARY.md      # Technical details
    └── COMPLETE_SUMMARY.md               # Status summary
```

## Common Tasks

### Add Proxy via MongoDB Shell

```javascript
db.proxies.insertOne({
  "proxy_name": "my-proxy",
  "host": "1.2.3.4",
  "socks5_port": 1080,
  "http_port": 8080,
  "username": "user",
  "password_encrypted": "...",  // Use API instead for encryption
  "active": true,
  "connected_accounts": 0,
  "rdns": true,
  "created_at": new Date()
})
```

**⚠️ Warning**: Direct DB insert bypasses password encryption. Use API methods.

### Update Proxy

```python
await db.update_proxy('my-proxy', {
    'active': False,
    'notes': 'Temporarily disabled'
})
```

### List All Proxies

```python
proxies = await db.get_all_proxies()
for proxy in proxies:
    print(f"{proxy['proxy_name']}: {proxy['host']}")
```

### Delete Proxy

```python
await db.delete_proxy('my-proxy')
```

## Configuration

### Proxy Mode

Set in `config.yaml`:

```yaml
proxy:
  mode: soft  # or 'strict'
```

- **soft**: Falls back to direct connection if proxy fails
- **strict**: Connection fails if proxy unavailable

### MongoDB Indexes

Automatically created on first use:
- Unique index on `proxy_name`
- Compound index on `active` + `connected_accounts` (for load balancing)

## Troubleshooting

### PySocks Not Found

```bash
pip install PySocks
```

### Password Decryption Fails

1. Check `KEK` environment variable is set
2. Verify same master key across instances
3. Re-encrypt passwords if migrating

### No Proxy Selected

1. Check `active: true` on proxy
2. Verify proxy has port fields
3. Check logs for errors

### Connection Fails

1. Test proxy manually: `curl --proxy socks5://user:pass@host:port https://api.telegram.org`
2. Check firewall settings
3. Verify credentials
4. Check `last_error` in database

## Testing

Run the test script:

```bash
python test_proxy.py
```

Tests:
- Proxy module functions
- Database operations (requires MongoDB)
- Candidate building
- Field aliases

## Security Best Practices

1. ✅ Always use `add_proxy()` for password encryption
2. ✅ Keep `KEK` environment variable secure
3. ✅ Use secret managers in production
4. ✅ Rotate proxies regularly
5. ✅ Monitor `last_error` fields
6. ✅ Use SOCKS5 when available

## Documentation

- **Full Guide**: [`docs/PROXY_CONFIGURATION.md`](docs/PROXY_CONFIGURATION.md)
- **Quick Reference**: [`docs/PROXY_QUICK_REFERENCE.md`](docs/PROXY_QUICK_REFERENCE.md)
- **Technical Details**: [`docs/PROXY_REFACTORING_SUMMARY.md`](docs/PROXY_REFACTORING_SUMMARY.md)
- **Summary**: [`docs/COMPLETE_SUMMARY.md`](docs/COMPLETE_SUMMARY.md)

## Support

For issues or questions:
1. Check documentation in `docs/` folder
2. Run `python test_proxy.py` to verify setup
3. Review logs for specific errors
4. Check database for error messages

## License

Part of LikeBot project. See main LICENSE file.
