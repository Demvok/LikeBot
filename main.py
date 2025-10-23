import asyncio, atexit, os, uuid
from dotenv import load_dotenv
from collections import deque
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from typing import Optional, List, Dict
from agent import *
from logger import crash_handler, cleanup_logging, get_log_directory
from taskhandler import *
from database import get_db
from fastapi.middleware.cors import CORSMiddleware
from schemas import (
    AccountCreate, AccountUpdate, AccountResponse, AccountPasswordResponse,
    PostCreate, PostUpdate, PostResponse,
    TaskCreate, TaskUpdate, TaskResponse,
    SuccessResponse, ErrorResponse, BulkOperationResult,
    DatabaseStats, ValidationResult, serialize_for_json,
    LoginStatus
)

load_dotenv()
frontend_http = os.getenv("frontend_http", None)

atexit.register(cleanup_logging)  # Register cleanup function

app = FastAPI(title="LikeBot API", description="Full CRUD API for LikeBot automation", version="1.0.1")

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


@app.get("/", summary="Health check")
async def root():
    return {"message": "LikeBot API Server is running", "version": app.version}

# ============= LOG STREAMING =============

@app.websocket('/ws/logs')
async def stream_logs(websocket: WebSocket):
    """Stream log file updates over a websocket connection."""
    log_file = websocket.query_params.get('log_file', 'main.log')
    tail_param = websocket.query_params.get('tail', '200')

    try:
        tail = int(tail_param)
    except ValueError:
        tail = 200

    tail = max(0, min(tail, 1000))
    log_path = _resolve_log_path(log_file)

    await websocket.accept()

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
    except Exception as exc:
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Log streaming interrupted: {exc}"
            })
        finally:
            await websocket.close(code=1011)

# ============= ACCOUNTS CRUD =============

@app.get('/accounts', summary="Get all accounts", response_model=List[Dict])
@crash_handler
async def get_accounts(
    phone_number: Optional[str] = Query(None, description="Filter by phone number")
):
    """Get all accounts with optional filtering by phone number."""
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

@app.get('/accounts/{phone_number}', summary="Get account by phone number")
@crash_handler
async def get_account(phone_number: str):
    """Get a specific account by phone number."""
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

@app.post('/accounts', summary="Create new account in database without login", status_code=201)
@crash_handler
async def create_account_without_login(account_data: AccountCreate):
    """Create a new account without logging in. Useful for pre-registering accounts. Legacy endpoint."""
    try:
        from encryption import encrypt_secret, PURPOSE_PASSWORD
        
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


@app.put('/accounts/{phone_number}', summary="Update account")
@crash_handler
async def update_account(phone_number: str, account_data: AccountUpdate):
    """Update an existing account."""
    try:
        from encryption import encrypt_secret, PURPOSE_PASSWORD
        
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

@app.delete('/accounts/{phone_number}', summary="Delete account")
@crash_handler
async def delete_account(phone_number: str):
    """Delete an account by phone number."""
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

@app.put('/accounts/{phone_number}/validate', summary="Validate account connection to Telegram")
@crash_handler
async def validate_account(phone_number: str):
    """Validate an account by testing its connection to Telegram."""
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

@app.get('/accounts/{phone_number}/password', summary="Get account password (secure endpoint)", response_model=AccountPasswordResponse)
@crash_handler
async def get_account_password(phone_number: str):
    """
    Get account password securely. This is a mockup endpoint for secure password retrieval.
    In production, this should require additional authentication/authorization.
    """
    try:
        from encryption import decrypt_secret, PURPOSE_PASSWORD
        
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

# ============= LOGIN PROCESS ENDPOINTS =============

@app.post('/accounts/create/start', summary="Start login process", status_code=200)
@crash_handler
async def login_start(
    phone_number: str = Query(..., description="Phone number with country code"),
    password: Optional[str] = Query(None, description="Password for 2FA (will be encrypted)"),
    session_name: Optional[str] = Query(None, description="Custom session name (optional)"),
    notes: Optional[str] = Query(None, description="Account notes (optional)")
):
    """
    Start the login process for a Telegram account.
    Returns login_session_id and status.
    Frontend should poll /accounts/create/status or proceed to /accounts/create/verify.
    """
    from agent import start_login, pending_logins
    from encryption import encrypt_secret, PURPOSE_PASSWORD
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


@app.post('/accounts/create/verify', summary="Verify login code", status_code=200)
@crash_handler
async def login_verify(
    login_session_id: str = Query(..., description="Login session ID from /accounts/create/start"),
    code: str = Query(..., description="Verification code from Telegram")
):
    """
    Submit verification code to continue login process.
    2FA passwords must be provided during /accounts/create/start, not here.
    """
    from agent import pending_logins
    
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


