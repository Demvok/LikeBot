"""
LikeBot FastAPI application.

Defines the HTTP and WebSocket API for authentication, account/post/task CRUD,
task actions, reporting, bulk operations and log streaming. Delegates data,
auth and background work to modules: auth, database, agent, reporter,
encryption and logger. Expects env vars (KEK, JWT_SECRET_KEY, db_url) and is
typically run with uvicorn.
"""

import asyncio, atexit, os, logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from collections import deque
from datetime import timedelta, datetime as dt, timezone
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from typing import Optional, List, Dict, Annotated
from main_logic.agent import *
from main_logic.post import *
from main_logic.task import *
from utils.logger import crash_handler, cleanup_logging, get_log_directory
from main_logic.database import get_db
from fastapi.middleware.cors import CORSMiddleware
from main_logic.schemas import *
from auxilary_logic.auth import (
    authenticate_user, get_current_user, get_current_verified_user,
    get_current_admin_user, create_user_account, create_user_token, decode_access_token
)
from jose import JWTError
from jose.exceptions import ExpiredSignatureError

load_dotenv()
frontend_http = os.getenv("frontend_http", None)

atexit.register(cleanup_logging)  # Register cleanup function


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # Startup
    await validate_environment()
    yield
    # Shutdown (if needed in the future)
    # Add cleanup code here


app = FastAPI(
    title="LikeBot API",
    description="Full API for LikeBot automation",
    version="1.1.1",
    lifespan=lifespan
)

logger = logging.getLogger("likebot.main")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", frontend_http],  # Or ["*"] for all origins (development only)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

convert_to_serializable = serialize_for_json  # Use centralized serialization function from schemas


def _resolve_log_path(log_file: str) -> Optional[str]:
    """Resolve a log file name against the configured log directory."""
    log_dir = os.path.abspath(get_log_directory())
    candidate = os.path.abspath(os.path.join(log_dir, os.path.basename(log_file)))
    if not candidate.startswith(log_dir):
        return None
    return candidate


async def validate_environment() -> None:
    """Ensure critical environment configuration and database connectivity are available."""
    required_vars = ("KEK", "JWT_SECRET_KEY", "db_url")
    missing = [env for env in required_vars if not os.getenv(env)]

    if missing:
        message = ", ".join(missing)
        logger.critical("Missing required environment variables: %s", message)
        raise RuntimeError(f"Missing required environment variables: {message}")

    db = get_db()
    try:
        await db._ensure_ready()
    except RuntimeError as exc:
        logger.critical("Database initialization failed: %s", exc)
        raise
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Unexpected error during database readiness check")
        raise


@app.get("/", summary="Health check")
async def root():
    return {"message": "LikeBot API Server is running", "version": app.version}


# ============= AUTHENTICATION ENDPOINTS =============

@app.post("/auth/register", summary="Register new user", status_code=201, response_model=UserResponse, tags=["Authentication"])
@crash_handler
async def register_user(user_data: UserCreate):
    """
    Register a new user account.
    
    - **username**: Unique username (3-50 characters, alphanumeric with underscores/hyphens)
    - **password**: Password (minimum 6 characters)
    - **role**: User role (default: user)
    
    New users start as unverified and require admin approval.
    """
    user_dict = await create_user_account(user_data)
    
    # Remove password hash from response
    response_dict = {k: v for k, v in user_dict.items() if k != 'password_hash'}
    return response_dict


@app.post("/auth/login", summary="Login and get access token", response_model=Token, tags=["Authentication"])
@crash_handler
async def login(form_data: Annotated[OAuth2PasswordRequestForm, Depends()]):
    """
    Login with username and password to get a JWT access token.
    
    The token should be included in subsequent requests as:
    `Authorization: Bearer <token>`
    
    Returns different error messages based on the failure reason:
    - User not found
    - Incorrect password
    - User not verified (needs admin approval)
    """
    # Reject passwords that exceed bcrypt's 72-byte limit to avoid silent truncation
    if len(form_data.password.encode('utf-8')) > 72:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password exceeds bcrypt's 72-byte limit. Please use a shorter password."
        )

    # Authenticate user
    user = await authenticate_user(form_data.username, form_data.password)
    
    if not user:
        # Check if user exists
        db = get_db()
        existing_user = await db.get_user(form_data.username)
        
        if not existing_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found. Please register first.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect password",
                headers={"WWW-Authenticate": "Bearer"},
            )
    
    # Check if user is verified
    if not user.get("is_verified", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is not verified. Please contact an administrator.",
        )
    
    # Create access token
    access_token = create_user_token(user)
    
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/auth/me", summary="Get current user info", response_model=UserResponse, tags=["Authentication"])
@crash_handler
async def get_me(current_user: Annotated[dict, Depends(get_current_user)]):
    """
    Get information about the currently authenticated user.
    
    Requires valid JWT token in Authorization header.
    """
    # Remove password hash from response
    response_dict = {k: v for k, v in current_user.items() if k != 'password_hash'}
    return response_dict


# ============= USER MANAGEMENT ENDPOINTS (Admin Only) =============

@app.get("/users", summary="Get all users", response_model=List[Dict], tags=["User Management"])
@crash_handler
async def get_all_users(current_user: dict = Depends(get_current_admin_user)):
    """
    Get all users in the system. Admin only.
    
    Returns list of users with their roles, verification status, and creation timestamps.
    Password hashes are excluded for security.
    """
    db = get_db()
    users = await db.get_all_users()
    
    # Remove password hashes for security
    secure_users = []
    for user in users:
        user_copy = user.copy()
        user_copy.pop('password_hash', None)
        secure_users.append(user_copy)
    
    return secure_users


@app.put("/users/{username}/role", summary="Update user role", tags=["User Management"])
@crash_handler
async def update_user_role(
    username: str,
    role: UserRole = Query(..., description="New role to assign"),
    current_user: dict = Depends(get_current_admin_user)
):
    """
    Update a user's role. Admin only.
    
    Allowed roles: admin, user, guest
    """
    db = get_db()
    
    # Check if user exists
    user = await db.get_user(username)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {username} not found")
    
    # Prevent demoting yourself
    if username.lower() == current_user.get('username', '').lower():
        raise HTTPException(
            status_code=400,
            detail="Cannot change your own role. Ask another admin."
        )
    
    # Update role
    success = await db.update_user(username, {"role": role.value})
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update user role")
    
    return {"message": f"User {username} role updated to {role.value}", "username": username, "new_role": role.value}


@app.put("/users/{username}/verify", summary="Update user verification status", tags=["User Management"])
@crash_handler
async def update_user_verification(
    username: str,
    is_verified: bool = Query(..., description="Verification status"),
    current_user: dict = Depends(get_current_admin_user)
):
    """
    Update a user's verification status. Admin only.
    
    Verified users can access the API, unverified users are blocked after login.
    """
    db = get_db()
    
    # Check if user exists
    user = await db.get_user(username)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {username} not found")
    
    # Update verification status
    success = await db.update_user(username, {"is_verified": is_verified})
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update verification status")
    
    return {
        "message": f"User {username} verification status updated",
        "username": username,
        "is_verified": is_verified
    }


@app.delete("/users/{username}", summary="Delete user", tags=["User Management"])
@crash_handler
async def delete_user(
    username: str,
    current_user: dict = Depends(get_current_admin_user)
):
    """
    Delete a user from the system. Admin only.
    
    Cannot delete yourself or the last admin user.
    """
    db = get_db()
    
    # Check if user exists
    user = await db.get_user(username)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {username} not found")
    
    # Prevent deleting yourself
    if username.lower() == current_user.get('username', '').lower():
        raise HTTPException(
            status_code=400,
            detail="Cannot delete your own account. Ask another admin."
        )
    
    # Prevent deleting last admin
    if user.get('role') == 'admin' and user.get('is_verified'):
        admin_count = await db.count_admin_users()
        if admin_count <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete the last verified admin user"
            )
    
    # Delete user
    success = await db.delete_user(username)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete user")
    
    return {"message": f"User {username} deleted successfully", "username": username}


# ============= LOG STREAMING =============

@app.websocket('/ws/logs')
async def stream_logs(websocket: WebSocket):
    """Stream log file updates over a websocket connection. Requires authentication via query param."""
    log_file = websocket.query_params.get('log_file', 'main.log')
    tail_param = websocket.query_params.get('tail', '200')
    token = websocket.query_params.get('token', None)

    # Basic token validation for websocket
    if not token:
        await websocket.close(code=4401, reason="Authentication required: supply token query parameter.")
        return

    try:
        payload = decode_access_token(token)
    except ExpiredSignatureError:
        await websocket.close(code=4403, reason="Token expired. Please refresh your session.")
        return
    except JWTError:
        await websocket.close(code=4401, reason="Invalid token")
        return

    username = payload.get("sub")
    if not username:
        await websocket.close(code=4401, reason="Invalid token payload")
        return

    db = get_db()
    user = await db.get_user(username)
    if not user:
        await websocket.close(code=4401, reason="User no longer exists.")
        return

    if not user.get("is_verified", False):
        await websocket.close(code=4403, reason="User is not verified.")
        return

    try:
        tail = int(tail_param)
    except ValueError:
        tail = 200

    tail = max(0, min(tail, 1000))
    log_path = _resolve_log_path(log_file)

    await websocket.accept()

    exp_ts = payload.get("exp")
    if exp_ts:
        expires_at = dt.fromtimestamp(exp_ts, tz=timezone.utc)
        remaining = expires_at - dt.now(tz=timezone.utc)
        if remaining <= timedelta(minutes=5):
            await websocket.send_json({
                "type": "warning",
                "message": "Access token expires soon. Please refresh to avoid disconnection.",
                "expires_at": expires_at.isoformat()
            })

    if not log_path or not os.path.exists(log_path):
        await websocket.send_json({
            "type": "error",
            "message": f"Log file {os.path.basename(log_file)} not found"
        })
        await websocket.close(code=1003)
        return

    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as stream:
            if tail > 0:
                history = deque()
                for raw_line in stream:
                    history.append(raw_line.rstrip('\n'))
                    if len(history) > tail:
                        history.popleft()
                for entry in history:
                    await websocket.send_text(entry)
            else:
                stream.seek(0, os.SEEK_END)

            while True:
                line = stream.readline()
                if line:
                    await websocket.send_text(line.rstrip('\n'))
                else:
                    await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return
    except ExpiredSignatureError:
        await websocket.close(code=4403, reason="Token expired. Please refresh your session.")
    except Exception as exc:
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Log streaming interrupted: {exc}"
            })
        finally:
            await websocket.close(code=1011)

