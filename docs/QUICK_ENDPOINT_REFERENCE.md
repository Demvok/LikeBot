# Quick API Endpoint Reference

## User Management (Admin Only)
```
GET    /users                        - List all users
PUT    /users/{username}/role        - Update user role
PUT    /users/{username}/verify      - Update verification status
DELETE /users/{username}             - Delete user
```

## Proxy Management
```
GET    /proxies                      - List all proxies (filter: proxy_name, active_only)
GET    /proxies/{proxy_name}         - Get specific proxy
POST   /proxies                      - Create new proxy
PUT    /proxies/{proxy_name}         - Update proxy
DELETE /proxies/{proxy_name}         - Delete proxy
GET    /proxies/stats/summary        - Get proxy statistics
```

## Channel Management
```
GET    /channels                     - List all channels (filter: chat_id, tag, name)
GET    /channels/{chat_id}           - Get specific channel
POST   /channels                     - Create new channel
PUT    /channels/{chat_id}           - Update channel
DELETE /channels/{chat_id}           - Delete channel
GET    /channels/stats/summary       - Get channel statistics
GET    /channels/with-post-counts    - List channels with post counts
```

## Account Subscriptions
```
GET    /accounts/{phone_number}/channels - Get subscribed channels for account
```

## Enhanced Existing Endpoints
```
GET    /posts?chat_id={id}           - Get posts by chat_id (optimized)
```

---

## Quick Examples

### Create a Proxy
```bash
curl -X POST "http://localhost:8080/proxies?proxy_name=my_proxy&host=proxy.example.com&port=1080&proxy_type=socks5&username=user&password=pass" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Create a Channel
```bash
curl -X POST "http://localhost:8080/channels?chat_id=1234567890&channel_name=My%20Channel&tags=news,tech" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Get User's Subscribed Channels
```bash
curl "http://localhost:8080/accounts/+1234567890/channels" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Update User Role (Admin)
```bash
curl -X PUT "http://localhost:8080/users/john_doe/role?role=admin" \
  -H "Authorization: Bearer ADMIN_TOKEN"
```

### Get Posts by Channel
```bash
curl "http://localhost:8080/posts?chat_id=1234567890" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Get Proxy Statistics
```bash
curl "http://localhost:8080/proxies/stats/summary" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Get Channel Statistics
```bash
curl "http://localhost:8080/channels/stats/summary" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## Authentication Notes

- All endpoints require JWT authentication via `Authorization: Bearer <token>` header
- User management endpoints require admin role
- Get your token via `POST /auth/login` endpoint
- Token includes username, role, and verification status
