# Quick API Endpoint Reference

## Authentication
```
POST   /auth/register               - Register new user
POST   /auth/login                  - Login and get JWT token
GET    /auth/me                     - Get current user info
```

## User Management (Admin Only)
```
GET    /users                        - List all users
PUT    /users/{username}/role        - Update user role
PUT    /users/{username}/verify      - Update verification status
DELETE /users/{username}             - Delete user
```

## Account Management
```
GET    /accounts                     - List all accounts (filter: phone_number)
GET    /accounts/{phone_number}      - Get specific account
POST   /accounts                     - Create account (without login)
PUT    /accounts/{phone_number}      - Update account
DELETE /accounts/{phone_number}      - Delete account
PUT    /accounts/{phone_number}/validate - Validate account connection
GET    /accounts/{phone_number}/password - Get decrypted password (Admin)
```

## Account Locks
```
GET    /accounts/locks                    - List all locked accounts
GET    /accounts/{phone_number}/lock      - Get lock status for account
DELETE /accounts/{phone_number}/lock      - Force release lock (Admin)
DELETE /tasks/{task_id}/locks             - Release all locks for task (Admin)
```

## Login Process
```
POST   /accounts/create/start        - Start Telegram login
POST   /accounts/create/verify       - Submit verification code
GET    /accounts/create/status       - Check login status
```

## Account Subscriptions
```
GET    /accounts/{phone_number}/channels      - Get subscribed channels
POST   /accounts/{phone_number}/channels/sync - Sync channels from Telegram
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
POST   /channels/bulk                - Get multiple channels by chat_ids
PUT    /channels/{chat_id}           - Update channel
DELETE /channels/{chat_id}           - Delete channel
GET    /channels/{chat_id}/subscribers - Get accounts subscribed to channel
GET    /channels/stats/summary       - Get channel statistics
GET    /channels/with-post-counts    - List channels with post counts
```

## Reaction Palettes
```
GET    /palettes                     - List all palettes (filter: palette_name)
GET    /palettes/{palette_name}      - Get specific palette
POST   /palettes                     - Create new palette
PUT    /palettes/{palette_name}      - Update palette
DELETE /palettes/{palette_name}      - Delete palette
```

## Posts
```
GET    /posts                        - List all posts (filter: post_id, chat_id, validated_only)
GET    /posts/{post_id}              - Get specific post
POST   /posts                        - Create new post
PUT    /posts/{post_id}              - Update post
DELETE /posts/{post_id}              - Delete post
POST   /posts/{post_id}/validate     - Validate post (extract IDs from link)
```

## Tasks
```
GET    /tasks                        - List all tasks (filter: task_id, status, name)
GET    /tasks/{task_id}              - Get specific task
POST   /tasks                        - Create new task
PUT    /tasks/{task_id}              - Update task
DELETE /tasks/{task_id}              - Delete task
```

## Task Actions
```
GET    /tasks/{task_id}/status       - Get task status
POST   /tasks/{task_id}/start        - Start task execution
POST   /tasks/{task_id}/pause        - Pause task
POST   /tasks/{task_id}/resume       - Resume task
GET    /tasks/{task_id}/report       - Get task report
GET    /tasks/{task_id}/runs         - List all runs for task
GET    /tasks/{task_id}/runs/{run_id}/report - Get report for specific run
DELETE /tasks/{task_id}/runs/{run_id} - Delete specific run
DELETE /tasks/{task_id}/runs         - Delete all runs for task
GET    /runs                         - List all runs across all tasks
```

## Bulk Operations
```
POST   /accounts/bulk                - Create multiple accounts
POST   /posts/bulk                   - Create multiple posts
DELETE /accounts/bulk                - Delete multiple accounts
DELETE /posts/bulk                   - Delete multiple posts
```

## Utilities
```
GET    /                             - Health check
GET    /stats                        - Get database statistics
```

## WebSocket
```
WS     /ws/logs                      - Stream logs (params: token, log_file, tail)
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

### Sync Account Channels from Telegram
```bash
curl -X POST "http://localhost:8080/accounts/+1234567890/channels/sync" \
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
