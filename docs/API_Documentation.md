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
   - [Log Streaming](#log-streaming)
   - [Accounts CRUD](#accounts-crud)
   - [Login Process](#login-process)
   - [Posts CRUD](#posts-crud)
   - [Tasks CRUD](#tasks-crud)
   - [Task Actions](#task-actions)
   - [Bulk Operations](#bulk-operations)
   - [Utility Endpoints](#utility-endpoints)
6. [Error Responses](#error-responses)
7. [Notes](#notes)

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
  "status": "NEW|ACTIVE|LOGGED_IN|BANNED|ERROR (optional)",
  "created_at": "string (ISO timestamp, optional)",
  "updated_at": "string (ISO timestamp, optional)"
}
```

**Note**: For security reasons, password information is not included in account responses. Use the secure password endpoint `/accounts/{phone_number}/password` to retrieve password information when needed.

### Post
```json
{
  "post_id": "integer (optional, auto-generated)",
  "message_link": "string",
  "chat_id": "integer (optional)",
  "message_id": "integer (optional)",
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
    "type": "string (react|comment)",
    "palette": "string (positive|negative) - for react actions",
    "content": "string - for comment actions"
  },
  "status": "string (PENDING|RUNNING|PAUSED|FINISHED|CRASHED)",
  "created_at": "string (ISO timestamp)",
  "updated_at": "string (ISO timestamp)"
}
```

## API Endpoints

### Health Check

#### GET /
Get server status.

**Authentication**: Not required

**Response:**
```json
{
  "message": "LikeBot API Server is running",
  "version": "1.0.2"
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

### React Action
```json
{
  "type": "react",
  "palette": "positive"  // or "negative"
}
```

Available emoji palettes:
- **positive**: 👍, ❤️, 🔥
- **negative**: 👎, 😡, 🤬, 🤮, 💩, 🤡

### Comment Action
```json
{
  "type": "comment",
  "content": "Your comment text here"
}
```

---

## Notes

1. **Phone Numbers**: Should include country code (e.g., "+1234567890")
2. **Post Validation**: Posts need to be validated to extract chat_id and message_id from Telegram links
3. **Task Dependencies**: Tasks require existing accounts and posts
4. **Async Operations**: Task execution is asynchronous; use status endpoints to monitor progress
5. **Database Storage**: Supports both file-based storage (JSON/CSV) and MongoDB based on configuration