# ============= ACCOUNTS CRUD =============

@app.get('/accounts', summary="Get all accounts", response_model=List[Dict], tags=["Accounts"])
@crash_handler
async def get_accounts(
    phone_number: Optional[str] = Query(None, description="Filter by phone number"),
    current_user: dict = Depends(get_current_user)
):
    """Get all accounts with optional filtering by phone number. Requires authentication."""
    try:
        db = get_db()
        accounts = await db.load_all_accounts()
        
        # Convert to secure dict format for JSON response (excludes passwords)
        accounts_data = [account.to_dict(secure=True) for account in accounts]
        
        # Apply filtering if phone_number is provided
        if phone_number:
            accounts_data = [acc for acc in accounts_data if acc.get('phone_number') == phone_number]
        
        return accounts_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load accounts: {str(e)}")

@app.get('/accounts/{phone_number}', summary="Get account by phone number", tags=["Accounts"])
@crash_handler
async def get_account(
    phone_number: str,
    current_user: dict = Depends(get_current_user)
):
    """Get a specific account by phone number. Requires authentication."""
    try:
        db = get_db()
        account = await db.get_account(phone_number)
        if not account:
            raise HTTPException(status_code=404, detail=f"Account with phone number {phone_number} not found")
        return account.to_dict(secure=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get account: {str(e)}")

@app.post('/accounts', summary="Create new account in database without login", status_code=201, tags=["Accounts"])
@crash_handler
async def create_account_without_login(
    account_data: AccountCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create a new account without logging in. Useful for pre-registering accounts. Legacy endpoint. Requires authentication."""
    try:
        from auxilary_logic.encryption import encrypt_secret, PURPOSE_PASSWORD
        
        db = get_db()
        existing_account = await db.get_account(account_data.phone_number)  # Check if account already exists
        if existing_account:
            raise HTTPException(status_code=409, detail=f"Account with phone number {account_data.phone_number} already exists")
        
        # Create account dictionary and encrypt password if provided
        account_dict = account_data.model_dump()
        
        # Handle password encryption
        if account_dict.get('password'):
            account_dict['password_encrypted'] = encrypt_secret(account_dict['password'], PURPOSE_PASSWORD)
            # Remove plain text password from dict
            del account_dict['password']
        else:
            account_dict['password_encrypted'] = None
        
        success = await db.add_account(account_dict)
        
        if success:
            return {"message": f"Account {account_data.phone_number} created successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to create account")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create account: {str(e)}")


@app.put('/accounts/{phone_number}', summary="Update account", tags=["Accounts"])
@crash_handler
async def update_account(
    phone_number: str, 
    account_data: AccountUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update an existing account. Requires authentication."""
    try:
        from auxilary_logic.encryption import encrypt_secret, PURPOSE_PASSWORD
        
        db = get_db()
        
        # Check if account exists
        existing_account = await db.get_account(phone_number)
        if not existing_account:
            raise HTTPException(status_code=404, detail=f"Account with phone number {phone_number} not found")
        
        # Update account with only provided fields
        update_dict = {k: v for k, v in account_data.model_dump().items() if v is not None}
        
        if not update_dict:
            raise HTTPException(status_code=400, detail="No update data provided")
        
        # Handle password encryption if password is being updated
        if 'password' in update_dict:
            if update_dict['password']:
                update_dict['password_encrypted'] = encrypt_secret(update_dict['password'], PURPOSE_PASSWORD)
                # Automatically set twofa to True if password is provided
                update_dict['twofa'] = True
            else:
                # If password is empty/None, clear password and disable 2FA
                update_dict['password_encrypted'] = None
                update_dict['twofa'] = False
            # Remove plain text password from dict
            del update_dict['password']
        
        success = await db.update_account(phone_number, update_dict)
        
        if success:
            return {"message": f"Account {phone_number} updated successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to update account")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update account: {str(e)}")

@app.delete('/accounts/{phone_number}', summary="Delete account", tags=["Accounts"])
@crash_handler
async def delete_account(
    phone_number: str,
    current_user: dict = Depends(get_current_user)
):
    """Delete an account by phone number. Requires authentication."""
    try:
        db = get_db()
        
        # Check if account exists
        existing_account = await db.get_account(phone_number)
        if not existing_account:
            raise HTTPException(status_code=404, detail=f"Account with phone number {phone_number} not found")
        
        success = await db.delete_account(phone_number)
        
        if success:
            return {"message": f"Account {phone_number} deleted successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to delete account")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete account: {str(e)}")

@app.put('/accounts/{phone_number}/validate', summary="Validate account connection to Telegram", tags=["Accounts"])
@crash_handler
async def validate_account(
    phone_number: str,
    current_user: dict = Depends(get_current_user)
):
    """Validate an account by testing its connection to Telegram. Requires authentication."""
    try:
        db = get_db()
        
        # Check if account exists
        existing_account = await db.get_account(phone_number)
        if not existing_account:
            raise HTTPException(status_code=404, detail=f"Account with phone number {phone_number} not found")
        
        # Check if account has a session
        if not existing_account.session_encrypted:
            raise HTTPException(
                status_code=400, 
                detail=f"Account {phone_number} has no session. Please login first using /accounts/create/start"
            )
        
        # Create connection and test it
        client = await existing_account.create_connection()
        
        try:
            # Test connection by trying to get user info
            if client.is_connected:
                return {
                    "message": f"Account {phone_number} validated successfully",
                    "account_id": existing_account.account_id or client.account.account_id,
                    "account_status": existing_account.status,
                    "has_session": True
                }
            else:
                raise HTTPException(status_code=500, detail="Failed to establish connection")
        finally:
            await client.disconnect()
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to validate account: {str(e)}")

@app.get('/accounts/{phone_number}/password', summary="Get account password (secure endpoint)", response_model=AccountPasswordResponse, tags=["Accounts"])
@crash_handler
async def get_account_password(
    phone_number: str,
    current_user: dict = Depends(get_current_admin_user)
):
    """
    Get account password securely. Requires admin privileges.
    In production, this should require additional authentication/authorization.
    """
    try:
        from auxilary_logic.encryption import decrypt_secret, PURPOSE_PASSWORD
        
        db = get_db()
        account = await db.get_account(phone_number)
        if not account:
            raise HTTPException(status_code=404, detail=f"Account with phone number {phone_number} not found")
        
        # Check if account has a password
        has_password = bool(account.password_encrypted)
        decrypted_password = None
        
        if has_password:
            try:
                decrypted_password = decrypt_secret(account.password_encrypted, PURPOSE_PASSWORD)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to decrypt password: {str(e)}")
        
        return AccountPasswordResponse(
            phone_number=phone_number,
            has_password=has_password,
            password=decrypted_password
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get account password: {str(e)}")

# ============= ACCOUNT LOCK ENDPOINTS =============

@app.get('/accounts/locks', summary="Get all account locks", tags=["Account Locks"])
@crash_handler
async def get_all_account_locks(
    current_user: dict = Depends(get_current_user)
):
    """
    Get all currently locked accounts and which tasks hold them.
    Requires authentication.
    """
    lock_manager = get_account_lock_manager()
    locks = lock_manager.get_all_locks()
    
    # Format for API response
    result = []
    for phone_number, info in locks.items():
        result.append({
            "phone_number": phone_number,
            "task_id": info.get("task_id"),
            "locked_at": info.get("locked_at").isoformat() if info.get("locked_at") else None
        })
    
    return {
        "count": len(result),
        "locks": result
    }


@app.get('/accounts/{phone_number}/lock', summary="Get account lock status", tags=["Account Locks"])
@crash_handler
async def get_account_lock_status(
    phone_number: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Check if a specific account is currently locked.
    Requires authentication.
    """
    lock_manager = get_account_lock_manager()
    
    is_locked = lock_manager.is_locked(phone_number)
    lock_info = lock_manager.get_lock_info(phone_number)
    
    if is_locked and lock_info:
        return {
            "phone_number": phone_number,
            "is_locked": True,
            "task_id": lock_info.get("task_id"),
            "locked_at": lock_info.get("locked_at").isoformat() if lock_info.get("locked_at") else None
        }
    else:
        return {
            "phone_number": phone_number,
            "is_locked": False,
            "task_id": None,
            "locked_at": None
        }


@app.delete('/accounts/{phone_number}/lock', summary="Force release account lock", tags=["Account Locks"])
@crash_handler
async def force_release_account_lock(
    phone_number: str,
    current_user: dict = Depends(get_current_admin_user)
):
    """
    Force release a lock on an account. Use with caution - this may cause issues 
    if a task is actively using the account.
    Requires admin privileges.
    """
    lock_manager = get_account_lock_manager()
    
    if not lock_manager.is_locked(phone_number):
        raise HTTPException(status_code=404, detail=f"Account {phone_number} is not locked")
    
    lock_info = lock_manager.get_lock_info(phone_number)
    released = await lock_manager.release(phone_number)
    
    if released:
        return {
            "message": f"Lock on account {phone_number} released successfully",
            "previous_task_id": lock_info.get("task_id") if lock_info else None
        }
    else:
        raise HTTPException(status_code=500, detail="Failed to release lock")


@app.delete('/tasks/{task_id}/locks', summary="Release all locks for a task", tags=["Account Locks"])
@crash_handler
async def release_task_locks(
    task_id: int,
    current_user: dict = Depends(get_current_admin_user)
):
    """
    Release all account locks held by a specific task.
    Useful for cleanup after a task crashes or is forcefully stopped.
    Requires admin privileges.
    """
    lock_manager = get_account_lock_manager()
    released_count = await lock_manager.release_all_for_task(task_id)
    
    return {
        "message": f"Released {released_count} locks for task {task_id}",
        "released_count": released_count
    }


# ============= LOGIN PROCESS ENDPOINTS =============

@app.post('/accounts/create/start', summary="Start login process", status_code=200, tags=["Account Creation"])
@crash_handler
async def login_start(
    phone_number: str = Query(..., description="Phone number with country code"),
    password: Optional[str] = Query(None, description="Password for 2FA (will be encrypted)"),
    session_name: Optional[str] = Query(None, description="Custom session name (optional)"),
    notes: Optional[str] = Query(None, description="Account notes (optional)"),
    current_user: dict = Depends(get_current_user)
):
    """
    Start the login process for a Telegram account. Requires authentication.
    Returns login_session_id and status.
    Frontend should poll /accounts/create/status or proceed to /accounts/create/verify.
    """
    from auxilary_logic.login import start_login, pending_logins
    from auxilary_logic.encryption import encrypt_secret, PURPOSE_PASSWORD
    import asyncio
    import uuid
    
    try:
        # Encrypt password if provided
        password_encrypted = None
        if password:
            password_encrypted = encrypt_secret(password, PURPOSE_PASSWORD)
        
        # Generate unique session ID
        login_session_id = str(uuid.uuid4())
        
        # Start login process in background
        asyncio.create_task(start_login(
            phone_number=phone_number, 
            password=password_encrypted, 
            login_session_id=login_session_id,
            session_name=session_name,
            notes=notes
        ))
        
        # Wait a moment for the process to initialize and send code
        await asyncio.sleep(1)
        
        # Get the login process from pending_logins
        login_process = pending_logins.get(login_session_id)
        if not login_process:
            raise HTTPException(status_code=500, detail="Failed to initialize login process")
        
        return {
            "status": login_process.status.value,
            "login_session_id": login_session_id,
            "message": f"Verification code sent to {phone_number}"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start login: {str(e)}")


@app.post('/accounts/create/verify', summary="Verify login code", status_code=200, tags=["Account Creation"])
@crash_handler
async def login_verify(
    login_session_id: str = Query(..., description="Login session ID from /accounts/create/start"),
    code: str = Query(..., description="Verification code from Telegram"),
    current_user: dict = Depends(get_current_user)
):
    """
    Submit verification code to continue login process. Requires authentication.
    2FA passwords must be provided during /accounts/create/start, not here.
    """
    from auxilary_logic.login import pending_logins
    
    try:
        # Get login process
        login_process = pending_logins.get(login_session_id)
        if not login_process:
            raise HTTPException(status_code=404, detail="Login session not found or expired")
        
        # Check what we're waiting for
        if login_process.status == LoginStatus.WAIT_CODE:
            # Set the code in the future to continue login
            if not login_process.code_future.done():
                login_process.code_future.set_result(code)
            
            return {
                "status": "processing",
                "message": "Verification code submitted, processing login..."
            }
            
        elif login_process.status == LoginStatus.WAIT_2FA:
            # 2FA is required but no password was provided during start
            raise HTTPException(
                status_code=400, 
                detail="2FA password is required but was not provided during /accounts/create/start. Please restart the login process with the password parameter."
            )
            
        else:
            return {
                "status": login_process.status.value,
                "message": f"Login is in {login_process.status.value} state"
            }
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to verify login: {str(e)}")


@app.get('/accounts/create/status', summary="Check login process status", status_code=200, tags=["Account Creation"])
@crash_handler
async def login_status(
    login_session_id: str = Query(..., description="Login session ID from /accounts/create/start"),
    current_user: dict = Depends(get_current_user)
):
    """
    Check the status of an ongoing login process. Requires authentication.
    Used for polling by the frontend.
    """
    from auxilary_logic.login import pending_logins, cleanup_expired_logins
    
    try:
        # Cleanup expired sessions
        cleanup_expired_logins()
        
        # Get login process
        login_process = pending_logins.get(login_session_id)
        if not login_process:
            raise HTTPException(status_code=404, detail="Login session not found or expired")
        
        response = {
            "status": login_process.status.value,
            "phone_number": login_process.phone_number,
            "created_at": login_process.created_at.isoformat(),
        }
        
        # Add additional info based on status
        if login_process.status == LoginStatus.DONE:
            response["message"] = "Login completed successfully"
            response["account_created"] = True
            
        elif login_process.status == LoginStatus.FAILED:
            response["message"] = "Login failed"
            response["error"] = login_process.error_message
            
        elif login_process.status == LoginStatus.WAIT_CODE:
            response["message"] = "Waiting for verification code"
            
        elif login_process.status == LoginStatus.WAIT_2FA:
            response["message"] = "Waiting for 2FA password"
            
        elif login_process.status == LoginStatus.PROCESSING:
            response["message"] = "Processing login..."
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get login status: {str(e)}")


# ============= POSTS CRUD =============

@app.get('/posts', summary="Get all posts", response_model=List[Dict], tags=["Posts"])
@crash_handler
async def get_posts(
    post_id: Optional[int] = Query(None, description="Filter by post ID"),
    chat_id: Optional[int] = Query(None, description="Filter by chat ID"),
    validated_only: Optional[bool] = Query(None, description="Filter by validation status"),
    current_user: dict = Depends(get_current_user)
):
    """Get all posts with optional filtering. Requires authentication."""
    try:
        db = get_db()
        
        # Optimize: use database query for chat_id filtering
        if chat_id is not None:
            posts = await db.get_posts_by_chat_id(chat_id)
        else:
            posts = await db.load_all_posts()
        
        # Convert to dict format for JSON response
        posts_data = [post.to_dict() for post in posts]
        
        # Apply additional filtering
        if post_id is not None:
            posts_data = [post for post in posts_data if post.get('post_id') == post_id]
        
        if validated_only is not None:
            if validated_only:
                posts_data = [post for post in posts_data if post.get('chat_id') is not None and post.get('message_id') is not None]
            else:
                posts_data = [post for post in posts_data if post.get('chat_id') is None or post.get('message_id') is None]
        
        return posts_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load posts: {str(e)}")

@app.get('/posts/{post_id}', summary="Get post by ID", tags=["Posts"])
@crash_handler
async def get_post(
    post_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get a specific post by ID. Requires authentication."""
    try:
        db = get_db()
        post = await db.get_post(post_id)
        if not post:
            raise HTTPException(status_code=404, detail=f"Post with ID {post_id} not found")
        return post.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get post: {str(e)}")

@app.post('/posts', summary="Create new post", status_code=201, tags=["Posts"])
@crash_handler
async def create_post(
    post_data: PostCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create a new post. Requires authentication."""
    try:
        db = get_db()
        
        # If post_id is provided, check if it already exists
        if post_data.post_id:
            existing_post = await db.get_post(post_data.post_id)
            if existing_post:
                raise HTTPException(status_code=409, detail=f"Post with ID {post_data.post_id} already exists")
        
        # Create post
        post_dict = post_data.model_dump()
        success = await db.add_post(post_dict)
        
        if success:
            return {"message": f"Post created successfully", "post_id": post_dict.get('post_id')}
        else:
            raise HTTPException(status_code=500, detail="Failed to create post")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create post: {str(e)}")

@app.put('/posts/{post_id}', summary="Update post", tags=["Posts"])
@crash_handler
async def update_post(
    post_id: int, 
    post_data: PostUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update an existing post. Requires authentication."""
    try:
        db = get_db()
        
        # Check if post exists
        existing_post = await db.get_post(post_id)
        if not existing_post:
            raise HTTPException(status_code=404, detail=f"Post with ID {post_id} not found")
        
        # Update post with only provided fields
        update_dict = {k: v for k, v in post_data.model_dump().items() if v is not None}
        
        if not update_dict:
            raise HTTPException(status_code=400, detail="No update data provided")
        
        success = await db.update_post(post_id, update_dict)
        
        if success:
            return {"message": f"Post {post_id} updated successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to update post")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update post: {str(e)}")

@app.delete('/posts/{post_id}', summary="Delete post", tags=["Posts"])
@crash_handler
async def delete_post(
    post_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Delete a post by ID. Requires authentication."""
    try:
        db = get_db()
        
        # Check if post exists
        existing_post = await db.get_post(post_id)
        if not existing_post:
            raise HTTPException(status_code=404, detail=f"Post with ID {post_id} not found")
        
        success = await db.delete_post(post_id)
        
        if success:
            return {"message": f"Post {post_id} deleted successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to delete post")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete post: {str(e)}")

# ============= TASKS CRUD =============

@app.get('/tasks', summary="Get all tasks", response_model=List[Dict], tags=["Tasks"])
@crash_handler
async def get_tasks(
    task_id: Optional[int] = Query(None, description="Filter by task ID"),
    status: Optional[str] = Query(None, description="Filter by task status"),
    name: Optional[str] = Query(None, description="Filter by task name (partial match)"),
    current_user: dict = Depends(get_current_user)
):
    """Get all tasks with optional filtering. Requires authentication."""
    try:
        db = get_db()
        tasks = await db.load_all_tasks()
        
        # Convert to dict format for JSON response
        tasks_data = [task.to_dict() for task in tasks]
        
        # Apply filtering
        if task_id is not None:
            tasks_data = [task for task in tasks_data if task.get('task_id') == task_id]
        
        if status:
            tasks_data = [task for task in tasks_data if task.get('status', '').upper() == status.upper()]
        
        if name:
            tasks_data = [task for task in tasks_data if name.lower() in task.get('name', '').lower()]
        
        return tasks_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load tasks: {str(e)}")

@app.get('/tasks/{task_id}', summary="Get task by ID", tags=["Tasks"])
@crash_handler
async def get_task(
    task_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get a specific task by ID. Requires authentication."""
    try:
        db = get_db()
        task = await db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        return task.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get task: {str(e)}")

@app.post('/tasks', summary="Create new task", status_code=201, tags=["Tasks"])
@crash_handler
async def create_task(
    task_data: TaskCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create a new task. Requires authentication."""
    try:
        db = get_db()
        
        # Validate that accounts exist
        for phone_number in task_data.accounts:
            account = await db.get_account(phone_number)
            if not account:
                raise HTTPException(status_code=400, detail=f"Account with phone number {phone_number} not found")
        
        # Validate that posts exist
        for post_id in task_data.post_ids:
            post = await db.get_post(post_id)
            if not post:
                raise HTTPException(status_code=400, detail=f"Post with ID {post_id} not found")
        
        # Create task
        task_dict = task_data.model_dump()
        success = await db.add_task(task_dict)
        
        if success:
            return {"message": f"Task '{task_data.name}' created successfully", "task_id": task_dict.get('task_id')}
        else:
            raise HTTPException(status_code=500, detail="Failed to create task")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create task: {str(e)}")

@app.put('/tasks/{task_id}', summary="Update task", tags=["Tasks"])
@crash_handler
async def update_task(
    task_id: int, 
    task_data: TaskUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update an existing task. Requires authentication."""
    try:
        db = get_db()
        
        # Check if task exists
        existing_task = await db.get_task(task_id)
        if not existing_task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        
        # Update task with only provided fields
        update_dict = {k: v for k, v in task_data.model_dump().items() if v is not None}
        
        if not update_dict:
            raise HTTPException(status_code=400, detail="No update data provided")
        
        # Validate accounts if provided
        if 'accounts' in update_dict:
            for phone_number in update_dict['accounts']:
                account = await db.get_account(phone_number)
                if not account:
                    raise HTTPException(status_code=400, detail=f"Account with phone number {phone_number} not found")
        
        # Validate posts if provided
        if 'post_ids' in update_dict:
            for post_id in update_dict['post_ids']:
                post = await db.get_post(post_id)
                if not post:
                    raise HTTPException(status_code=400, detail=f"Post with ID {post_id} not found")
        
        success = await db.update_task(task_id, update_dict)
        
        if success:
            return {"message": f"Task {task_id} updated successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to update task")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update task: {str(e)}")

@app.delete('/tasks/{task_id}', summary="Delete task", tags=["Tasks"])
@crash_handler
async def delete_task(
    task_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Delete a task by ID. Requires authentication."""
    try:
        db = get_db()
        
        # Check if task exists
        existing_task = await db.get_task(task_id)
        if not existing_task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        
        success = await db.delete_task(task_id)
        
        if success:
            return {"message": f"Task {task_id} deleted successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to delete task")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete task: {str(e)}")

# ============= TASK ACTIONS =============

@app.get('/tasks/{task_id}/status', summary="Get task status", tags=["Task Actions"])
@crash_handler
async def get_task_status(
    task_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get the current status of a task. Requires authentication."""
    try:
        db = get_db()
        task = await db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        
        status = await task.get_status()
        from main_logic.schemas import status_name
        return {"task_id": task_id, "status": status_name(status)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get task status: {str(e)}")

@app.post('/tasks/{task_id}/start', summary="Start task execution", tags=["Task Actions"])
@crash_handler
async def start_task(
    task_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Start task execution. Requires authentication."""
    try:
        db = get_db()
        task = await db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        
        await task.start()
        return {"message": f"Task {task_id} started successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start task: {str(e)}")

@app.post('/tasks/{task_id}/pause', summary="Pause task execution", tags=["Task Actions"])
@crash_handler
async def pause_task(
    task_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Pause task execution. Requires authentication."""
    try:
        db = get_db()
        task = await db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        
        await task.pause()
        return {"message": f"Task {task_id} paused successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to pause task: {str(e)}")

@app.post('/tasks/{task_id}/resume', summary="Resume task execution", tags=["Task Actions"])
@crash_handler
async def resume_task(
    task_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Resume task execution. Requires authentication."""
    try:
        db = get_db()
        task = await db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        
        await task.resume()
        return {"message": f"Task {task_id} resumed successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to resume task: {str(e)}")

@app.get('/tasks/{task_id}/report', summary="Get task execution report", tags=["Task Actions"])
@crash_handler
async def get_task_report(
    task_id: int,
    report_type: str = Query("success", description="Type of report (success, all, errors)"),
    run_id: Optional[str] = Query(None, description="Specific run ID to get report for. If not provided, returns latest run report."),
    current_user: dict = Depends(get_current_user)
):
    """Get execution report for a task. By default returns the latest run report. Requires authentication."""
    try:
        db = get_db()
        task = await db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        
        from auxilary_logic.reporter import RunEventManager, create_report
        event_manager = RunEventManager()

        effective_run_id = run_id

        if run_id is not None:
            events = await event_manager.get_events(run_id)
        else:
            runs_df = await event_manager.get_runs(task_id)
            if runs_df is None or runs_df.empty:
                return {
                    "message": f"No runs found for task {task_id}",
                    "task_id": task_id
                }
            effective_run_id = str(runs_df.iloc[0]["run_id"])
            events = await event_manager.get_events(effective_run_id)

        if events is None or events.empty:
            return {
                "message": f"No reportable events for task {task_id}",
                "task_id": task_id,
                "run_id": effective_run_id
            }

        report = await create_report(events, report_type)

        if report is None or report.empty:
            return {
                "message": f"No report available for task {task_id}",
                "task_id": task_id,
                "run_id": effective_run_id
            }

        report = report.drop(columns=['_id'], errors='ignore')
        report_records = report.to_dict(orient='records')
        report_records = convert_to_serializable(report_records)

        response = {"task_id": task_id, "report": report_records}
        if effective_run_id is not None:
            response["run_id"] = effective_run_id

        return response
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get task report: {str(e)}")

@app.get('/tasks/{task_id}/runs', summary="Get all runs for a task", tags=["Task Actions"])
@crash_handler
async def get_task_runs(
    task_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get all execution runs for a specific task, ordered by most recent first. Requires authentication."""
    try:
        db = get_db()
        task = await db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        
        from auxilary_logic.reporter import RunEventManager
        from pandas import DataFrame
        import json

        eventManager = RunEventManager()
        runs: DataFrame = await eventManager.get_runs(task_id)
        # Drop _id column if it exists (database already removes it, but ensure it's gone)
        runs_json = json.loads(runs.drop(columns=['_id'], errors='ignore').to_json(orient='records'))

        return {
            "task_id": task_id,
            "total_runs": len(runs_json),
            "runs": runs_json
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get task runs: {str(e)}")

@app.get('/tasks/{task_id}/runs/{run_id}/report', summary="Get report for specific run", tags=["Task Actions"])
@crash_handler
async def get_run_report(
    task_id: int,
    run_id: str,
    report_type: str = Query("success", description="Type of report (success, all, errors)"),
    current_user: dict = Depends(get_current_user)
):
    """Get execution report for a specific run of a task. Requires authentication."""
    try:
        db = get_db()  # May be deleted as so it is only an unnecessary check
        task = await db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        
        from auxilary_logic.reporter import RunEventManager, create_report
        from pandas import DataFrame

        event_manager = RunEventManager()
        events: DataFrame = await event_manager.get_events(run_id)

        if events is None or events.empty:
            raise HTTPException(status_code=404, detail=f"Run with ID {run_id} not found.")

        report = await create_report(data=events, type=report_type)

        if report is None or report.empty:
            return {
                "message": f"No report available for run {run_id}",
                "task_id": task_id,
                "run_id": run_id
            }

        report = report.drop(columns=['_id'], errors='ignore')
        report_records = report.to_dict(orient='records')
        report_records = convert_to_serializable(report_records)

        return {"task_id": task_id, "run_id": run_id, "report": report_records}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get run report: {str(e)}")

@app.get('/runs', summary="Get all runs across all tasks", tags=["Task Actions"])
@crash_handler
async def get_all_runs(current_user: dict = Depends(get_current_user)):
    """Get all execution runs across all tasks. Requires authentication."""
    try:
        from auxilary_logic.reporter import RunEventManager
        eventManager = RunEventManager()
        
        # Get all tasks
        tasks_df = await eventManager.get_tasks()
        if tasks_df.empty:
            return {"total_tasks": 0, "total_runs": 0, "tasks": []}
        
        all_tasks_data = []
        total_runs = 0
        
        for _, task_row in tasks_df.iterrows():
            # Convert numpy types to native Python types
            task_id = int(task_row['task_id'])  # Convert np.int64 to int
            run_count = int(task_row['run_count'])  # Convert np.int64 to int
            
            # Get runs for this task
            runs_df = await eventManager.get_runs(task_id)
            runs = []
            
            if not runs_df.empty:
                runs = runs_df.to_dict('records')
                # Convert all non-serializable types using our helper function
                runs = convert_to_serializable(runs)
            
            all_tasks_data.append({
                "task_id": task_id,
                "run_count": run_count,
                "runs": runs
            })
            total_runs += run_count
        
        return {
            "total_tasks": len(all_tasks_data),
            "total_runs": total_runs,
            "tasks": all_tasks_data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get all runs: {str(e)}")

@app.delete('/tasks/{task_id}/runs/{run_id}', summary="Delete a specific run", tags=["Task Actions"])
@crash_handler
async def delete_run(
    task_id: int, 
    run_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Delete a specific run and all its events. Requires authentication."""
    try:
        db = get_db()
        task = await db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        
        from auxilary_logic.reporter import RunEventManager
        eventManager = RunEventManager()

        res = await eventManager.delete_run(run_id)
        
        if res['runs_deleted'] == 0:
            raise HTTPException(status_code=404, detail=f"Run with ID {run_id} not found")
        
        return {
            "message": f"Run {run_id} deleted successfully",
            "runs_deleted": res['runs_deleted'],
            "events_deleted": res['events_deleted']
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete run: {str(e)}")

@app.delete('/tasks/{task_id}/runs', summary="Delete all runs for a task", tags=["Task Actions"])
@crash_handler
async def delete_all_task_runs(
    task_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Delete all runs and their events for a specific task. Requires authentication."""
    try:
        db = get_db()
        task = await db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        
        from auxilary_logic.reporter import RunEventManager
        eventManager = RunEventManager()
        result = await eventManager.clear_runs(str(task_id))
        
        return {
            "message": f"All runs for task {task_id} deleted successfully",
            "runs_deleted": result['runs_deleted'],
            "events_deleted": result['events_deleted']
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete task runs: {str(e)}")

# ============= LEGACY ENDPOINTS (for backward compatibility) =============

# @app.post('/actions/run_task', summary="Run task (legacy endpoint)")
# async def run_task(
#     task_id: int
# ):
#     """Legacy endpoint to run a task. Use POST /tasks/{task_id}/start instead."""

#     @crash_handler
#     async def run_task_internal():
#         logger = setup_logger("main", "main.log")
#         logger.info(f"Task {task_id} starting...")

#         try:
#             db = get_db()
#             task = await db.get_task(task_id)

#             if not task:
#                 raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")

#             await task.run_and_wait()    
            
#         except Exception as e:
#             logger.error(f"Error in main: {e}")
#             raise
#         finally:
#             logger.info(f"Task {task_id} completed")

#     try:
#         await run_task_internal()
#         return {"status": f"Task {task_id} completed successfully"}
#     except HTTPException:
#         raise
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Task {task_id} failed: {str(e)}")
#     finally:
#         cleanup_logging()

# ============= BULK OPERATIONS =============

@app.post('/accounts/bulk', summary="Create multiple accounts", status_code=201, tags=["Bulk Operations"])
@crash_handler
async def create_accounts_bulk(
    accounts_data: List[AccountCreate],
    current_user: dict = Depends(get_current_user)
):
    """Create multiple accounts in bulk. Requires authentication."""
    try:
        from auxilary_logic.encryption import encrypt_secret, PURPOSE_PASSWORD
        
        db = get_db()
        results = []
        
        for account_data in accounts_data:
            try:
                # Check if account already exists
                existing_account = await db.get_account(account_data.phone_number)
                if existing_account:
                    results.append({
                        "phone_number": account_data.phone_number,
                        "status": "skipped",
                        "message": "Account already exists"
                    })
                    continue
                
                # Create account dictionary and encrypt password if provided
                account_dict = account_data.model_dump()
                
                # Handle password encryption
                if account_dict.get('password'):
                    account_dict['password_encrypted'] = encrypt_secret(account_dict['password'], PURPOSE_PASSWORD)
                    # Remove plain text password from dict
                    del account_dict['password']
                else:
                    account_dict['password_encrypted'] = None
                
                success = await db.add_account(account_dict)
                
                if success:
                    results.append({
                        "phone_number": account_data.phone_number,
                        "status": "success",
                        "message": "Account created successfully"
                    })
                else:
                    results.append({
                        "phone_number": account_data.phone_number,
                        "status": "failed",
                        "message": "Failed to create account"
                    })
            except Exception as e:
                results.append({
                    "phone_number": account_data.phone_number,
                    "status": "error",
                    "message": f"Error: {str(e)}"
                })
        
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create accounts in bulk: {str(e)}")

@app.post('/posts/bulk', summary="Create multiple posts", status_code=201, tags=["Bulk Operations"])
@crash_handler
async def create_posts_bulk(
    posts_data: List[PostCreate],
    current_user: dict = Depends(get_current_user)
):
    """Create multiple posts in bulk. Requires authentication."""
    try:
        db = get_db()
        results = []
        
        for post_data in posts_data:
            try:
                # If post_id is provided, check if it already exists
                if post_data.post_id:
                    existing_post = await db.get_post(post_data.post_id)
                    if existing_post:
                        results.append({
                            "post_id": post_data.post_id,
                            "status": "skipped",
                            "message": "Post already exists"
                        })
                        continue
                
                # Create post
                post_dict = post_data.model_dump()
                success = await db.add_post(post_dict)
                
                if success:
                    results.append({
                        "post_id": post_dict.get('post_id'),
                        "status": "success",
                        "message": "Post created successfully"
                    })
                else:
                    results.append({
                        "post_id": post_data.post_id,
                        "status": "failed",
                        "message": "Failed to create post"
                    })
            except Exception as e:
                results.append({
                    "post_id": post_data.post_id,
                    "status": "error",
                    "message": f"Error: {str(e)}"
                })
        
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create posts in bulk: {str(e)}")

@app.delete('/accounts/bulk', summary="Delete multiple accounts", tags=["Bulk Operations"])
@crash_handler
async def delete_accounts_bulk(
    phone_numbers: List[str],
    current_user: dict = Depends(get_current_user)
):
    """Delete multiple accounts in bulk. Requires authentication."""
    try:
        db = get_db()
        results = []
        
        for phone_number in phone_numbers:
            try:
                # Check if account exists
                existing_account = await db.get_account(phone_number)
                if not existing_account:
                    results.append({
                        "phone_number": phone_number,
                        "status": "not_found",
                        "message": "Account not found"
                    })
                    continue
                
                success = await db.delete_account(phone_number)
                
                if success:
                    results.append({
                        "phone_number": phone_number,
                        "status": "success",
                        "message": "Account deleted successfully"
                    })
                else:
                    results.append({
                        "phone_number": phone_number,
                        "status": "failed",
                        "message": "Failed to delete account"
                    })
            except Exception as e:
                results.append({
                    "phone_number": phone_number,
                    "status": "error",
                    "message": f"Error: {str(e)}"
                })
        
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete accounts in bulk: {str(e)}")

@app.delete('/posts/bulk', summary="Delete multiple posts", tags=["Bulk Operations"])
@crash_handler
async def delete_posts_bulk(
    post_ids: List[int],
    current_user: dict = Depends(get_current_user)
):
    """Delete multiple posts in bulk. Requires authentication."""
    try:
        db = get_db()
        results = []
        
        for post_id in post_ids:
            try:
                # Check if post exists
                existing_post = await db.get_post(post_id)
                if not existing_post:
                    results.append({
                        "post_id": post_id,
                        "status": "not_found",
                        "message": "Post not found"
                    })
                    continue
                
                success = await db.delete_post(post_id)
                
                if success:
                    results.append({
                        "post_id": post_id,
                        "status": "success",
                        "message": "Post deleted successfully"
                    })
                else:
                    results.append({
                        "post_id": post_id,
                        "status": "failed",
                        "message": "Failed to delete post"
                    })
            except Exception as e:
                results.append({
                    "post_id": post_id,
                    "status": "error",
                    "message": f"Error: {str(e)}"
                })
        
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete posts in bulk: {str(e)}")

# ============= UTILITY ENDPOINTS =============

@app.get('/stats', summary="Get database statistics", tags=["Utilities"])
@crash_handler
async def get_stats(current_user: dict = Depends(get_current_user)):
    """Get statistics about accounts, posts, and tasks. Requires authentication."""
    try:
        db = get_db()
        
        accounts = await db.load_all_accounts()
        posts = await db.load_all_posts()
        tasks = await db.load_all_tasks()
        
        # Task status breakdown
        task_statuses = {}
        for task in tasks:
            status = str(task.status)
            task_statuses[status] = task_statuses.get(status, 0) + 1
        
        # Post validation status
        validated_posts = sum(1 for post in posts if post.is_validated)
        unvalidated_posts = len(posts) - validated_posts
        
        return {
            "accounts": {
                "total": len(accounts)
            },
            "posts": {
                "total": len(posts),
                "validated": validated_posts,
                "unvalidated": unvalidated_posts
            },
            "tasks": {
                "total": len(tasks),
                "by_status": task_statuses
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get statistics: {str(e)}")

@app.post('/posts/{post_id}/validate', summary="Validate a specific post", tags=["Utilities"])
@crash_handler
async def validate_post(
    post_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Validate a specific post by extracting chat_id and message_id from its link. Requires authentication."""
    try:
        db = get_db()
        
        # Get the post
        post = await db.get_post(post_id)
        if not post:
            raise HTTPException(status_code=404, detail=f"Post with ID {post_id} not found")
        
        if post.is_validated:
            return {"message": f"Post {post_id} is already validated"}
        
        # Get an account to use for validation
        accounts = await db.load_all_accounts()
        if not accounts:
            raise HTTPException(status_code=400, detail="No accounts available for validation")
        
        # Create a client and validate the post
        client = Client(accounts[0])
        await client.connect()
        
        try:
            validated_post = await post.validate(client)
            return {
                "message": f"Post {post_id} validated successfully",
                "chat_id": validated_post.chat_id,
                "message_id": validated_post.message_id
            }
        finally:
            await client.disconnect()
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to validate post: {str(e)}")


# ============= PROXY MANAGEMENT ENDPOINTS =============

@app.get('/proxies', summary="Get all proxies", response_model=List[Dict], tags=["Proxies"])
@crash_handler
async def get_proxies(
    proxy_name: Optional[str] = Query(None, description="Filter by proxy name"),
    active_only: Optional[bool] = Query(None, description="Filter by active status"),
    current_user: dict = Depends(get_current_user)
):
    """
    Get all proxies with optional filtering.
    
    Returns proxy configurations with decrypted passwords (for authenticated users).
    Requires authentication.
    """
    try:
        db = get_db()
        
        # Get specific proxy
        if proxy_name:
            proxy = await db.get_proxy(proxy_name)
            if not proxy:
                raise HTTPException(status_code=404, detail=f"Proxy '{proxy_name}' not found")
            return [proxy]
        
        # Get all proxies or only active ones
        if active_only:
            proxies = await db.get_active_proxies()
        else:
            proxies = await db.get_all_proxies()
        
        return proxies
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get proxies: {str(e)}")


@app.get('/proxies/{proxy_name}', summary="Get proxy by name", tags=["Proxies"])
@crash_handler
async def get_proxy(
    proxy_name: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get a specific proxy by name with decrypted password.
    
    Requires authentication.
    """
    try:
        db = get_db()
        proxy = await db.get_proxy(proxy_name)
        
        if not proxy:
            raise HTTPException(status_code=404, detail=f"Proxy '{proxy_name}' not found")
        
        return proxy
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get proxy: {str(e)}")


@app.post('/proxies', summary="Create new proxy", status_code=201, tags=["Proxies"])
@crash_handler
async def create_proxy(
    proxy_name: str = Query(..., description="Unique proxy name/identifier"),
    host: str = Query(..., description="Proxy hostname or IP address"),
    port: int = Query(..., description="Proxy port", ge=1, le=65535),
    proxy_type: str = Query("socks5", description="Proxy type (socks5, socks4, http)"),
    username: Optional[str] = Query(None, description="Proxy authentication username"),
    password: Optional[str] = Query(None, description="Proxy authentication password (will be encrypted)"),
    rdns: bool = Query(True, description="Resolve DNS remotely"),
    active: bool = Query(True, description="Is proxy active?"),
    notes: Optional[str] = Query(None, description="Optional notes about the proxy"),
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new proxy configuration.
    
    Password is automatically encrypted before storage.
    Requires authentication.
    """
    try:
        db = get_db()
        
        # Validate proxy type
        proxy_type_lower = proxy_type.lower()
        if proxy_type_lower not in ['socks5', 'socks4', 'http']:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid proxy type '{proxy_type}'. Must be one of: socks5, socks4, http"
            )
        
        # Build proxy data
        from datetime import datetime, timezone as tz
        proxy_data = {
            'proxy_name': proxy_name,
            'host': host,
            'port': port,
            'type': proxy_type_lower,
            'rdns': rdns,
            'active': active,
            'connected_accounts': 0,
            'created_at': datetime.now(tz.utc),
            'updated_at': datetime.now(tz.utc)
        }
        
        if username:
            proxy_data['username'] = username
        if password:
            proxy_data['password'] = password  # Will be encrypted by add_proxy
        if notes:
            proxy_data['notes'] = notes
        
        success = await db.add_proxy(proxy_data)
        if not success:
            raise HTTPException(
                status_code=400,
                detail=f"Proxy '{proxy_name}' already exists or failed to create"
            )
        
        # Return created proxy (without password for security in response)
        created_proxy = await db.get_proxy(proxy_name)
        created_proxy.pop('password', None)
        created_proxy.pop('password_encrypted', None)
        
        return created_proxy
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create proxy: {str(e)}")


@app.put('/proxies/{proxy_name}', summary="Update proxy", tags=["Proxies"])
@crash_handler
async def update_proxy(
    proxy_name: str,
    host: Optional[str] = Query(None, description="Proxy hostname or IP address"),
    port: Optional[int] = Query(None, description="Proxy port", ge=1, le=65535),
    proxy_type: Optional[str] = Query(None, description="Proxy type (socks5, socks4, http)"),
    username: Optional[str] = Query(None, description="Proxy authentication username"),
    password: Optional[str] = Query(None, description="Proxy authentication password (will be encrypted)"),
    rdns: Optional[bool] = Query(None, description="Resolve DNS remotely"),
    active: Optional[bool] = Query(None, description="Is proxy active?"),
    notes: Optional[str] = Query(None, description="Optional notes about the proxy"),
    current_user: dict = Depends(get_current_user)
):
    """
    Update an existing proxy configuration.
    
    Only provided fields will be updated. Password is automatically encrypted.
    Requires authentication.
    """
    try:
        db = get_db()
        
        # Check if proxy exists
        existing_proxy = await db.get_proxy(proxy_name)
        if not existing_proxy:
            raise HTTPException(status_code=404, detail=f"Proxy '{proxy_name}' not found")
        
        # Build update data with only provided fields
        update_data = {}
        if host is not None:
            update_data['host'] = host
        if port is not None:
            update_data['port'] = port
        if proxy_type is not None:
            proxy_type_lower = proxy_type.lower()
            if proxy_type_lower not in ['socks5', 'socks4', 'http']:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid proxy type '{proxy_type}'. Must be one of: socks5, socks4, http"
                )
            update_data['type'] = proxy_type_lower
        if username is not None:
            update_data['username'] = username
        if password is not None:
            update_data['password'] = password  # Will be encrypted by update_proxy
        if rdns is not None:
            update_data['rdns'] = rdns
        if active is not None:
            update_data['active'] = active
        if notes is not None:
            update_data['notes'] = notes
        
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields provided for update")
        
        from datetime import datetime, timezone as tz
        update_data['updated_at'] = datetime.now(tz.utc)
        
        success = await db.update_proxy(proxy_name, update_data)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update proxy")
        
        # Return updated proxy (without password for security)
        updated_proxy = await db.get_proxy(proxy_name)
        updated_proxy.pop('password', None)
        updated_proxy.pop('password_encrypted', None)
        
        return updated_proxy
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update proxy: {str(e)}")


@app.delete('/proxies/{proxy_name}', summary="Delete proxy", tags=["Proxies"])
@crash_handler
async def delete_proxy(
    proxy_name: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete a proxy configuration.
    
    Requires authentication.
    """
    try:
        db = get_db()
        
        # Check if proxy exists
        proxy = await db.get_proxy(proxy_name)
        if not proxy:
            raise HTTPException(status_code=404, detail=f"Proxy '{proxy_name}' not found")
        
        # Check if proxy is in use
        connected_accounts = proxy.get('connected_accounts', 0)
        if connected_accounts > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete proxy '{proxy_name}': currently connected to {connected_accounts} account(s)"
            )
        
        success = await db.delete_proxy(proxy_name)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete proxy")
        
        return {"message": f"Proxy '{proxy_name}' deleted successfully", "proxy_name": proxy_name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete proxy: {str(e)}")


@app.get('/proxies/stats/summary', summary="Get proxy statistics", tags=["Proxies"])
@crash_handler
async def get_proxy_stats(current_user: dict = Depends(get_current_user)):
    """
    Get statistics about proxies in the system.
    
    Returns:
    - Total proxy count
    - Active proxy count
    - Total connected accounts across all proxies
    - Least used proxy
    - Most used proxy
    
    Requires authentication.
    """
    try:
        db = get_db()
        
        all_proxies = await db.get_all_proxies()
        active_proxies = [p for p in all_proxies if p.get('active', False)]
        
        total_connections = sum(p.get('connected_accounts', 0) for p in all_proxies)
        
        # Find least and most used
        least_used = None
        most_used = None
        if all_proxies:
            least_used = min(all_proxies, key=lambda p: p.get('connected_accounts', 0))
            most_used = max(all_proxies, key=lambda p: p.get('connected_accounts', 0))
        
        stats = {
            'total_proxies': len(all_proxies),
            'active_proxies': len(active_proxies),
            'inactive_proxies': len(all_proxies) - len(active_proxies),
            'total_connected_accounts': total_connections,
            'least_used_proxy': {
                'proxy_name': least_used.get('proxy_name'),
                'connected_accounts': least_used.get('connected_accounts', 0)
            } if least_used else None,
            'most_used_proxy': {
                'proxy_name': most_used.get('proxy_name'),
                'connected_accounts': most_used.get('connected_accounts', 0)
            } if most_used else None
        }
        
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get proxy stats: {str(e)}")
    

# ============= REACTION PALETTES CRUD =============

@app.get('/palettes', summary="Get all reaction palettes", tags=["Reaction Palettes"])
@crash_handler
async def get_palettes(
    palette_name: Optional[str] = Query(None, description="Filter by palette name"),
    current_user: dict = Depends(get_current_user)
):
    """
    Get all reaction palettes with optional filtering.
    
    Palettes define emoji sets used for reactions in tasks.
    Requires authentication.
    """
    try:
        db = get_db()
        
        if palette_name:
            palette = await db.get_palette(palette_name)
            if not palette:
                raise HTTPException(status_code=404, detail=f"Palette '{palette_name}' not found")
            return [palette]
        
        palettes = await db.get_all_palettes()
        return palettes
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get palettes: {str(e)}")


@app.get('/palettes/{palette_name}', summary="Get palette by name", tags=["Reaction Palettes"])
@crash_handler
async def get_palette(
    palette_name: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get a specific reaction palette by name.
    
    Returns palette configuration including emojis list and ordering settings.
    Requires authentication.
    """
    try:
        db = get_db()
        palette = await db.get_palette(palette_name)
        
        if not palette:
            raise HTTPException(status_code=404, detail=f"Palette '{palette_name}' not found")
        
        return palette
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get palette: {str(e)}")


@app.post('/palettes', summary="Create new reaction palette", status_code=201, tags=["Reaction Palettes"])
@crash_handler
async def create_palette(
    palette_name: str = Query(..., description="Unique palette name"),
    emojis: str = Query(..., description="Comma-separated list of emojis"),
    ordered: bool = Query(False, description="If true, emojis are used in sequence; if false, chosen randomly"),
    description: Optional[str] = Query(None, description="Optional palette description"),
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new reaction palette.
    
    Palettes define sets of emojis that can be used in reaction tasks.
    - **ordered=false**: Emojis are chosen randomly for each reaction
    - **ordered=true**: Emojis are used sequentially in the order defined
    
    Requires authentication.
    """
    try:
        from datetime import datetime, timezone
        
        db = get_db()
        
        # Check if palette already exists
        existing = await db.get_palette(palette_name)
        if existing:
            raise HTTPException(status_code=409, detail=f"Palette '{palette_name}' already exists")
        
        # Parse emojis
        emoji_list = [e.strip() for e in emojis.split(',') if e.strip()]
        if not emoji_list:
            raise HTTPException(status_code=400, detail="At least one emoji is required")
        
        # Create palette data
        palette_data = {
            'palette_name': palette_name.lower(),
            'emojis': emoji_list,
            'ordered': ordered,
            'description': description or f"{palette_name.capitalize()} reactions palette",
            'created_at': datetime.now(timezone.utc),
            'updated_at': datetime.now(timezone.utc)
        }
        
        success = await db.add_palette(palette_data)
        
        if success:
            return {
                "message": f"Palette '{palette_name}' created successfully",
                "palette_name": palette_name.lower(),
                "emoji_count": len(emoji_list)
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to create palette")
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create palette: {str(e)}")


@app.put('/palettes/{palette_name}', summary="Update reaction palette", tags=["Reaction Palettes"])
@crash_handler
async def update_palette(
    palette_name: str,
    emojis: Optional[str] = Query(None, description="Comma-separated list of emojis"),
    ordered: Optional[bool] = Query(None, description="If true, emojis are used in sequence; if false, chosen randomly"),
    description: Optional[str] = Query(None, description="Palette description"),
    current_user: dict = Depends(get_current_user)
):
    """
    Update an existing reaction palette.
    
    Only provided fields will be updated. Palette name cannot be changed.
    Requires authentication.
    """
    try:
        from datetime import datetime, timezone
        
        db = get_db()
        
        # Check if palette exists
        existing = await db.get_palette(palette_name)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Palette '{palette_name}' not found")
        
        # Build update data
        update_data = {}
        
        if emojis is not None:
            emoji_list = [e.strip() for e in emojis.split(',') if e.strip()]
            if not emoji_list:
                raise HTTPException(status_code=400, detail="At least one emoji is required")
            update_data['emojis'] = emoji_list
        
        if ordered is not None:
            update_data['ordered'] = ordered
        
        if description is not None:
            update_data['description'] = description
        
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")
        
        update_data['updated_at'] = datetime.now(timezone.utc)
        
        success = await db.update_palette(palette_name, update_data)
        
        if success:
            return {
                "message": f"Palette '{palette_name}' updated successfully"
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to update palette")
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update palette: {str(e)}")


@app.delete('/palettes/{palette_name}', summary="Delete reaction palette", tags=["Reaction Palettes"])
@crash_handler
async def delete_palette(
    palette_name: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete a reaction palette.
    
    Warning: Tasks using this palette will fail to execute.
    Requires authentication.
    """
    try:
        db = get_db()
        
        # Check if palette exists
        existing = await db.get_palette(palette_name)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Palette '{palette_name}' not found")
        
        success = await db.delete_palette(palette_name)
        
        if success:
            return {
                "message": f"Palette '{palette_name}' deleted successfully"
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to delete palette")
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete palette: {str(e)}")


# ============= CHANNEL MANAGEMENT ENDPOINTS =============

@app.get('/channels', summary="Get all channels", response_model=List[Dict], tags=["Channels"])
@crash_handler
async def get_channels(
    chat_id: Optional[int] = Query(None, description="Filter by chat_id"),
    tag: Optional[str] = Query(None, description="Filter by tag"),
    name: Optional[str] = Query(None, description="Search by channel name"),
    current_user: dict = Depends(get_current_user)
):
    """
    Get all channels with optional filtering.
    
    Supports filtering by:
    - chat_id: Exact match on Telegram chat ID
    - tag: Channels with specific tag
    - name: Partial match on channel name (case-insensitive)
    
    Requires authentication.
    """
    try:
        db = get_db()
        
        # Filter by specific channel
        if chat_id is not None:
            channel = await db.get_channel(chat_id)
            if not channel:
                raise HTTPException(status_code=404, detail=f"Channel with chat_id {chat_id} not found")
            return [convert_to_serializable(channel.to_dict())]
        
        # Filter by tag
        if tag:
            channels = await db.get_channels_by_tag(tag)
            return [convert_to_serializable(c.to_dict()) for c in channels]
        
        # Search by name
        if name:
            channels = await db.search_channels_by_name(name)
            return [convert_to_serializable(c.to_dict()) for c in channels]
        
        # Get all channels
        channels = await db.get_all_channels()
        return [convert_to_serializable(c.to_dict()) for c in channels]
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get channels: {str(e)}")


@app.get('/channels/{chat_id}', summary="Get channel by chat_id", tags=["Channels"])
@crash_handler
async def get_channel(
    chat_id: int,
    current_user: dict = Depends(get_current_user)
):
    """
    Get a specific channel by chat_id.
    
    Accepts both normalized and -100 prefixed chat IDs.
    Requires authentication.
    """
    try:
        db = get_db()
        channel = await db.get_channel(chat_id)
        
        if not channel:
            raise HTTPException(status_code=404, detail=f"Channel with chat_id {chat_id} not found")
        
        return convert_to_serializable(channel.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get channel: {str(e)}")


@app.post('/channels/bulk', summary="Get multiple channels by chat_ids", tags=["Channels"])
@crash_handler
async def get_channels_bulk(
    chat_ids: List[int],
    current_user: dict = Depends(get_current_user)
):
    """
    Get multiple channels by their chat_ids in a single request.
    
    Accepts both normalized and -100 prefixed chat IDs.
    Returns list of found channels (may be fewer than requested if some don't exist).
    
    Request body: List of chat_ids as JSON array
    Example: [123456789, -1001234567890, 987654321]
    
    Requires authentication.
    """
    try:
        db = get_db()
        
        if not chat_ids:
            return []
        
        channels = await db.get_channels_bulk(chat_ids)
        return [convert_to_serializable(c.to_dict()) for c in channels]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get channels: {str(e)}")


@app.post('/channels', summary="Create new channel", status_code=201, tags=["Channels"])
@crash_handler
async def create_channel(
    chat_id: int = Query(..., description="Telegram chat ID (unique identifier)"),
    channel_name: Optional[str] = Query(None, description="Channel name/title"),
    is_private: bool = Query(False, description="Is the channel private?"),
    has_enabled_reactions: bool = Query(True, description="Does the channel have reactions enabled?"),
    reactions_only_for_subscribers: bool = Query(False, description="Are reactions only for subscribers?"),
    discussion_chat_id: Optional[int] = Query(None, description="Discussion group chat ID if exists"),
    tags: Optional[str] = Query(None, description="Comma-separated list of tags"),
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new channel entry.
    
    Requires authentication.
    """
    try:
        db = get_db()
        from main_logic.channel import Channel
        from datetime import datetime, timezone as tz
        
        # Parse tags
        tag_list = []
        if tags:
            tag_list = [t.strip() for t in tags.split(',') if t.strip()]
        
        # Create channel data
        channel_data = {
            'chat_id': chat_id,
            'channel_name': channel_name,
            'is_private': is_private,
            'has_enabled_reactions': has_enabled_reactions,
            'reactions_only_for_subscribers': reactions_only_for_subscribers,
            'discussion_chat_id': discussion_chat_id,
            'tags': tag_list,
            'channel_hash': '',
            'created_at': datetime.now(tz.utc),
            'updated_at': datetime.now(tz.utc)
        }
        
        # Create channel
        success = await db.add_channel(channel_data)
        if not success:
            raise HTTPException(
                status_code=400,
                detail=f"Channel with chat_id {chat_id} already exists or failed to create"
            )
        
        # Return created channel
        created_channel = await db.get_channel(chat_id)
        return convert_to_serializable(created_channel.to_dict())
        
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create channel: {str(e)}")


@app.put('/channels/{chat_id}', summary="Update channel", tags=["Channels"])
@crash_handler
async def update_channel(
    chat_id: int,
    channel_name: Optional[str] = Query(None, description="Channel name/title"),
    is_private: Optional[bool] = Query(None, description="Is the channel private?"),
    has_enabled_reactions: Optional[bool] = Query(None, description="Does the channel have reactions enabled?"),
    reactions_only_for_subscribers: Optional[bool] = Query(None, description="Are reactions only for subscribers?"),
    discussion_chat_id: Optional[int] = Query(None, description="Discussion group chat ID if exists"),
    tags: Optional[str] = Query(None, description="Comma-separated list of tags"),
    current_user: dict = Depends(get_current_user)
):
    """
    Update an existing channel.
    
    Only provided fields will be updated.
    Requires authentication.
    """
    try:
        db = get_db()
        
        # Check if channel exists
        existing_channel = await db.get_channel(chat_id)
        if not existing_channel:
            raise HTTPException(status_code=404, detail=f"Channel with chat_id {chat_id} not found")
        
        # Build update data with only provided fields
        update_data = {}
        if channel_name is not None:
            update_data['channel_name'] = channel_name
        if is_private is not None:
            update_data['is_private'] = is_private
        if has_enabled_reactions is not None:
            update_data['has_enabled_reactions'] = has_enabled_reactions
        if reactions_only_for_subscribers is not None:
            update_data['reactions_only_for_subscribers'] = reactions_only_for_subscribers
        if discussion_chat_id is not None:
            update_data['discussion_chat_id'] = discussion_chat_id
        if tags is not None:
            tag_list = [t.strip() for t in tags.split(',') if t.strip()]
            update_data['tags'] = tag_list
        
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields provided for update")
        
        from datetime import datetime, timezone as tz
        update_data['updated_at'] = datetime.now(tz.utc)
        
        success = await db.update_channel(chat_id, update_data)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update channel")
        
        # Return updated channel
        updated_channel = await db.get_channel(chat_id)
        return convert_to_serializable(updated_channel.to_dict())
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update channel: {str(e)}")


@app.delete('/channels/{chat_id}', summary="Delete channel", tags=["Channels"])
@crash_handler
async def delete_channel(
    chat_id: int,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete a channel from the database.
    
    Note: This does not delete the actual Telegram channel, only the local database entry.
    Associated posts will remain in the database.
    Requires authentication.
    """
    try:
        db = get_db()
        
        # Check if channel exists
        channel = await db.get_channel(chat_id)
        if not channel:
            raise HTTPException(status_code=404, detail=f"Channel with chat_id {chat_id} not found")
        
        success = await db.delete_channel(chat_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete channel")
        
        return {"message": f"Channel with chat_id {chat_id} deleted successfully", "chat_id": chat_id}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete channel: {str(e)}")


@app.get('/channels/stats/summary', summary="Get channel statistics", tags=["Channels"])
@crash_handler
async def get_channel_stats(current_user: dict = Depends(get_current_user)):
    """
    Get statistics about channels in the system.
    
    Returns:
    - Total channel count
    - Private vs public channel breakdown
    - Channels with reactions enabled
    - Channel distribution by tags
    
    Requires authentication.
    """
    try:
        db = get_db()
        
        all_channels = await db.get_all_channels()
        
        total_channels = len(all_channels)
        private_channels = sum(1 for c in all_channels if c.is_private)
        public_channels = total_channels - private_channels
        reactions_enabled = sum(1 for c in all_channels if c.has_enabled_reactions)
        
        # Tag distribution
        tag_distribution = {}
        for channel in all_channels:
            for tag in channel.tags:
                tag_distribution[tag] = tag_distribution.get(tag, 0) + 1
        
        stats = {
            'total_channels': total_channels,
            'private_channels': private_channels,
            'public_channels': public_channels,
            'channels_with_reactions': reactions_enabled,
            'tag_distribution': tag_distribution
        }
        
        return stats
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get channel stats: {str(e)}")


@app.get('/channels/with-post-counts', summary="Get channels with post counts", tags=["Channels"])
@crash_handler
async def get_channels_with_post_counts(current_user: dict = Depends(get_current_user)):
    """
    Get all channels with their post counts.
    
    Returns a list of channels with an additional 'post_count' field indicating
    how many posts exist for each channel.
    
    Requires authentication.
    """
    try:
        db = get_db()
        
        channels_with_counts = await db.get_channels_with_post_counts()
        return convert_to_serializable(channels_with_counts)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get channels with post counts: {str(e)}")


@app.get('/channels/{chat_id}/subscribers', summary="Get accounts subscribed to a channel", tags=["Channels"])
@crash_handler
async def get_channel_subscribers(
    chat_id: int,
    current_user: dict = Depends(get_current_user)
):
    """
    Get all accounts that are subscribed to a specific channel.
    
    Accepts both normalized and -100 prefixed chat IDs.
    Returns list of Account objects (secure format, without passwords).
    
    Requires authentication.
    """
    try:
        db = get_db()
        
        # Get subscribers using native MongoDB query
        subscribers = await db.get_channel_subscribers(chat_id)
        
        # Convert to secure dict format (excludes passwords)
        return [account.to_dict(secure=True) for account in subscribers]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get channel subscribers: {str(e)}")


@app.get('/accounts/{phone_number}/channels', summary="Get account's subscribed channels", tags=["Accounts"])
@crash_handler
async def get_account_subscribed_channels(
    phone_number: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get all channels that an account is subscribed to.
    
    Returns list of Channel objects based on the account's subscribed_to list.
    Requires authentication.
    """
    try:
        db = get_db()
        
        # Check if account exists
        account = await db.get_account(phone_number)
        if not account:
            raise HTTPException(status_code=404, detail=f"Account with phone number {phone_number} not found")
        
        # Get subscribed channels
        channels = await db.get_subscribed_channels(phone_number)
        return [convert_to_serializable(c.to_dict()) for c in channels]
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get subscribed channels: {str(e)}")


@app.post('/accounts/{phone_number}/channels/sync', summary="Sync account's subscribed channels from Telegram", tags=["Accounts"])
@crash_handler
async def sync_account_subscribed_channels(
    phone_number: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Fetch and update all channels that an account is subscribed to from Telegram.
    
    This connects to Telegram, fetches all subscribed channels, updates the account's
    subscribed_to field, and upserts channel data to the channels collection.
    
    Returns list of chat_ids that were synced.
    Requires authentication.
    """
    try:
        db = get_db()
        
        # Check if account exists
        account_data = await db.get_account(phone_number)
        if not account_data:
            raise HTTPException(status_code=404, detail=f"Account with phone number {phone_number} not found")
        
        # Create Account object and Client
        account = Account.from_dict(account_data)
        client = Client(account)
        
        try:
            # Connect and fetch subscribed channels
            await client.connect()
            chat_ids = await client.fetch_and_update_subscribed_channels()
            
            # Record sync metadata in database
            from datetime import datetime, timezone
            sync_metadata = {
                'last_channel_sync_at': datetime.now(timezone.utc),
                'last_channel_sync_count': len(chat_ids),
                'updated_at': datetime.now(timezone.utc)
            }
            await db.update_account(phone_number, sync_metadata)
            
            return {
                "message": f"Successfully synced {len(chat_ids)} channels for account {phone_number}",
                "phone_number": phone_number,
                "channels_count": len(chat_ids),
                "chat_ids": chat_ids,
                "synced_at": sync_metadata['last_channel_sync_at'].isoformat()
            }
        finally:
            # Always disconnect client
            await client.disconnect()
        
    except HTTPException:
        raise
    except ValueError as e:
        # No session available - user needs to login first
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to sync subscribed channels: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv
    load_dotenv()
    
    backend_ip = os.getenv("backend_ip", "127.0.0.1")
    backend_port = int(os.getenv("backend_port", "8080"))

    uvicorn.run(app, host=backend_ip, port=backend_port)