@app.get('/accounts/create/status', summary="Check login process status", status_code=200)
@crash_handler
async def login_status(
    login_session_id: str = Query(..., description="Login session ID from /accounts/create/start")
):
    """
    Check the status of an ongoing login process.
    Used for polling by the frontend.
    """
    from agent import pending_logins, cleanup_expired_logins
    
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

@app.get('/posts', summary="Get all posts", response_model=List[Dict])
@crash_handler
async def get_posts(
    post_id: Optional[int] = Query(None, description="Filter by post ID"),
    chat_id: Optional[int] = Query(None, description="Filter by chat ID"),
    validated_only: Optional[bool] = Query(None, description="Filter by validation status")
):
    """Get all posts with optional filtering."""
    try:
        db = get_db()
        posts = await db.load_all_posts()
        
        # Convert to dict format for JSON response
        posts_data = [post.to_dict() for post in posts]
        
        # Apply filtering
        if post_id is not None:
            posts_data = [post for post in posts_data if post.get('post_id') == post_id]
        
        if chat_id is not None:
            posts_data = [post for post in posts_data if post.get('chat_id') == chat_id]
        
        if validated_only is not None:
            if validated_only:
                posts_data = [post for post in posts_data if post.get('chat_id') is not None and post.get('message_id') is not None]
            else:
                posts_data = [post for post in posts_data if post.get('chat_id') is None or post.get('message_id') is None]
        
        return posts_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load posts: {str(e)}")

@app.get('/posts/{post_id}', summary="Get post by ID")
@crash_handler
async def get_post(post_id: int):
    """Get a specific post by ID."""
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

@app.post('/posts', summary="Create new post", status_code=201)
@crash_handler
async def create_post(post_data: PostCreate):
    """Create a new post."""
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

@app.put('/posts/{post_id}', summary="Update post")
@crash_handler
async def update_post(post_id: int, post_data: PostUpdate):
    """Update an existing post."""
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

@app.delete('/posts/{post_id}', summary="Delete post")
@crash_handler
async def delete_post(post_id: int):
    """Delete a post by ID."""
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

@app.get('/tasks', summary="Get all tasks", response_model=List[Dict])
@crash_handler
async def get_tasks(
    task_id: Optional[int] = Query(None, description="Filter by task ID"),
    status: Optional[str] = Query(None, description="Filter by task status"),
    name: Optional[str] = Query(None, description="Filter by task name (partial match)")
):
    """Get all tasks with optional filtering."""
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

@app.get('/tasks/{task_id}', summary="Get task by ID")
@crash_handler
async def get_task(task_id: int):
    """Get a specific task by ID."""
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

@app.post('/tasks', summary="Create new task", status_code=201)
@crash_handler
async def create_task(task_data: TaskCreate):
    """Create a new task."""
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

@app.put('/tasks/{task_id}', summary="Update task")
@crash_handler
async def update_task(task_id: int, task_data: TaskUpdate):
    """Update an existing task."""
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

@app.delete('/tasks/{task_id}', summary="Delete task")
@crash_handler
async def delete_task(task_id: int):
    """Delete a task by ID."""
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

