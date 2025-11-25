# LikeBot API Documentation

This document describes the full CRUD API for the LikeBot automation system.

## Table of Contents

1. [Base URL](#base-url)
2. [Authentication](#authentication)
3. [Initial Setup](#initial-setup)
4. [Data Models](#data-models)
5. [API Endpoints](#api-endpoints)
   - [Health Check](#health-check)
   - [Authentication Endpoints](#authentication-endpoints)
   - [User Management](#user-management)
   - [Log Streaming](#log-streaming)
   - [Accounts CRUD](#accounts-crud)
   - [Account Locks](#account-locks)
   - [Login Process](#login-process)
   - [Posts CRUD](#posts-crud)
   - [Tasks CRUD](#tasks-crud)
   - [Task Actions](#task-actions)
   - [Proxy Management](#proxy-management)
   - [Reaction Palettes](#reaction-palettes)
   - [Channel Management](#channel-management)
   - [Bulk Operations](#bulk-operations)
   - [Utility Endpoints](#utility-endpoints)
6. [Error Responses](#error-responses)
7. [Notes](#notes)

## Changelog

The following notes summarize important changes in the codebase since the previous documented version (1.0.2):

- Bumped API version to **1.1.1** (see health-check response below).
- Added CORS support that can be configured via the `frontend_http` environment variable (used by the API to allow frontend origins).
- Startup now enforces critical environment variables at launch (e.g., `KEK`, `JWT_SECRET_KEY`, `db_url`) and will fail fast if they are missing.
- The application registers a cleanup handler on process exit to ensure logging/resources are cleaned up (clean shutdown behavior).
- WebSocket `/ws/logs` accepts a `tail` query parameter (0-1000, default 200) and will send token-expiry warnings when an access token is nearing expiry.

**New Endpoints Added:**
- **User Management** (Admin only): GET /users, PUT /users/{username}/role, PUT /users/{username}/verify, DELETE /users/{username}
- **Proxy Management**: Full CRUD for proxy configurations (GET, POST, PUT, DELETE /proxies, GET /proxies/stats/summary)
- **Channel Management**: Full CRUD for Telegram channels (GET, POST, PUT, DELETE /channels, POST /channels/bulk, GET /channels/{chat_id}/subscribers, GET /channels/stats/summary, GET /channels/with-post-counts)
- **Account-Channel Subscriptions**: GET /accounts/{phone_number}/channels, POST /accounts/{phone_number}/channels/sync
- **Reaction Palettes**: Full CRUD for emoji reaction palettes (GET, POST, PUT, DELETE /palettes)
- **Account Locks**: GET /accounts/locks, GET /accounts/{phone_number}/lock, DELETE /accounts/{phone_number}/lock, DELETE /tasks/{task_id}/locks

Notes: these changes are reflected in the `main.py` implementation. The rest of this document describes the current API surface.

## Base URL
```
http://localhost:8080
```
(Default port is 8080, configurable via `backend_port` environment variable)

## Authentication

The API uses **JWT (JSON Web Token) based authentication**. Most endpoints require authentication.

### Authentication Flow

1. **Register** a new user account via `POST /auth/register`
2. Wait for an **admin to verify** your account (new users start as unverified)
3. **Login** via `POST /auth/login` to receive an access token
4. Include the token in subsequent requests using the **Authorization header**:
   ```
   Authorization: Bearer <your_access_token>
   ```

### Token Expiration
- Tokens expire after **7 days** by default
- When a token expires, you'll receive a `401 Unauthorized` response
- Simply login again to get a new token

### Public Endpoints (No Authentication Required)
- `GET /` - Health check
- `POST /auth/register` - User registration
- `POST /auth/login` - User login

### Protected Endpoints (Authentication Required)
All other endpoints require a valid JWT token in the Authorization header.

---

## Initial Setup

Before using the API, you need to set up the environment and create an admin user.

### 1. Environment Variables

Create a `.env` file with the following required variables:

```env
# Encryption key for sensitive data (generate using setup_env.py)
KEK=your_base64_encoded_key_here

# JWT secret key for authentication (generate using setup_env.py)
JWT_SECRET_KEY=your_jwt_secret_here

# MongoDB connection
db_url=mongodb://localhost:27017/
db_name=LikeBot

# Optional: Backend configuration
backend_ip=127.0.0.1
backend_port=8080

# Optional: Frontend CORS
frontend_http=http://localhost:4200
```

### 2. Run Setup Script

Use the provided setup script to generate secrets and create your first admin user:

```bash
python setup_env.py
```

This script will:
- Generate a JWT secret key
- Create an admin user account
- Verify that all required environment variables are set

### 3. Start the API Server

```bash
python main.py
```

The server will start on `http://127.0.0.1:8080` (or your configured backend_ip and backend_port).

### 4. Login and Get Token

Once your admin user is created, you can login to get an access token:

```bash
curl -X POST http://localhost:8080/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=your_admin&password=your_password"
```

Response:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

Use this token in all subsequent requests:
```bash
curl -X GET http://localhost:8080/accounts \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

---

## Data Models

### User
```json
{
  "username": "string (3-50 chars, alphanumeric with underscores/hyphens)",
  "is_verified": "boolean (default: false)",
  "role": "string (user, admin, guest - default: user)",
  "created_at": "string (ISO timestamp)",
  "updated_at": "string (ISO timestamp)"
}
```

**Note**: User passwords are never included in API responses. Password hashes are stored securely using bcrypt with a 72-byte limit.

### Account
```json
{
  "phone_number": "+1234567890",
  "account_id": 123456789,
  "session_name": "string (optional)",
  "session_encrypted": "string (optional)",
  "twofa": false,
  "notes": "string (optional)",
  "status": "NEW|ACTIVE|AUTH_KEY_INVALID|BANNED|DEACTIVATED|RESTRICTED|ERROR (optional)",
  "subscribed_to": ["array of channel chat_ids (optional)"],
  "last_error": "string (optional, last error message)",
  "last_error_type": "string (optional, error class name)",
  "last_error_time": "string (ISO timestamp, optional)",
  "last_success_time": "string (ISO timestamp, optional)",
  "last_checked": "string (ISO timestamp, optional)",
  "flood_wait_until": "string (ISO timestamp, optional)",
  "last_channel_sync_at": "string (ISO timestamp, optional)",
  "last_channel_sync_count": "integer (optional)",
  "created_at": "string (ISO timestamp, optional)",
  "updated_at": "string (ISO timestamp, optional)"
}
```

**Account Status Values:**
- `NEW` - Account created but not logged in
- `ACTIVE` - Account is healthy and ready to use
- `AUTH_KEY_INVALID` - Session invalid, needs re-login
- `BANNED` - Account banned by Telegram
- `DEACTIVATED` - Account deactivated by Telegram
- `RESTRICTED` - Account has restrictions
- `ERROR` - Generic error state

**Note**: For security reasons, password information is not included in account responses. Use the secure password endpoint `/accounts/{phone_number}/password` to retrieve password information when needed.

### Post
```json
{
  "post_id": "integer (optional, auto-generated)",
  "message_link": "string",
  "chat_id": "integer (optional)",
  "message_id": "integer (optional)",
  "is_validated": "boolean (default: false)",
  "created_at": "string (ISO timestamp)",
  "updated_at": "string (ISO timestamp)"
}
```

### Task
```json
{
  "task_id": "integer (optional, auto-generated)",
  "name": "string",
  "description": "string (optional)",
  "post_ids": ["array of integers"],
  "accounts": ["array of phone numbers"],
  "action": {
    "type": "string (react|comment|undo_reaction|undo_comment)",
    "palette": "string (palette name from /palettes) - for react actions",
    "content": "string (1-4096 chars) - for comment actions"
  },
  "status": "string (PENDING|RUNNING|PAUSED|FINISHED|FAILED|CRASHED)",
  "created_at": "string (ISO timestamp)",
  "updated_at": "string (ISO timestamp)"
}
```

**Task Status Values:**
- `PENDING` - Task created but not yet started
- `RUNNING` - Task is currently executing
- `PAUSED` - Task execution is paused
- `FINISHED` - Task completed successfully (at least one worker succeeded)
- `FAILED` - Task ran correctly but all workers failed due to account issues (banned, session expired, etc.)
- `CRASHED` - Task encountered infrastructure/system-level errors

### Proxy
```json
{
  "proxy_name": "string (unique identifier)",
  "type": "string (http|socks4|socks5)",
  "host": "string (IP or hostname)",
  "port": "integer (1-65535)",
  "username": "string (optional)",
  "rdns": "boolean (resolve DNS remotely, default: true)",
  "active": "boolean",
  "connected_accounts": "integer (number of accounts using this proxy)",
  "notes": "string (optional)",
  "created_at": "string (ISO timestamp)",
  "updated_at": "string (ISO timestamp)"
}
```

**Note**: Proxy passwords are encrypted using AES-256-GCM and are never returned in API responses.

### Channel
```json
{
  "chat_id": "integer (Telegram chat ID, unique identifier)",
  "channel_name": "string (channel title/name)",
  "is_private": "boolean",
  "has_enabled_reactions": "boolean",
  "reactions_only_for_subscribers": "boolean",
  "discussion_chat_id": "integer (optional, linked discussion group)",
  "tags": ["array of strings"],
  "created_at": "string (ISO timestamp)",
  "updated_at": "string (ISO timestamp)"
}
```

**Note**: Chat IDs are automatically normalized to handle both -100 prefixed and non-prefixed forms.

### Reaction Palette
```json
{
  "palette_name": "string (unique identifier, lowercase)",
  "emojis": ["array of emoji strings"],
  "ordered": "boolean (true=sequential, false=random)",
  "description": "string (optional)",
  "created_at": "string (ISO timestamp)",
  "updated_at": "string (ISO timestamp)"
}
```

**Note**: If `ordered` is true, reactions are applied sequentially from the emoji list. If false, emojis are chosen randomly.

## API Endpoints

### Health Check

#### GET /
Get server status.

**Authentication**: Not required

**Response:**
```json
{
  "message": "LikeBot API Server is running",
  "version": "1.1.1"
}
```

---

## Authentication Endpoints

### POST /auth/register
Register a new user account.

**Authentication**: Not required

**Request Body:**
```json
{
  "username": "john_doe",
  "password": "securepassword123",
  "role": "user"
}
```

**Response (201 Created):**
```json
{
  "username": "john_doe",
  "is_verified": false,
  "role": "user",
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z"
}
```

**Notes:**
- New users start as **unverified** and require admin approval
- Username must be 3-50 characters, alphanumeric with underscores/hyphens
- Password must be at least 6 characters
- Passwords exceeding 72 bytes (bcrypt limit) will be rejected

**Error Responses:**
- `400`: Username already registered or password exceeds bcrypt limit
- `500`: Failed to create user

---

### POST /auth/login
Login with username and password to get a JWT access token.

**Authentication**: Not required

**Request Body (Form Data):**
```
username=john_doe&password=securepassword123
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

**Notes:**
- Use the `access_token` in subsequent requests as: `Authorization: Bearer <token>`
- Tokens expire after 7 days by default

**Error Responses:**
- `400`: Password exceeds bcrypt's 72-byte limit
- `401`: User not found or incorrect password
- `403`: User is not verified (needs admin approval)

---

### GET /auth/me
Get information about the currently authenticated user.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "username": "john_doe",
  "is_verified": true,
  "role": "user",
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z"
}
```

**Error Responses:**
- `401`: Invalid or missing token
- `403`: User is not verified

---

## User Management

All user management endpoints require **admin privileges**.

### GET /users
Get all users in the system.

**Authentication**: Required (Admin only)

**Headers:**
```
Authorization: Bearer <your_admin_access_token>
```

**Response:**
```json
[
  {
    "username": "john_doe",
    "is_verified": true,
    "role": "user",
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z"
  }
]
```

**Notes:**
- Password hashes are excluded from the response for security
- Only accessible by admin users

**Error Responses:**
- `401`: Invalid or missing token
- `403`: User is not admin

---

### PUT /users/{username}/role
Update a user's role.

**Authentication**: Required (Admin only)

**Headers:**
```
Authorization: Bearer <your_admin_access_token>
```

**Query Parameters:**
- `role` (required): New role to assign (admin, user, guest)

**Example:**
```
PUT /users/john_doe/role?role=admin
```

**Response:**
```json
{
  "message": "User john_doe role updated to admin",
  "username": "john_doe",
  "new_role": "admin"
}
```

**Notes:**
- Cannot change your own role (ask another admin)
- Valid roles: admin, user, guest

**Error Responses:**
- `401`: Invalid or missing token
- `403`: User is not admin
- `404`: User not found
- `400`: Attempting to change your own role

---

### PUT /users/{username}/verify
Update a user's verification status.

**Authentication**: Required (Admin only)

**Headers:**
```
Authorization: Bearer <your_admin_access_token>
```

**Query Parameters:**
- `is_verified` (required): Verification status (true/false)

**Example:**
```
PUT /users/john_doe/verify?is_verified=true
```

**Response:**
```json
{
  "message": "User john_doe verification status updated",
  "username": "john_doe",
  "is_verified": true
}
```

**Notes:**
- Verified users can access the API
- Unverified users are blocked after login

**Error Responses:**
- `401`: Invalid or missing token
- `403`: User is not admin
- `404`: User not found

---

### DELETE /users/{username}
Delete a user from the system.

**Authentication**: Required (Admin only)

**Headers:**
```
Authorization: Bearer <your_admin_access_token>
```

**Response:**
```json
{
  "message": "User john_doe deleted successfully",
  "username": "john_doe"
}
```

**Notes:**
- Cannot delete yourself (ask another admin)
- Cannot delete the last verified admin user

**Error Responses:**
- `401`: Invalid or missing token
- `403`: User is not admin
- `404`: User not found
- `400`: Cannot delete yourself or last admin

---

## Log Streaming

### Websocket /ws/logs
Stream log output in real time. Connect with a websocket client (e.g., browser `WebSocket`, `wscat`).

**Authentication**: Required (via query parameter)

**URL:** `ws://localhost:8080/ws/logs?token=<your_access_token>&log_file=main.log&tail=100`

**Query Parameters:**
- `token` (required): JWT access token for authentication
- `log_file` (optional): Name of the log file inside the configured logs directory. Defaults to `main.log`.
- `tail` (optional): Number of trailing lines to send immediately after connection (0-1000, default 200).

**Message Format:**
- Server sends plain-text log lines as they are written.
- Warning messages sent as JSON when token is about to expire (within 5 minutes)
- Error messages are sent as JSON objects with fields `type` and `message` before the socket closes.

**WebSocket Close Codes:**
- `4401`: Authentication required or invalid token
- `4403`: Token expired or user not verified
- `1003`: Log file not found
- `1011`: Log streaming interrupted by error

**Example Usage (JavaScript):**
```javascript
const token = 'your_jwt_token_here';
const socket = new WebSocket(`ws://localhost:8080/ws/logs?token=${token}&tail=100`);
socket.onmessage = (event) => {
  try {
    const data = JSON.parse(event.data);
    if (data.type === 'warning') {
      console.warn(data.message);
    } else if (data.type === 'error') {
      console.error(data.message);
    }
  } catch {
    // Plain text log line
    console.log(event.data);
  }
};
socket.onerror = (event) => console.error('Log stream error', event);
```

---

## Accounts CRUD

### GET /accounts
Get all accounts with optional filtering.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Query Parameters:**
- `phone_number` (optional): Filter by phone number

**Response:**
```json
[
  {
    "phone_number": "+1234567890",
    "account_id": "123456789",
    "session_name": "user_session"
  }
]
```

### GET /accounts/{phone_number}
Get a specific account by phone number.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "phone_number": "+1234567890",
  "account_id": "123456789",
  "session_name": "user_session"
}
```

### POST /accounts
Create a new account in database without login. Legacy endpoint.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Request Body:**
```json
{
  "phone_number": "+1234567890",
  "session_name": "user_session",
  "twofa": true,
  "password": "mypassword123",
  "notes": "Test account"
}
```

**Note**: The `password` field is sent as plain text but is immediately encrypted server-side for security. It's never stored in plain text.

**Error Responses:**
- `409`: Account already exists
- `500`: Failed to create account

**Response:**
```json
{
  "message": "Account +1234567890 created successfully"
}
```

### PUT /accounts/{phone_number}
Update an existing account.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Request Body:**
```json
{
  "account_id": 123456789,
  "session_name": "new_session_name",
  "session_encrypted": "encrypted_session_string",
  "twofa": true,
  "password": "newpassword123",
  "notes": "Updated notes",
  "status": "ACTIVE"
}
```

**Note**: The `password` field is sent as plain text but is immediately encrypted server-side for security. Setting `password` to an empty string or null will disable 2FA and clear the password.

**Error Responses:**
- `404`: Account not found
- `400`: No update data provided
- `500`: Failed to update account

**Response:**
```json
{
  "message": "Account +1234567890 updated successfully"
}
```

### DELETE /accounts/{phone_number}
Delete an account.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "message": "Account +1234567890 deleted successfully"
}
```

### PUT /accounts/{phone_number}/validate
Validate an account by testing its connection to Telegram.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "message": "Account +1234567890 validated successfully",
  "account_id": 123456789,
  "account_status": "ACTIVE",
  "has_session": true
}
```

**Error Responses:**
- `404`: Account not found
- `400`: Account has no session (needs login first)
- `500`: Connection failed or other validation errors

### GET /accounts/{phone_number}/password
Get account password securely. **Requires admin privileges.**

**Authentication**: Required (Admin only)

**Headers:**
```
Authorization: Bearer <your_admin_access_token>
```

**Response:**
```json
{
  "phone_number": "+1234567890",
  "has_password": true,
  "password": "decrypted_password_here"
}
```

**Response (No Password):**
```json
{
  "phone_number": "+1234567890",
  "has_password": false,
  "password": null
}
```

**Note**: This endpoint requires admin privileges. In production, implement additional security measures such as IP restrictions or multi-factor authentication.

**Error Responses:**
- `401`: Invalid or missing token
- `403`: User is not admin
- `404`: Account not found
- `500`: Failed to decrypt password

---

## Account Locks

Account locking prevents multiple tasks from using the same Telegram account simultaneously. When a task starts, it acquires locks on all accounts it uses. These locks are automatically released when the task completes or is stopped.

### GET /accounts/locks
Get all currently locked accounts and which tasks hold them.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "count": 2,
  "locks": [
    {
      "phone_number": "+1234567890",
      "task_id": 1,
      "locked_at": "2025-01-15T10:30:00Z"
    },
    {
      "phone_number": "+0987654321",
      "task_id": 1,
      "locked_at": "2025-01-15T10:30:01Z"
    }
  ]
}
```

### GET /accounts/{phone_number}/lock
Check if a specific account is currently locked.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response (locked):**
```json
{
  "phone_number": "+1234567890",
  "is_locked": true,
  "task_id": 1,
  "locked_at": "2025-01-15T10:30:00Z"
}
```

**Response (not locked):**
```json
{
  "phone_number": "+1234567890",
  "is_locked": false,
  "task_id": null,
  "locked_at": null
}
```

### DELETE /accounts/{phone_number}/lock
Force release a lock on an account. Use with caution - this may cause issues if a task is actively using the account.

**Authentication**: Required (Admin only)

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "message": "Lock on account +1234567890 released successfully",
  "previous_task_id": 1
}
```

**Error Responses:**
- `401`: Invalid or missing token
- `403`: User is not admin
- `404`: Account is not locked

### DELETE /tasks/{task_id}/locks
Release all account locks held by a specific task. Useful for cleanup after a task crashes or is forcefully stopped.

**Authentication**: Required (Admin only)

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "message": "Released 3 locks for task 1",
  "released_count": 3
}
```

**Error Responses:**
- `401`: Invalid or missing token
- `403`: User is not admin

---

## Login Process

### POST /accounts/create/start
Start the login process for a Telegram account. Sends verification code to the phone number.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Query Parameters:**
- `phone_number` (required): Phone number with country code (e.g., +1234567890)
- `password` (optional): Password for 2FA (will be encrypted server-side)
- `session_name` (optional): Custom session name
- `notes` (optional): Account notes

**Response:**
```json
{
  "status": "wait_code",
  "login_session_id": "uuid-string",
  "message": "Verification code sent to +1234567890"
}
```

**Status Values:**
- `wait_code`: Waiting for verification code from user
- `wait_2fa`: Waiting for 2FA password from user
- `processing`: Processing authentication
- `done`: Login completed successfully
- `failed`: Login failed with error

**Error Responses:**
- `401`: Invalid or missing token
- `500`: Failed to start login

### POST /accounts/create/verify
Submit verification code to continue the login process.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Query Parameters:**
- `login_session_id` (required): Login session ID from /accounts/create/start
- `code` (required): Verification code from Telegram

**Note**: 2FA passwords must be provided during `/accounts/create/start`, not here. If 2FA is required but no password was provided during start, this endpoint will return an error instructing to restart the login process.

**Response:**
```json
{
  "status": "processing",
  "message": "Verification code submitted, processing login..."
}
```

**Error Responses:**
- `401`: Invalid or missing token
- `404`: Login session not found or expired
- `400`: Missing verification code or 2FA password required but not provided during start
- `500`: Failed to verify login

### GET /accounts/create/status
Check the status of an ongoing login process. Used for polling by the frontend.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Query Parameters:**
- `login_session_id` (required): Login session ID from /accounts/create/start

**Response (Success):**
```json
{
  "status": "done",
  "phone_number": "+1234567890",
  "created_at": "2025-01-01T00:00:00Z",
  "message": "Login completed successfully",
  "account_created": true
}
```

**Response (Waiting for Code):**
```json
{
  "status": "wait_code",
  "phone_number": "+1234567890",
  "created_at": "2025-01-01T00:00:00Z",
  "message": "Waiting for verification code"
}
```

**Response (Waiting for 2FA):**
```json
{
  "status": "wait_2fa",
  "phone_number": "+1234567890",
  "created_at": "2025-01-01T00:00:00Z",
  "message": "Waiting for 2FA password"
}
```

**Response (Failed):**
```json
{
  "status": "failed",
  "phone_number": "+1234567890",
  "created_at": "2025-01-01T00:00:00Z",
  "message": "Login failed",
  "error": "Invalid verification code"
}
```

**Error Responses:**
- `401`: Invalid or missing token
- `404`: Login session not found or expired
- `500`: Failed to get login status

---

## Posts CRUD

### GET /posts
Get all posts with optional filtering.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Query Parameters:**
- `post_id` (optional): Filter by post ID
- `chat_id` (optional): Filter by chat ID
- `validated_only` (optional): Filter by validation status (true/false)

**Response:**
```json
[
  {
    "post_id": 1,
    "message_link": "https://t.me/channel/123",
    "chat_id": -1001234567890,
    "message_id": 123,
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z"
  }
]
```

### GET /posts/{post_id}
Get a specific post by ID.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "post_id": 1,
  "message_link": "https://t.me/channel/123",
  "chat_id": -1001234567890,
  "message_id": 123,
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z"
}
```

### POST /posts
Create a new post.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Request Body:**
```json
{
  "message_link": "https://t.me/channel/123",
  "post_id": 1,
  "chat_id": -1001234567890,
  "message_id": 123
}
```

**Response:**
```json
{
  "message": "Post created successfully",
  "post_id": 1
}
```

### PUT /posts/{post_id}
Update an existing post.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Request Body:**
```json
{
  "message_link": "https://t.me/channel/456",
  "chat_id": -1001234567891,
  "message_id": 456
}
```

**Response:**
```json
{
  "message": "Post 1 updated successfully"
}
```

### DELETE /posts/{post_id}
Delete a post.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "message": "Post 1 deleted successfully"
}
```

---

## Tasks CRUD

### GET /tasks
Get all tasks with optional filtering.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Query Parameters:**
- `task_id` (optional): Filter by task ID
- `status` (optional): Filter by task status
- `name` (optional): Filter by task name (partial match)

**Response:**
```json
[
  {
    "task_id": 1,
    "name": "React to posts",
    "description": "React to specific posts with positive emojis",
    "post_ids": [1, 2, 3],
    "accounts": ["+1234567890", "+0987654321"],
    "action": {
      "type": "react",
      "palette": "positive"
    },
    "status": "PENDING",
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z"
  }
]
```

### GET /tasks/{task_id}
Get a specific task by ID.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "task_id": 1,
  "name": "React to posts",
  "description": "React to specific posts with positive emojis",
  "post_ids": [1, 2, 3],
  "accounts": ["+1234567890", "+0987654321"],
  "action": {
    "type": "react",
    "palette": "positive"
  },
  "status": "PENDING",
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z"
}
```

### POST /tasks
Create a new task.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Request Body:**
```json
{
  "name": "React to posts",
  "description": "React to specific posts with positive emojis",
  "post_ids": [1, 2, 3],
  "accounts": ["+1234567890", "+0987654321"],
  "action": {
    "type": "react",
    "palette": "positive"
  }
}
```

**Response:**
```json
{
  "message": "Task 'React to posts' created successfully",
  "task_id": 1
}
```

### PUT /tasks/{task_id}
Update an existing task.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Request Body:**
```json
{
  "name": "Updated task name",
  "description": "Updated description",
  "status": "PAUSED"
}
```

**Response:**
```json
{
  "message": "Task 1 updated successfully"
}
```

### DELETE /tasks/{task_id}
Delete a task.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "message": "Task 1 deleted successfully"
}
```

---

## Task Actions

### GET /tasks/{task_id}/status
Get the current status of a task.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "task_id": 1,
  "status": "RUNNING"
}
```

### POST /tasks/{task_id}/start
Start task execution.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "message": "Task 1 started successfully"
}
```

### POST /tasks/{task_id}/pause
Pause task execution.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "message": "Task 1 paused successfully"
}
```

### POST /tasks/{task_id}/resume
Resume task execution.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "message": "Task 1 resumed successfully"
}
```

### GET /tasks/{task_id}/report
Get execution report for a task. By default returns the latest run report.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Query Parameters:**
- `report_type` (optional): Type of report (success, all, errors). Default: "success"
- `run_id` (optional): Specific run ID to get report for. If not provided, returns latest run report.

**Response:**
```json
{
  "task_id": 1,
  "run_id": "abc123-def456-ghi789",
  "report": {
    "events": [...],
    "summary": {...}
  }
}
```

### GET /tasks/{task_id}/runs
Get all execution runs for a specific task, ordered by most recent first.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "task_id": 1,
  "total_runs": 5,
  "runs": [
    {
      "run_id": "abc123-def456-ghi789",
      "task_id": "1",
      "started_at": "2025-01-01T10:00:00Z",
      "finished_at": "2025-01-01T10:05:00Z",
      "status": "success",
      "event_count": 25,
      "meta": {
        "task_name": "React to posts",
        "action": "react"
      }
    }
  ]
}
```

### GET /tasks/{task_id}/runs/{run_id}/report
Get execution report for a specific run of a task.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Query Parameters:**
- `report_type` (optional): Type of report (success, all, errors). Default: "success"

**Response:**
```json
{
  "task_id": 1,
  "run_id": "abc123-def456-ghi789",
  "report": {
    "events": [...],
    "summary": {...}
  }
}
```

### GET /runs
Get all execution runs across all tasks.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "total_tasks": 3,
  "total_runs": 15,
  "tasks": [
    {
      "task_id": "1",
      "run_count": 5,
      "runs": [...]
    },
    {
      "task_id": "2", 
      "run_count": 10,
      "runs": [...]
    }
  ]
}
```

### DELETE /tasks/{task_id}/runs/{run_id}
Delete a specific run and all its events.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "message": "Run abc123-def456-ghi789 deleted successfully",
  "runs_deleted": 1,
  "events_deleted": 25
}
```

### DELETE /tasks/{task_id}/runs
Delete all runs and their events for a specific task.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "message": "All runs for task 1 deleted successfully",
  "runs_deleted": 5,
  "events_deleted": 125
}
```

---

## Bulk Operations

### POST /accounts/bulk
Create multiple accounts in bulk.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Request Body:**
```json
[
  {
    "phone_number": "+1234567890",
    "session_name": "user1_session",
    "twofa": true,
    "password": "password123",
    "notes": "User 1 account"
  },
  {
    "phone_number": "+0987654321",
    "session_name": "user2_session",
    "twofa": false,
    "notes": "User 2 account"
  }
]
```

**Response:**
```json
{
  "results": [
    {
      "phone_number": "+1234567890",
      "status": "success",
      "message": "Account created successfully"
    },
    {
      "phone_number": "+0987654321",
      "status": "skipped",
      "message": "Account already exists"
    }
  ]
}
```

### POST /posts/bulk
Create multiple posts in bulk.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Request Body:**
```json
[
  {
    "message_link": "https://t.me/channel/123"
  },
  {
    "message_link": "https://t.me/channel/456"
  }
]
```

### DELETE /accounts/bulk
Delete multiple accounts in bulk.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Request Body:**
```json
["+1234567890", "+0987654321"]
```

### DELETE /posts/bulk
Delete multiple posts in bulk.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Request Body:**
```json
[1, 2, 3]
```

---

## Utility Endpoints

### GET /stats
Get statistics about accounts, posts, and tasks.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "accounts": {
    "total": 5
  },
  "posts": {
    "total": 10,
    "validated": 8,
    "unvalidated": 2
  },
  "tasks": {
    "total": 3,
    "by_status": {
      "PENDING": 1,
      "RUNNING": 1,
      "FINISHED": 1
    }
  }
}
```

### POST /posts/{post_id}/validate
Validate a specific post by extracting chat_id and message_id from its link.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "message": "Post 1 validated successfully",
  "chat_id": -1001234567890,
  "message_id": 123
}
```

---

## Error Responses

All endpoints return appropriate HTTP status codes:

- `200`: Success
- `201`: Created
- `400`: Bad Request (validation errors, missing parameters)
- `401`: Unauthorized (invalid or missing authentication token)
- `403`: Forbidden (user not verified or insufficient permissions)
- `404`: Not Found
- `409`: Conflict (resource already exists)
- `500`: Internal Server Error

Error response format:
```json
{
  "detail": "Error message describing what went wrong"
}
```

### Common Authentication Errors

**401 Unauthorized:**
```json
{
  "detail": "Could not validate credentials"
}
```

**403 Forbidden (User Not Verified):**
```json
{
  "detail": "User account is not verified. Please contact an administrator."
}
```

**403 Forbidden (Admin Required):**
```json
{
  "detail": "Admin privileges required"
}
```

---

## Notes

1. **Token Expiration**: JWT tokens expire after 7 days. When you receive a 401 error, try logging in again to get a fresh token.

2. **Password Security**: 
   - Passwords are hashed using bcrypt with a 72-byte limit
   - Account passwords (for 2FA) are encrypted using AES-256-GCM
   - Never log or store passwords in plain text

3. **User Verification**: New users start as unverified and require admin approval before they can use protected endpoints.

4. **WebSocket Authentication**: WebSocket connections require token authentication via query parameter since headers can't be easily set in browser WebSocket API.

5. **CORS**: The API supports CORS for frontend integration. Configure allowed origins via the `frontend_http` environment variable.

6. **Rate Limiting**: Consider implementing rate limiting for production deployments to prevent abuse.

7. **HTTPS**: Always use HTTPS in production to protect authentication tokens and sensitive data in transit.
```json
{
  "detail": "Error message describing what went wrong"
}
```

---

## Action Types

Task actions define what operation to perform on posts. Actions are specified in the `action` field when creating or updating tasks.

### React Action
```json
{
  "type": "react",
  "palette": "palette_name"
}
```

**Note**: Palettes are dynamically managed via the `/palettes` endpoints. Use `GET /palettes` to see available palettes.

### Comment Action
```json
{
  "type": "comment",
  "content": "Your comment text here (1-4096 characters)"
}
```

### Undo Reaction Action
```json
{
  "type": "undo_reaction"
}
```

Removes any reaction previously added by the account.

### Undo Comment Action
```json
{
  "type": "undo_comment"
}
```

Deletes any comment previously posted by the account.
```

---

## Proxy Management

Manage proxy configurations for Telegram accounts. Proxies can be assigned to accounts to route their connections through specific servers.

### GET /proxies
Get all proxies with optional filtering.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Query Parameters:**
- `proxy_name` (optional): Filter by proxy name
- `active_only` (optional): Filter by active status (true/false)

**Response:**
```json
[
  {
    "proxy_name": "proxy1",
    "type": "socks5",
    "host": "192.168.1.100",
    "port": 1080,
    "username": "proxyuser",
    "rdns": true,
    "active": true,
    "connected_accounts": 3,
    "notes": "Primary proxy",
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z"
  }
]
```

**Notes:**
- Passwords are never returned in responses for security

---

### GET /proxies/{proxy_name}
Get a specific proxy by name.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "proxy_name": "proxy1",
  "type": "socks5",
  "host": "192.168.1.100",
  "port": 1080,
  "username": "proxyuser",
  "rdns": true,
  "active": true,
  "connected_accounts": 3,
  "notes": "Primary proxy",
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z"
}
```

**Error Responses:**
- `404`: Proxy not found

---

### POST /proxies
Create a new proxy configuration.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Query Parameters:**
- `proxy_name` (required): Unique name for the proxy
- `host` (required): Proxy server IP or hostname
- `port` (required): Proxy server port (1-65535)
- `proxy_type` (optional, default="socks5"): Type of proxy (http, socks4, socks5)
- `username` (optional): Proxy authentication username
- `password` (optional): Proxy authentication password (will be encrypted)
- `rdns` (optional, default=true): Resolve DNS remotely
- `active` (optional, default=true): Whether proxy is active
- `notes` (optional): Optional notes about the proxy

**Example:**
```
POST /proxies?proxy_name=proxy1&host=192.168.1.100&port=1080&proxy_type=socks5&username=user&password=pass
```

**Response (201):**
```json
{
  "proxy_name": "proxy1",
  "type": "socks5",
  "host": "192.168.1.100",
  "port": 1080,
  "username": "user",
  "rdns": true,
  "active": true,
  "connected_accounts": 0,
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z"
}
```

**Notes:**
- Password is encrypted server-side before storage
- Password is never returned in response
- Proxy name must be unique

**Error Responses:**
- `400`: Invalid parameters, validation error, or proxy already exists

---

### PUT /proxies/{proxy_name}
Update an existing proxy configuration.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Query Parameters (all optional):**
- `host`: Proxy server IP or hostname
- `port`: Proxy server port (1-65535)
- `proxy_type`: Type of proxy (http, socks4, socks5)
- `username`: Proxy authentication username
- `password`: Proxy authentication password (will be encrypted)
- `rdns`: Resolve DNS remotely (true/false)
- `active`: Whether proxy is active (true/false)
- `notes`: Notes about the proxy

**Example:**
```
PUT /proxies/proxy1?host=192.168.1.101&port=1081
```

**Response:**
```json
{
  "proxy_name": "proxy1",
  "type": "socks5",
  "host": "192.168.1.101",
  "port": 1081,
  "username": "user",
  "rdns": true,
  "active": true,
  "connected_accounts": 3,
  "updated_at": "2025-01-01T12:00:00Z"
}
```

**Notes:**
- Only provided fields will be updated
- Password is never returned in response

**Error Responses:**
- `404`: Proxy not found
- `400`: No fields provided or validation error

---

### DELETE /proxies/{proxy_name}
Delete a proxy configuration.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "message": "Proxy 'proxy1' deleted successfully",
  "proxy_name": "proxy1"
}
```

**Error Responses:**
- `400`: Proxy is in use (has connected accounts)
- `404`: Proxy not found

---

### GET /proxies/stats/summary
Get proxy usage statistics.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "total_proxies": 5,
  "active_proxies": 3,
  "inactive_proxies": 2,
  "total_connected_accounts": 15,
  "least_used_proxy": {
    "proxy_name": "proxy2",
    "connected_accounts": 1
  },
  "most_used_proxy": {
    "proxy_name": "proxy1",
    "connected_accounts": 8
  }
}
```
```

---

## Reaction Palettes CRUD

Reaction palettes let you define named emoji sets that tasks can reference when performing react actions. The API provides CRUD endpoints to manage palettes.

### GET /palettes
Get all palettes or filter by name.

Authentication: Required

Query parameters:
- `palette_name` (optional): Filter by palette name

Response (200):
```json
[
  {
    "palette_name": "positive",
    "emojis": ["👍","❤️","🔥"],
    "ordered": false,
    "description": "Positive reactions palette",
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z"
  }
]
```

### GET /palettes/{palette_name}
Get a specific palette by name.

Authentication: Required

Response (200):
```json
{
  "palette_name": "positive",
  "emojis": ["👍","❤️","🔥"],
  "ordered": false,
  "description": "Positive reactions palette",
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z"
}
```

Error responses:
- `404`: Palette not found

### POST /palettes
Create a new palette.

Authentication: Required

Query parameters (form/query style):
- `palette_name` (required): Unique name for the palette (case-insensitive; stored lowercased)
- `emojis` (required): Comma-separated list of emojis (e.g. "👍,❤️,🔥")
- `ordered` (optional, default false): If true, the palette will be used sequentially by tasks; if false, emojis are chosen randomly.
- `description` (optional): Human-friendly description

Notes:
- The endpoint parses the comma-separated `emojis` string into an array and validates at least one emoji is provided.
- `palette_name` is normalized to lowercase when stored.

Response (201):
```json
{
  "message": "Palette 'positive' created successfully",
  "palette_name": "positive",
  "emoji_count": 3
}
```

Error responses:
- `400`: Validation error (e.g., no emojis provided)
- `409`: Palette already exists

### PUT /palettes/{palette_name}
Update an existing palette. Only provided fields are changed.

Authentication: Required

Query parameters (optional):
- `emojis`: Comma-separated list of emojis (replaces existing list)
- `ordered`: true/false
- `description`: New description

Response (200):
```json
{ "message": "Palette 'positive' updated successfully" }
```

Error responses:
- `400`: No fields provided or invalid emoji list
- `404`: Palette not found

### DELETE /palettes/{palette_name}
Delete a palette.

Authentication: Required

Response (200):
```json
{ "message": "Palette 'positive' deleted successfully" }
```

Warning: Tasks that reference a deleted palette will fail to execute until the palette is recreated or tasks are updated to use an existing palette.

---

## Channel Management

Manage Telegram channel metadata and subscriptions. Channels represent Telegram channels/groups that accounts are subscribed to.

### GET /channels
Get all channels with optional filtering.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Query Parameters:**
- `chat_id` (optional): Filter by chat ID
- `tag` (optional): Filter by tag
- `name` (optional): Search by channel name (partial match)

**Response:**
```json
[
  {
    "chat_id": -1001234567890,
    "channel_name": "My Channel",
    "is_private": false,
    "has_enabled_reactions": true,
    "reactions_only_for_subscribers": false,
    "discussion_chat_id": -1009876543210,
    "tags": ["crypto", "news"],
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z"
  }
]
```

---

### GET /channels/{chat_id}
Get a specific channel by chat ID.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "chat_id": -1001234567890,
  "channel_name": "My Channel",
  "is_private": false,
  "has_enabled_reactions": true,
  "reactions_only_for_subscribers": false,
  "discussion_chat_id": -1009876543210,
  "tags": ["crypto", "news"],
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z"
}
```

**Error Responses:**
- `404`: Channel not found

---

### POST /channels/bulk
Get multiple channels by their chat IDs in a single request.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Request Body:**
```json
[-1001234567890, 123456789, -1009876543210]
```

**Response:**
```json
[
  {
    "chat_id": -1001234567890,
    "channel_name": "Channel 1",
    "is_private": false,
    "tags": ["crypto"],
    "created_at": "2025-01-01T00:00:00Z"
  },
  {
    "chat_id": -1009876543210,
    "channel_name": "Channel 2",
    "is_private": true,
    "tags": [],
    "created_at": "2025-01-02T00:00:00Z"
  }
]
```

**Notes:**
- Accepts both normalized and -100 prefixed chat IDs
- Returns only channels that exist (may be fewer than requested)
- Missing channels are silently omitted from response

---

### POST /channels
Create a new channel entry.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Query Parameters:**
- `chat_id` (required): Telegram chat ID (with or without -100 prefix)
- `channel_name` (required): Channel name/title
- `is_private` (optional, default=false): Is the channel private?
- `has_enabled_reactions` (optional, default=true): Does channel have reactions enabled?
- `reactions_only_for_subscribers` (optional, default=false): Are reactions only for subscribers?
- `discussion_chat_id` (optional): Linked discussion group chat ID
- `tags` (optional): Comma-separated list of tags

**Example:**
```
POST /channels?chat_id=-1001234567890&channel_name=MyChannel&tags=crypto,news
```

**Response:**
```json
{
  "chat_id": -1001234567890,
  "channel_name": "MyChannel",
  "is_private": false,
  "has_enabled_reactions": true,
  "tags": ["crypto", "news"],
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z"
}
```

**Notes:**
- Chat IDs are automatically normalized (handles both -100 prefixed and non-prefixed forms)
- Tags are trimmed and empty tags are removed

**Error Responses:**
- `400`: Chat ID already exists or validation error

---

### PUT /channels/{chat_id}
Update an existing channel.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Query Parameters (all optional):**
- `channel_name`: Channel name/title
- `is_private`: Is the channel private? (true/false)
- `has_enabled_reactions`: Does channel have reactions enabled? (true/false)
- `reactions_only_for_subscribers`: Are reactions only for subscribers? (true/false)
- `discussion_chat_id`: Linked discussion group chat ID
- `tags`: Comma-separated list of tags

**Example:**
```
PUT /channels/-1001234567890?channel_name=Updated+Name&tags=crypto,tech
```

**Response:**
```json
{
  "chat_id": -1001234567890,
  "channel_name": "Updated Name",
  "tags": ["crypto", "tech"],
  "updated_at": "2025-01-01T12:00:00Z"
}
```

**Error Responses:**
- `404`: Channel not found
- `400`: No fields provided for update

---

### DELETE /channels/{chat_id}
Delete a channel from the database.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "message": "Channel with chat_id -1001234567890 deleted successfully",
  "chat_id": -1001234567890
}
```

**Notes:**
- This does NOT delete the actual Telegram channel, only the local database entry
- Associated posts will remain in the database

**Error Responses:**
- `404`: Channel not found

---

### GET /channels/stats/summary
Get channel statistics.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
{
  "total_channels": 10,
  "private_channels": 3,
  "public_channels": 7,
  "channels_with_reactions": 8,
  "tag_distribution": {
    "crypto": 5,
    "news": 3,
    "tech": 4
  }
}
```

---

### GET /channels/with-post-counts
Get all channels with their post counts.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
[
  {
    "chat_id": -1001234567890,
    "channel_name": "My Channel",
    "post_count": 15,
    "tags": ["crypto", "news"],
    "created_at": "2025-01-01T00:00:00Z"
  }
]
```

**Notes:**
- Returns all channels with an additional `post_count` field
- Post count indicates how many posts exist for each channel

---

### GET /channels/{chat_id}/subscribers
Get all accounts that are subscribed to a specific channel.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
[
  {
    "phone_number": "+1234567890",
    "account_id": 123456789,
    "session_name": "user1_session",
    "status": "ACTIVE",
    "subscribed_to": [-1001234567890, -1009876543210],
    "created_at": "2025-01-01T00:00:00Z"
  }
]
```

**Notes:**
- Accepts both normalized and -100 prefixed chat IDs
- Returns accounts in secure format (without passwords)

---

### GET /accounts/{phone_number}/channels
Get all channels that an account is subscribed to.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Response:**
```json
[
  {
    "chat_id": -1001234567890,
    "channel_name": "Subscribed Channel",
    "is_private": false,
    "tags": ["crypto"]
  }
]
```

**Notes:**
- Returns Channel objects based on the account's `subscribed_to` list
- Only returns channels that exist in the database

**Error Responses:**
- `404`: Account not found

---

### POST /accounts/{phone_number}/channels/sync
Sync account's subscribed channels from Telegram.

**Authentication**: Required

**Headers:**
```
Authorization: Bearer <your_access_token>
```

**Description:**
Connects to Telegram, fetches all channels the account is subscribed to, updates the account's `subscribed_to` field, and upserts channel data to the channels collection.

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

**Notes:**
- Requires account to have a valid session (must be logged in first)
- Updates `last_channel_sync_at` and `last_channel_sync_count` metadata on the account
- Automatically creates channel entries for newly discovered channels

**Error Responses:**
- `400`: Account has no valid session
- `404`: Account not found
- `500`: Failed to connect to Telegram or sync error

---

## Notes

1. **Phone Numbers**: Should include country code (e.g., "+1234567890")
2. **Post Validation**: Posts need to be validated to extract chat_id and message_id from Telegram links
3. **Task Dependencies**: Tasks require existing accounts and posts
4. **Async Operations**: Task execution is asynchronous; use status endpoints to monitor progress
5. **Database Storage**: Uses MongoDB with Motor (async driver) for all persistence
6. **Chat ID Normalization**: Chat IDs are automatically normalized to handle both -100 prefixed and non-prefixed forms
7. **Account Status Tracking**: Accounts track detailed error information including last_error, last_error_type, and flood_wait_until
8. **Channel Sync**: Use `/accounts/{phone_number}/channels/sync` to fetch subscribed channels from Telegram
