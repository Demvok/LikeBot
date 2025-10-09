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
  "account_id": "string (optional)",
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
  "account_id": "123456789",
  "session_name": "new_session_name"
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