@app.get('/tasks/{task_id}/status', summary="Get task status")
@crash_handler
async def get_task_status(task_id: int):
    """Get the current status of a task."""
    try:
        db = get_db()
        task = await db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        
        status = await task.get_status()
        return {"task_id": task_id, "status": status.name if hasattr(status, 'name') else str(status)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get task status: {str(e)}")

@app.post('/tasks/{task_id}/start', summary="Start task execution")
@crash_handler
async def start_task(task_id: int):
    """Start task execution."""
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

@app.post('/tasks/{task_id}/pause', summary="Pause task execution")
@crash_handler
async def pause_task(task_id: int):
    """Pause task execution."""
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

@app.post('/tasks/{task_id}/resume', summary="Resume task execution")
@crash_handler
async def resume_task(task_id: int):
    """Resume task execution."""
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

@app.get('/tasks/{task_id}/report', summary="Get task execution report")
@crash_handler
async def get_task_report(
    task_id: int,
    report_type: str = Query("success", description="Type of report (success, all, errors)"),
    run_id: Optional[str] = Query(None, description="Specific run ID to get report for. If not provided, returns latest run report.")
):
    """Get execution report for a task. By default returns the latest run report."""
    try:
        db = get_db()
        task = await db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        
        from reporter import RunEventManager, create_report
        import json
        eventManager = RunEventManager()        

        if run_id is not None:
            events = await eventManager.get_events(run_id)
        else:
            runs = await eventManager.get_runs(task_id) if run_id is None else None
            events = await eventManager.get_events(runs.iloc[0].loc['run_id']) if run_id is None and not runs.empty else None
        
        report = await create_report(events, report_type) if events is not None else None
        
        if report is None:
            return {"message": f"No report available for task {task_id}", "task_id": task_id}
        
        if '_id' in report.columns:
            report = report.drop('_id', axis=1)

        report = json.loads(report.to_json(orient='records'))

        return {"task_id": task_id, "report": report, "run_id": run_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get task report: {str(e)}")

@app.get('/tasks/{task_id}/runs', summary="Get all runs for a task")
@crash_handler
async def get_task_runs(task_id: int):
    """Get all execution runs for a specific task, ordered by most recent first."""
    try:
        db = get_db()
        task = await db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        
        from reporter import RunEventManager
        from pandas import DataFrame
        import json

        eventManager = RunEventManager()
        runs: DataFrame = await eventManager.get_runs(task_id)
        runs_json = json.loads(runs.drop(['_id'], axis=1).to_json(orient='records'))

        return {
            "task_id": task_id,
            "total_runs": len(runs_json),
            "runs": runs_json
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get task runs: {str(e)}")

@app.get('/tasks/{task_id}/runs/{run_id}/report', summary="Get report for specific run")
@crash_handler
async def get_run_report(
    task_id: int,
    run_id: str,
    report_type: str = Query("success", description="Type of report (success, all, errors)")
):
    """Get execution report for a specific run of a task."""
    try:
        db = get_db()  # May be deleted as so it is only an unnecessary check
        task = await db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        
        from reporter import RunEventManager, create_report
        from pandas import DataFrame
        import json

        eventManager = RunEventManager()
        events: DataFrame = await eventManager.get_events(run_id)

        if events.empty:
            raise HTTPException(status_code=404, detail=f"Run with ID {run_id} not found.")

        report = await create_report(data=events, type=report_type) if events is not None else None

        if report is None:
            return {"message": f"No report available for run {run_id}", "task_id": task_id, "run_id": run_id}

        if '_id' in report.columns:
            report = report.drop('_id', axis=1)

        report_json = json.loads(report.to_json(orient='records'))

        return {"task_id": task_id, "run_id": run_id, "report": report_json}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get run report: {str(e)}")

@app.get('/runs', summary="Get all runs across all tasks")
@crash_handler
async def get_all_runs():
    """Get all execution runs across all tasks."""
    try:
        from reporter import RunEventManager
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

@app.delete('/tasks/{task_id}/runs/{run_id}', summary="Delete a specific run")
@crash_handler
async def delete_run(task_id: int, run_id: str):
    """Delete a specific run and all its events."""
    try:
        db = get_db()
        task = await db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        
        from reporter import RunEventManager
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

@app.delete('/tasks/{task_id}/runs', summary="Delete all runs for a task")
@crash_handler
async def delete_all_task_runs(task_id: int):
    """Delete all runs and their events for a specific task."""
    try:
        db = get_db()
        task = await db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found")
        
        from reporter import RunEventManager
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

@app.post('/accounts/bulk', summary="Create multiple accounts", status_code=201)
@crash_handler
async def create_accounts_bulk(accounts_data: List[AccountCreate]):
    """Create multiple accounts in bulk."""
    try:
        from encryption import encrypt_secret, PURPOSE_PASSWORD
        
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

@app.post('/posts/bulk', summary="Create multiple posts", status_code=201)
@crash_handler
async def create_posts_bulk(posts_data: List[PostCreate]):
    """Create multiple posts in bulk."""
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

@app.delete('/accounts/bulk', summary="Delete multiple accounts")
@crash_handler
async def delete_accounts_bulk(phone_numbers: List[str]):
    """Delete multiple accounts in bulk."""
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

@app.delete('/posts/bulk', summary="Delete multiple posts")
@crash_handler
async def delete_posts_bulk(post_ids: List[int]):
    """Delete multiple posts in bulk."""
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

@app.get('/stats', summary="Get database statistics")
@crash_handler
async def get_stats():
    """Get statistics about accounts, posts, and tasks."""
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

@app.post('/posts/{post_id}/validate', summary="Validate a specific post")
@crash_handler
async def validate_post(post_id: int):
    """Validate a specific post by extracting chat_id and message_id from its link."""
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

if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv
    load_dotenv()
    
    backend_ip = os.getenv("backend_ip", "127.0.0.1")
    backend_port = int(os.getenv("backend_port", "8080"))

    uvicorn.run(app, host=backend_ip, port=backend_port)