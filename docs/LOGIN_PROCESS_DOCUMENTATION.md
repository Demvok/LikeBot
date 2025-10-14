# Login Process Documentation

## Overview

This document describes the new asynchronous login process for LikeBot that allows the Angular frontend to interact with Telegram account authentication through a multi-step API flow.

## Architecture

### Key Components

1. **LoginStatus Enum** (`schemas.py`):
   - `WAIT_CODE`: Waiting for verification code from user
   - `WAIT_2FA`: Waiting for 2FA password from user
   - `PROCESSING`: Processing authentication
   - `DONE`: Login completed successfully
   - `FAILED`: Login failed with error

2. **LoginProcess Schema** (`schemas.py`):
   - Tracks the state of each login session
   - Contains `asyncio.Future` objects for awaiting user input
   - Stores TelegramClient instance during login
   - Includes expiration timestamp (10 minutes default)

3. **Global Storage** (`agent.py`):
   - `pending_logins: dict[str, LoginProcess]` - In-memory storage for active login processes
   - Cleaned up automatically when expired

4. **start_login() Coroutine** (`agent.py`):
   - Main async function that handles the entire login flow
   - Runs in background as an asyncio task
   - Waits for user input via Futures
   - Handles both verification code and 2FA password
   - Saves encrypted session to database on success

## API Endpoints

### 1. POST /accounts/create/start

**Purpose**: Initialize login process and send verification code to Telegram

**Request**:
```http
POST /accounts/create/start?phone_number=+1234567890&password_encrypted=optional&session_name=my_session&notes=My%20Account
```

**Parameters**:
- `phone_number` (required): Phone number with country code
- `password_encrypted` (optional): Encrypted password for 2FA
- `session_name` (optional): Custom session name (defaults to "session_{phone_number}")
- `notes` (optional): Account notes

**Response**:
```json
{
  "status": "wait_code",
  "login_session_id": "uuid-string",
  "message": "Verification code sent to +1234567890"
}
```

**Flow**:
1. Generates unique `login_session_id`
2. Starts `start_login()` coroutine as background task
3. Waits 1 second for initialization
4. Returns session ID and status to frontend

### 2. POST /accounts/create/verify

**Purpose**: Submit verification code or 2FA password

**Request**:
```http
POST /accounts/create/verify?login_session_id=uuid&code=12345
# OR (if 2FA is required)
POST /accounts/create/verify?login_session_id=uuid&password_2fa=my2fapassword
```

**Parameters**:
- `login_session_id` (required): Login session ID from /accounts/create/start
- `code` (optional): Verification code from Telegram
- `password_2fa` (optional): 2FA password if required

**Note**: It's recommended to provide encrypted password at `/accounts/create/start` for better security.

**Response**:
```json
{
  "status": "processing",
  "message": "Verification code submitted, processing login..."
}
```

**Flow**:
1. Retrieves LoginProcess from `pending_logins`
2. Checks current status (WAIT_CODE or WAIT_2FA)
3. Sets result on appropriate Future
4. Background coroutine continues execution
5. Returns processing status

### 3. GET /accounts/create/status

**Purpose**: Poll for login process completion

**Request**:
```http
GET /accounts/create/status?login_session_id=uuid
```

**Response** (Success):
```json
{
  "status": "done",
  "phone_number": "+1234567890",
  "created_at": "2025-01-01T00:00:00Z",
  "message": "Login completed successfully",
  "account_created": true
}
```

**Response** (Failure):
```json
{
  "status": "failed",
  "phone_number": "+1234567890",
  "created_at": "2025-01-01T00:00:00Z",
  "message": "Login failed",
  "error": "Invalid verification code"
}
```

**Flow**:
1. Cleans up expired login sessions
2. Returns current status and metadata
3. Includes error message if failed

## Frontend Flow (Angular)

```typescript
// Step 1: Start login (optionally include session_name and notes)
const startResponse = await fetch('/accounts/create/start?phone_number=+123456789&session_name=MySession&notes=MyAccount');
const { login_session_id, status } = await startResponse.json();

// Step 2: Show modal for verification code
const code = await showCodeInputModal();

// Step 3: Submit code
await fetch(`/accounts/create/verify?login_session_id=${login_session_id}&code=${code}`);

// Step 4: Poll for status
const pollStatus = async () => {
  const statusResponse = await fetch(`/accounts/create/status?login_session_id=${login_session_id}`);
  const { status, error } = await statusResponse.json();
  
  if (status === 'wait_2fa') {
    // Show 2FA password input
    const password = await show2FAInputModal();
    await fetch(`/accounts/create/verify?login_session_id=${login_session_id}&password_2fa=${password}`);
    setTimeout(pollStatus, 1000); // Continue polling
  } else if (status === 'done') {
    showSuccess('Login completed!');
  } else if (status === 'failed') {
    showError(error);
  } else {
    setTimeout(pollStatus, 1000); // Keep polling
  }
};

pollStatus();
```

