# LikeBot API Documentation

This document describes the full CRUD API for the LikeBot automation system.

## Base URL
```
http://localhost:8000
```

## Authentication
Currently, no authentication is required for API endpoints.

## Data Models

### Account
```json
{
  "phone_number": "+1234567890",
  "account_id": 123456789,
  "session_name": "string (optional)",
  "session_encrypted": "string (optional)",
  "twofa": false,
  "password_encrypted": "string (optional)",
  "notes": "string (optional)",
  "status": "NEW|ACTIVE|LOGGED_IN|BANNED|ERROR (optional)",
  "created_at": "string (ISO timestamp, optional)",
  "updated_at": "string (ISO timestamp, optional)"
}
```

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
    "palette": "string (palette name from database) - for react actions",
    "content": "string - for comment actions"
  },
  "status": "string (PENDING|RUNNING|PAUSED|FINISHED|CRASHED)",
  "created_at": "string (ISO timestamp)",
  "updated_at": "string (ISO timestamp)"
}
```

### Reaction Palette
```json
{
  "palette_name": "string (unique identifier)",
  "emojis": ["array of emoji strings"],
  "ordered": "boolean (false=random, true=sequential)",
  "description": "string (optional)",
  "created_at": "string (ISO timestamp)",
  "updated_at": "string (ISO timestamp)"
}
```

**Palette Behavior:**
- `ordered: false` - Random emoji selection for each reaction
- `ordered: true` - Sequential emoji selection (cycles through list)

## API Endpoints

### Health Check

#### GET /
Get server status.

**Response:**
```json
{
  "message": "LikeBot API Server is running",
  "version": "1.0.0"
}
```

---

## Accounts CRUD

### GET /accounts
Get all accounts with optional filtering.

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

**Response:**
```json
{
  "phone_number": "+1234567890",
  "account_id": "123456789",
  "session_name": "user_session"
}
```

### POST /accounts
Create a new account.

**Request Body:**
```json
{
  "phone_number": "+1234567890",
  "account_id": "123456789",
  "session_name": "user_session"
}
```

**Response:**
```json
{
  "message": "Account +1234567890 created successfully"
}
```

### PUT /accounts/{phone_number}
Update an existing account.

**Request Body:**
```json
{
  "account_id": 123456789,
  "session_name": "new_session_name",
  "session_encrypted": "encrypted_session_string",
  "twofa": true,
  "password_encrypted": "encrypted_password",
  "notes": "Updated notes",
  "status": "ACTIVE"
}
```

**Response:**
```json
{
  "message": "Account +1234567890 updated successfully"
}
```

### DELETE /accounts/{phone_number}
Delete an account.

**Response:**
```json
{
  "message": "Account +1234567890 deleted successfully"
}
```

### PUT /accounts/{phone_number}/validate
Validate an account by testing its connection to Telegram.

**Prerequisites:**
- Account must have been logged in via `/accounts/create/start` and have a valid session
- Account must have `session_encrypted` field populated

**Response:**
```json
{
  "message": "Account +1234567890 validated successfully",
  "account_id": 123456789,
  "account_status": "LOGGED_IN",
  "has_session": true
}
```

**Error Responses:**
- `404`: Account not found
- `400`: Account has no session (not logged in)
- `500`: Connection failed or other validation errors

---

## Login Process

### POST /accounts/create/start
Start the login process for a Telegram account. Sends verification code to the phone number.

**Query Parameters:**
- `phone_number` (required): Phone number with country code (e.g., +1234567890)
- `password_encrypted` (optional): Encrypted password for 2FA
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

### POST /accounts/create/verify
Submit verification code or 2FA password to continue the login process.

**Query Parameters:**
- `login_session_id` (required): Login session ID from /accounts/create/start
- `code` (optional): Verification code from Telegram
- `password_2fa` (optional): 2FA password if required (will be encrypted)

**Note**: It's recommended to provide encrypted password at `/accounts/create/start` for better security.

**Response:**
```json
{
  "status": "processing",
  "message": "Verification code submitted, processing login..."
}
```

**Error Responses:**
- `404`: Login session not found or expired
- `400`: Missing required parameter (code or password)

### GET /accounts/create/status
Check the status of an ongoing login process. Used for polling by the frontend.

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
- `404`: Login session not found or expired

---

## Posts CRUD

### GET /posts
Get all posts with optional filtering.

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

**Response:**
```json
{
  "task_id": 1,
  "status": "RUNNING"
}
```

### POST /tasks/{task_id}/start
Start task execution.

**Response:**
```json
{
  "message": "Task 1 started successfully"
}
```

### POST /tasks/{task_id}/pause
Pause task execution.

**Response:**
```json
{
  "message": "Task 1 paused successfully"
}
```

### POST /tasks/{task_id}/resume
Resume task execution.

**Response:**
```json
{
  "message": "Task 1 resumed successfully"
}
```

### GET /tasks/{task_id}/report
Get execution report for a task.

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
Get all execution runs for a specific task.

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

**Request Body:**
```json
[
  {
    "phone_number": "+1234567890",
    "account_id": "123456789"
  },
  {
    "phone_number": "+0987654321",
    "account_id": "987654321"
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

**Request Body:**
```json
["+1234567890", "+0987654321"]
```

### DELETE /posts/bulk
Delete multiple posts in bulk.

**Request Body:**
```json
[1, 2, 3]
```

---

## Reaction Palettes CRUD

Reaction palettes define sets of emojis used in reaction tasks. Each palette can be configured to use emojis randomly or sequentially.

### GET /palettes
Get all reaction palettes with optional filtering.

**Query Parameters:**
- `palette_name` (optional): Filter by palette name

**Response:**
```json
[
  {
    "palette_name": "positive",
    "emojis": ["👍", "❤️", "🔥"],
    "ordered": false,
    "description": "Positive reactions palette",
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z"
  },
  {
    "palette_name": "negative",
    "emojis": ["👎", "😡", "🤬", "🤮", "💩", "🤡"],
    "ordered": false,
    "description": "Negative reactions palette",
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z"
  }
]
```

### GET /palettes/{palette_name}
Get a specific reaction palette by name.

**Response:**
```json
{
  "palette_name": "positive",
  "emojis": ["👍", "❤️", "🔥"],
  "ordered": false,
  "description": "Positive reactions palette",
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z"
}
```

**Error Responses:**
- `404`: Palette not found

### POST /palettes
Create a new reaction palette.

**Query Parameters:**
- `palette_name` (required): Unique palette name (alphanumeric, lowercase, underscores/hyphens)
- `emojis` (required): Comma-separated list of emojis (e.g., "👍,❤️,🔥")
- `ordered` (optional): If true, emojis are used sequentially; if false (default), chosen randomly
- `description` (optional): Palette description

**Example Request:**
```
POST /palettes?palette_name=custom&emojis=🎉,🎊,🎈&ordered=false&description=Celebration emojis
```

**Response:**
```json
{
  "message": "Palette 'custom' created successfully",
  "palette_name": "custom",
  "emoji_count": 3
}
```

**Error Responses:**
- `400`: Invalid emoji list (empty or invalid format)
- `409`: Palette already exists

**Palette Ordering:**
- **ordered=false** (default): Emojis are chosen randomly for each reaction
- **ordered=true**: Emojis are used sequentially in the order defined (cycling through)

### PUT /palettes/{palette_name}
Update an existing reaction palette.

**Query Parameters:**
- `emojis` (optional): Comma-separated list of emojis
- `ordered` (optional): Update ordering behavior
- `description` (optional): Update description

**Note:** Palette name cannot be changed. Only provided fields will be updated.

**Example Request:**
```
PUT /palettes/custom?emojis=🎉,🎊,🎈,🎁&description=Updated celebration emojis
```

**Response:**
```json
{
  "message": "Palette 'custom' updated successfully"
}
```

**Error Responses:**
- `400`: No fields to update or invalid emoji list
- `404`: Palette not found

### DELETE /palettes/{palette_name}
Delete a reaction palette.

**Warning:** Tasks using this palette will fail to execute until palette is recreated or task is updated.

**Response:**
```json
{
  "message": "Palette 'custom' deleted successfully"
}
```

**Error Responses:**
- `404`: Palette not found

---

## Utility Endpoints

### GET /stats
Get database statistics.

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

**Response:**
```json
{
  "message": "Post 1 validated successfully",
  "chat_id": -1001234567890,
  "message_id": 123
}
```

---

## Legacy Endpoints

### POST /actions/run_task
Legacy endpoint to run a task (for backward compatibility).

**Request Body:**
```json
{
  "task_id": 1
}
```

**Response:**
```json
{
  "status": "Task 1 completed successfully"
}
```

---

## Error Responses

All endpoints return appropriate HTTP status codes:

- `200`: Success
- `201`: Created
- `400`: Bad Request (validation errors)
- `404`: Not Found
- `409`: Conflict (resource already exists)
- `500`: Internal Server Error

Error response format:
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
  "palette": "positive"  // Palette name - must exist in database
}
```

Palettes are stored in MongoDB and can be managed via the `/palettes` endpoints. Common palettes:
- **positive**: Positive reaction emojis (👍, ❤️, 🔥, etc.)
- **negative**: Negative reaction emojis (👎, 😡, 🤬, etc.)

To view available palettes: `GET /palettes`
To create custom palettes: `POST /palettes`

### Comment Action
```json
{
  "type": "comment",
  "content": "Your comment text here"
}
```

---

## Palette Management

Reaction palettes are now stored exclusively in MongoDB. To set up palettes:

1. **View existing palettes**: `GET /palettes`
2. **Create default palettes**: Run `python migrate_palettes.py --auto`
3. **Create custom palette**: `POST /palettes` with your emoji set
4. **Update palette**: `PUT /palettes/{palette_name}`
5. **Delete palette**: `DELETE /palettes/{palette_name}`

**Palette Configuration:**
- **Random selection** (`ordered=false`): System picks a random emoji from the palette for each reaction
- **Sequential selection** (`ordered=true`): System cycles through emojis in order

**Note:** Tasks will fail if they reference a palette that doesn't exist in the database. Ensure palettes are created before running tasks.

---

## Notes

1. **Phone Numbers**: Should include country code (e.g., "+1234567890")
2. **Post Validation**: Posts need to be validated to extract chat_id and message_id from Telegram links
3. **Task Dependencies**: Tasks require existing accounts and posts
4. **Async Operations**: Task execution is asynchronous; use status endpoints to monitor progress
5. **Database Storage**: Supports both file-based storage (JSON/CSV) and MongoDB based on configuration
6. **Reaction Palettes**: Palettes are stored exclusively in MongoDB. Use `python migrate_palettes.py --auto` to create default palettes, or use the `/palettes` API endpoints to manage them
7. **Palette Requirement**: Tasks with `type: "react"` require the specified palette to exist in the database, or the task will fail with a clear error message
