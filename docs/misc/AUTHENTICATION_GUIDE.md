# JWT Authentication Guide for LikeBot API

## Overview

LikeBot API now uses JWT (JSON Web Token) based authentication to secure all endpoints. This guide explains how to use the authentication system.

## Table of Contents

1. [Environment Setup](#environment-setup)
2. [User Registration](#user-registration)
3. [User Login](#user-login)
4. [Using Access Tokens](#using-access-tokens)
5. [User Roles and Permissions](#user-roles-and-permissions)
6. [API Endpoint Organization](#api-endpoint-organization)
7. [Error Responses](#error-responses)

## Environment Setup

### Required Environment Variables

Add these environment variables to your `.env` file:

```bash
# Existing variables
KEK=<your-master-encryption-key>
db_url=<your-mongodb-url>
db_name=LikeBot

# New JWT variables
JWT_SECRET_KEY=<your-jwt-secret-key>
```

### Generate JWT Secret Key

You can generate a secure JWT secret key using Python:

```python
from encryption import generate_jwt_secret_key
secret = generate_jwt_secret_key()
print(f"JWT_SECRET_KEY={secret}")
```

Or in terminal:
```bash
python -c "from encryption import generate_jwt_secret_key; print('JWT_SECRET_KEY=' + generate_jwt_secret_key())"
```

## User Registration

### Register a New User

**Endpoint:** `POST /auth/register`

**Request Body:**
```json
{
  "username": "john_doe",
  "password": "secure_password_123",
  "role": "user"
}
```

**Response (201 Created):**
```json
{
  "username": "john_doe",
  "is_verified": false,
  "role": "user",
  "created_at": "2025-10-30T12:00:00Z",
  "updated_at": "2025-10-30T12:00:00Z"
}
```

**Notes:**
- Usernames must be 3-50 characters, alphanumeric with underscores/hyphens
- Passwords must be at least 6 characters
- New users start as `is_verified: false`
- Available roles: `admin`, `user`, `guest`

## User Login

### Authenticate and Get Token

**Endpoint:** `POST /auth/login`

**Request Body (form-data):**
```
username: john_doe
password: secure_password_123
```

**Response (200 OK):**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

**Token Payload:**
```json
{
  "sub": "john_doe",
  "is_verified": true,
  "role": "admin",
  "exp": 1730300000
}
```

### Login Error Responses

#### User Not Found (401)
```json
{
  "detail": "User not found. Please register first."
}
```

#### Incorrect Password (401)
```json
{
  "detail": "Incorrect password"
}
```

#### User Not Verified (403)
```json
{
  "detail": "User account is not verified. Please contact an administrator."
}
```

## Using Access Tokens

### Include Token in Requests

Add the token to the `Authorization` header:

```bash
curl -H "Authorization: Bearer <your-token>" \
  http://localhost:8080/accounts
```

### Python Example

```python
import requests

# Login
login_response = requests.post(
    "http://localhost:8080/auth/login",
    data={
        "username": "john_doe",
        "password": "secure_password_123"
    }
)
token = login_response.json()["access_token"]

# Make authenticated request
headers = {"Authorization": f"Bearer {token}"}
accounts = requests.get(
    "http://localhost:8080/accounts",
    headers=headers
)
```

### JavaScript Example

```javascript
// Login
const loginResponse = await fetch('http://localhost:8080/auth/login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  body: new URLSearchParams({
    username: 'john_doe',
    password: 'secure_password_123'
  })
});
const { access_token } = await loginResponse.json();

// Make authenticated request
const accountsResponse = await fetch('http://localhost:8080/accounts', {
  headers: { 'Authorization': `Bearer ${access_token}` }
});
```

## User Roles and Permissions

### Role Hierarchy

1. **Admin** - Full access to all endpoints
2. **User** - Access to most endpoints (default for new users)
3. **Guest** - Limited read-only access

### Admin-Only Endpoints

The following endpoints require admin privileges:

- `GET /accounts/{phone_number}/password` - Get account password

### Verified User Requirements

Most endpoints require the user to be verified (`is_verified: true`). Admins can verify users by updating the user record in the database:

```javascript
// MongoDB update to verify a user
db.users.updateOne(
  { username: "john_doe" },
  { $set: { is_verified: true } }
)
```

## API Endpoint Organization

All endpoints now use simple, clean URLs without versioning:

### Authentication
- `POST /auth/register` - Register new user
- `POST /auth/login` - Login and get token
- `GET /auth/me` - Get current user info

### Accounts
- `GET /accounts` - List all accounts
- `GET /accounts/{phone_number}` - Get specific account
- `POST /accounts` - Create account
- `PUT /accounts/{phone_number}` - Update account
- `DELETE /accounts/{phone_number}` - Delete account
- `PUT /accounts/{phone_number}/validate` - Validate account
- `GET /accounts/{phone_number}/password` - Get account password (admin only)

### Account Creation (Telegram Login)
- `POST /accounts/create/start` - Start Telegram login
- `POST /accounts/create/verify` - Verify login code
- `GET /accounts/create/status` - Check login status

### Posts
- `GET /posts` - List all posts
- `GET /posts/{post_id}` - Get specific post
- `POST /posts` - Create post
- `PUT /posts/{post_id}` - Update post
- `DELETE /posts/{post_id}` - Delete post
- `POST /posts/{post_id}/validate` - Validate post

### Tasks
- `GET /tasks` - List all tasks
- `GET /tasks/{task_id}` - Get specific task
- `POST /tasks` - Create task
- `PUT /tasks/{task_id}` - Update task
- `DELETE /tasks/{task_id}` - Delete task

### Task Actions
- `GET /tasks/{task_id}/status` - Get task status
- `POST /tasks/{task_id}/start` - Start task
- `POST /tasks/{task_id}/pause` - Pause task
- `POST /tasks/{task_id}/resume` - Resume task
- `GET /tasks/{task_id}/report` - Get task report
- `GET /tasks/{task_id}/runs` - Get all runs
- `GET /tasks/{task_id}/runs/{run_id}/report` - Get specific run report
- `DELETE /tasks/{task_id}/runs/{run_id}` - Delete specific run
- `DELETE /tasks/{task_id}/runs` - Delete all runs for task
- `GET /runs` - Get all runs across all tasks

### Bulk Operations
- `POST /accounts/bulk` - Create multiple accounts
- `DELETE /accounts/bulk` - Delete multiple accounts
- `POST /posts/bulk` - Create multiple posts
- `DELETE /posts/bulk` - Delete multiple posts

### Utilities
- `GET /stats` - Get database statistics

### WebSocket
- `WS /ws/logs?token=<jwt-token>&log_file=<filename>` - Stream logs (requires token in query)

## Error Responses

### 401 Unauthorized
Token is missing, invalid, or expired:
```json
{
  "detail": "Could not validate credentials"
}
```

### 403 Forbidden
User doesn't have required permissions:
```json
{
  "detail": "User is not verified"
}
```

or

```json
{
  "detail": "Admin privileges required"
}
```

### 400 Bad Request
Invalid input data:
```json
{
  "detail": "Username already registered"
}
```

## Token Expiration

- Default token lifetime: **7 days**
- Tokens automatically expire after the configured time
- Users must login again to get a new token
- The expiration time can be configured in `encryption.py` by changing `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`

## Security Best Practices

1. **Never commit JWT_SECRET_KEY to version control**
2. **Use HTTPS in production** to prevent token interception
3. **Store tokens securely** on the client side (e.g., httpOnly cookies, secure storage)
4. **Implement token refresh** for long-running applications
5. **Validate and sanitize all user inputs**
6. **Monitor failed login attempts** and implement rate limiting
7. **Regularly rotate JWT_SECRET_KEY** in production environments

## Initial Admin Setup

To create the first admin user, you'll need to:

1. Register a user through the API
2. Manually update the user in MongoDB to set admin privileges:

```javascript
db.users.updateOne(
  { username: "admin" },
  { 
    $set: { 
      is_verified: true,
      role: "admin"
    } 
  }
)
```

## Migration from Previous Version

If you're upgrading from a version without authentication:

1. **Update environment variables** - Add `JWT_SECRET_KEY`
2. **Create admin user** - Follow initial admin setup
3. **Update client applications** - Add authentication headers
4. **No URL changes needed** - All existing URLs work the same, just add auth headers

## Troubleshooting

### "JWT secret key not found"
- Ensure `JWT_SECRET_KEY` is set in your `.env` file
- Restart the server after updating environment variables

### "Could not validate credentials"
- Check if token is included in the Authorization header
- Verify token hasn't expired
- Ensure token format is `Bearer <token>`

### "User account is not verified"
- Admin must verify the user account in MongoDB
- Update `is_verified` field to `true`

## Support

For issues or questions, please refer to the main documentation or create an issue in the repository.