## Backend Flow

```
1. POST /accounts/create/start
   ↓
2. Create LoginProcess with code_future
   ↓
3. Start background task: start_login()
   ↓
4. start_login() creates TelegramClient
   ↓
5. start_login() sends verification code
   ↓
6. start_login() awaits code_future
   ║
   ╠═══ [WAITING FOR USER INPUT]
   ║
7. User submits code via POST /accounts/create/verify
   ↓
8. code_future.set_result(code)
   ↓
9. start_login() continues, calls sign_in()
   ↓
10. If 2FA required:
    - Set status = WAIT_2FA
    - await password_future
    ╠═══ [WAITING FOR PASSWORD]
    - password_future.set_result(password)
    - sign_in(password=password)
   ↓
11. Save encrypted session to database
   ↓
12. Set status = DONE
```

## Legacy Support

The original `Client.connect()` method and `_get_session()` flow remain unchanged:
- Uses `input()` for interactive console-based login
- No API or LoginProcess creation
- Useful for local testing and debugging
- Called by legacy `POST /accounts/create` endpoint

## Session Cleanup

- Login sessions expire after 10 minutes
- `cleanup_expired_logins()` removes expired sessions
- Called automatically during status polling
- Can be scheduled as periodic background task

## Error Handling

### Common Errors

1. **Invalid verification code**:
   - Status: `FAILED`
   - Error message stored in `LoginProcess.error_message`

2. **2FA password incorrect**:
   - Status: `FAILED`
   - Error message stored in `LoginProcess.error_message`

3. **Session not found**:
   - HTTP 404 returned by `/accounts/create/verify` or `/accounts/create/status`
   - May indicate expired session

4. **Network errors**:
   - Caught in `start_login()` coroutine
   - Status set to `FAILED`
   - Client disconnected if connected

## Security Considerations

1. **Password Encryption**:
   - 2FA passwords encrypted before storage
   - Use `encrypt_secret()` and `decrypt_secret()` from `encryption.py`

2. **Session Storage**:
   - Sessions stored encrypted in database
   - In-memory `pending_logins` cleared after completion/expiration

3. **HTTPS Required**:
   - All API communication should use HTTPS in production
   - Prevents password/code interception

4. **Rate Limiting**:
   - Consider implementing rate limiting on login endpoints
   - Prevent brute force attacks on verification codes

## Testing

### Test Without Frontend

Use the legacy endpoint:
```python
# Start server
uvicorn main:app --reload

# In Python console or script
import requests

# Create account (will prompt for code in server console)
response = requests.post(
    "http://localhost:8000/accounts/create",
    json={
        "phone_number": "+1234567890",
        "session_name": "test_session"
    }
)
```

### Test With Frontend Simulation

```bash
# Terminal 1: Start server
uvicorn main:app --reload

# Terminal 2: Test API flow
curl -X POST "http://localhost:8000/accounts/create/start?phone_number=+1234567890"
# Returns: {"status": "wait_code", "login_session_id": "uuid-xxx"}

curl -X POST "http://localhost:8000/accounts/create/verify?login_session_id=uuid-xxx&code=12345"
# Returns: {"status": "processing"}

curl -X GET "http://localhost:8000/accounts/create/status?login_session_id=uuid-xxx"
# Returns: {"status": "done"} or {"status": "wait_2fa"}
```

## Future Improvements

1. **WebSocket Support**:
   - Push status updates instead of polling
   - More efficient for frontend

2. **Persistent Storage**:
   - Store LoginProcess in Redis instead of memory
   - Survive server restarts
   - Enable horizontal scaling

3. **Rate Limiting**:
   - Implement per-phone-number rate limits
   - Prevent abuse

4. **Session Management**:
   - Background task to periodically clean expired sessions
   - Metrics on active login processes

5. **Email/SMS Notifications**:
   - Alert users of login attempts
   - Security monitoring
