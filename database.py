"""database.py
Async MongoDB storage for LikeBot.

Provides MongoStorage (get_db()) using motor.AsyncIOMotorClient:
- Lazy client/collection init, idempotent index creation (asyncio.Lock)
- CRUD for accounts, posts, tasks, users, runs, and events
- Accepts domain objects or dicts, strips MongoDB _id on return
- ensure_async decorator wraps sync helpers for async use
- Centralized database logic for all collections including reporter events/runs

Collections:
- accounts: Telegram account information
- posts: Post/message data
- tasks: Task definitions and status
- users: API user authentication
- runs: Task execution runs (reporter)
- events: Task execution events (reporter)

Environment:
- db_url (required), db_name (default "LikeBot"), db_timeout_ms (default 5000)
"""
import os, inspect, asyncio
from typing import Optional
from pandas import Timestamp
from functools import wraps
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError
from logger import setup_logger, load_config
from agent import Account
from taskhandler import Post, Task

config = load_config()
logger = setup_logger("DB", "main.log")

load_dotenv()
db_url = os.getenv('db_url')
db_name = os.getenv('db_name', 'LikeBot')
mongo_timeout_ms = int(os.getenv('db_timeout_ms', '5000'))

def ensure_async(func):
    if inspect.iscoroutinefunction(func):
        return func

    @wraps(func)
    async def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper

class MongoStorage():
    _accounts = None
    _db = None
    _posts = None
    _accounts = None
    _tasks = None
    _users = None
    _events = None
    _runs = None
    _proxies = None
    _palettes = None
    _client: Optional[AsyncIOMotorClient] = None
    _indexes_initialized = False
    _index_lock: Optional[asyncio.Lock] = None

    @classmethod
    def _init(cls):
        if cls._accounts is not None:
            return

        if not db_url:
            logger.critical("Environment variable 'db_url' is not set; cannot initialize MongoDB client.")
            raise RuntimeError("MongoDB connection string is missing (env 'db_url')")

        logger.info("Initializing MongoDB client and collections.")
        try:
            cls._client = AsyncIOMotorClient(db_url, serverSelectionTimeoutMS=mongo_timeout_ms)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Failed to create MongoDB client: %s", exc)
            raise

        cls._db = cls._client[db_name]
        cls._accounts = cls._db["accounts"]
        cls._posts = cls._db["posts"]
        cls._tasks = cls._db["tasks"]
        cls._users = cls._db["users"]
        cls._proxies = cls._db["proxies"]
        cls._palettes = cls._db["reaction_palettes"]
        
        # Reporter collections with write concerns
        from pymongo.write_concern import WriteConcern
        events_coll_name = config.get('database', {}).get('events_coll', 'events')
        runs_coll_name = config.get('database', {}).get('runs_coll', 'runs')
        cls._events = cls._db.get_collection(events_coll_name, write_concern=WriteConcern(w="majority", j=True))
        cls._runs = cls._db.get_collection(runs_coll_name, write_concern=WriteConcern(w="majority", j=True))

    @classmethod
    async def _ensure_ready(cls):
        cls._init()
        await cls._ensure_indexes()

    @classmethod
    async def _ensure_indexes(cls):
        if cls._indexes_initialized:
            return

        if cls._index_lock is None:
            cls._index_lock = asyncio.Lock()

        async with cls._index_lock:
            if cls._indexes_initialized:
                return

            try:
                await cls._client.admin.command("ping")
            except ServerSelectionTimeoutError as exc:
                logger.critical("Unable to reach MongoDB server: %s", exc)
                raise RuntimeError("MongoDB server unavailable. Check 'db_url' and database connectivity.") from exc

            try:
                # Import pymongo constants first
                from pymongo import ASCENDING, IndexModel
                
                await cls._accounts.create_index("phone_number", unique=True, name="ux_accounts_phone")
                await cls._accounts.create_index("account_id", unique=True, sparse=True, name="ux_accounts_account_id")
                await cls._posts.create_index("post_id", unique=True, name="ux_posts_post_id")
                await cls._tasks.create_index("task_id", unique=True, name="ux_tasks_task_id")
                await cls._users.create_index("username", unique=True, name="ux_users_username")
                await cls._proxies.create_index("proxy_name", unique=True, name="ux_proxies_proxy_name")
                await cls._proxies.create_index([("active", ASCENDING), ("connected_accounts", ASCENDING)], name="ix_proxies_active_usage")
                
                # Reporter collection indexes
                await cls._runs.create_index([("run_id", ASCENDING)], unique=True, name="ux_runs_run_id")
                await cls._events.create_indexes([
                    IndexModel([("run_id", ASCENDING), ("ts", ASCENDING)], name="ix_events_run_ts"),
                    IndexModel([("task_id", ASCENDING)], name="ix_events_task_id"),
                    IndexModel([("level", ASCENDING)], name="ix_events_level"),
                    IndexModel([("code", ASCENDING)], name="ix_events_code")
                ])
            except PyMongoError as exc:
                logger.error("Failed to ensure MongoDB indexes: %s", exc)
                raise RuntimeError("Failed to create required MongoDB indexes") from exc

            cls._indexes_initialized = True

    @classmethod
    @ensure_async
    async def load_all_accounts(cls):
        await cls._ensure_ready()
        logger.info("Loading all accounts from MongoDB.")
        cursor = cls._accounts.find()
        accounts = []
        async for acc in cursor:
            acc.pop('_id', None)
            accounts.append(Account(acc))
        logger.debug(f"Loaded {len(accounts)} accounts from MongoDB.")
        return accounts

    @classmethod
    @ensure_async
    async def add_account(cls, account_data):
        await cls._ensure_ready()
        logger.info(f"Adding account to MongoDB: {account_data}")
        if hasattr(account_data, 'to_dict'):
            account_data = account_data.to_dict()
        # Ensure all AccountBase/AccountDict fields are present
        for field in [
            'phone_number', 'account_id', 'session_name', 'session_encrypted', 'twofa',
            'password_encrypted', 'notes', 'status', 'created_at', 'updated_at'
        ]:
            account_data.setdefault(field, None)
        phone_number = account_data.get('phone_number')
        existing_account = await cls.get_account(phone_number)
        if existing_account:
            logger.debug(f"Account with phone number {phone_number} exists in MongoDB. Updating.")
            return await cls.update_account(phone_number, account_data)
        account_data.pop('_id', None)
        await cls._accounts.insert_one(account_data)
        logger.debug(f"Account with phone number {phone_number} added to MongoDB.")
        return True

    @classmethod
    @ensure_async
    async def get_account(cls, phone_number):
        await cls._ensure_ready()
        logger.info(f"Getting account from MongoDB with phone number: {phone_number}")
        acc = await cls._accounts.find_one({"phone_number": phone_number})
        if acc and '_id' in acc:
            acc.pop('_id')
        if acc:
            # Ensure all AccountBase/AccountDict fields are present
            for field in [
                'phone_number', 'account_id', 'session_name', 'session_encrypted', 'twofa',
                'password_encrypted', 'notes', 'status', 'created_at', 'updated_at'
            ]:
                acc.setdefault(field, None)
            logger.debug(f"Account found in MongoDB: {acc.get('phone_number', '')}, {acc.get('status', '')}, {acc.get('updated_at', '')}")
        return Account(acc) if acc else None

    @classmethod
    @ensure_async
    async def update_account(cls, phone_number, update_data):
        await cls._ensure_ready()
        logger.info(f"Upserting account {phone_number} in MongoDB with data: {update_data}")
        if not isinstance(update_data, dict):
            raise ValueError(f"update_data must be a dict mapping field names to values, got {type(update_data)}: {update_data}")
        update_data.pop('_id', None)
        update_data['phone_number'] = phone_number
        result = await cls._accounts.update_one({"phone_number": phone_number}, {"$set": update_data}, upsert=True)
        logger.debug(f"Account {phone_number} upsert result: modified={result.modified_count}, upserted_id={result.upserted_id}")
        return result.modified_count > 0 or result.upserted_id is not None

    @classmethod
    @ensure_async
    async def delete_account(cls, phone_number):
        await cls._ensure_ready()
        logger.info(f"Deleting account from MongoDB with phone number: {phone_number}")
        if hasattr(phone_number, 'phone_number'):
            phone_number = phone_number.phone_number
        result = await cls._accounts.delete_one({"phone_number": phone_number})
        logger.debug(f"Account {phone_number} delete result: {result.deleted_count}")
        return result.deleted_count > 0

    # --- Post methods ---
    @classmethod
    @ensure_async
    async def load_all_posts(cls):
        await cls._ensure_ready()
        logger.info("Loading all posts from MongoDB.")
        cursor = cls._posts.find()
        posts = []
        async for post in cursor:
            post.pop('_id', None)
            post.pop('is_validated', None)
            posts.append(Post(**post))
        logger.debug(f"Loaded {len(posts)} posts from MongoDB.")
        return posts

    @classmethod
    @ensure_async
    async def add_post(cls, post):
        await cls._ensure_ready()
        logger.info(f"Adding post to MongoDB: {post}")
        if hasattr(post, 'to_dict'):
            post = post.to_dict()
        post_id = post.get('post_id')
        if not post_id:
            posts = await cls.load_all_posts()
            used_ids = set()
            for p in posts:
                try:
                    used_ids.add(int(p.post_id))
                except Exception:
                    continue
            post_id = 1
            while post_id in used_ids:
                post_id += 1
            post['post_id'] = post_id
        existing_post = await cls.get_post(post_id)
        if existing_post:
            logger.debug(f"Post with post_id {post_id} exists in MongoDB. Updating.")
            return await cls.update_post(post_id, post)
        post.pop('_id', None)
        await cls._posts.insert_one(post)
        logger.debug(f"Post with post_id {post_id} added to MongoDB.")
        return True

    @classmethod
    @ensure_async
    async def get_post(cls, post_id):
        await cls._ensure_ready()
        logger.info(f"Getting post from MongoDB with post_id: {post_id}")
        post = await cls._posts.find_one({"post_id": post_id})
        if post and '_id' in post:
            post.pop('_id')
        if post:
            logger.debug(f"Post found in MongoDB: {post.get('post_id', '')}, link: {post.get('message_link', '')}")
        return Post(**post) if post else None

    @classmethod
    @ensure_async
    async def update_post(cls, post_id, update_data):
        await cls._ensure_ready()
        logger.info(f"Upserting post {post_id} in MongoDB with data: {update_data}")
        if not isinstance(update_data, dict):
            raise ValueError(f"update_data must be a dict mapping field names to values, got {type(update_data)}: {update_data}")
        update_data.pop('_id', None)
        # Ensure post_id is set in the update data for upserts
        update_data['post_id'] = post_id
        result = await cls._posts.update_one({"post_id": post_id}, {"$set": update_data}, upsert=True)
        logger.debug(f"Post {post_id} upsert result: modified={result.modified_count}, upserted_id={result.upserted_id}")
        return result.modified_count > 0 or result.upserted_id is not None

    @classmethod
    @ensure_async
    async def delete_post(cls, post_id):
        await cls._ensure_ready()
        logger.info(f"Deleting post from MongoDB with post_id: {post_id}")
        if hasattr(post_id, 'post_id'):
            post_id = post_id.post_id
        result = await cls._posts.delete_one({"post_id": post_id})
        logger.debug(f"Post {post_id} delete result: {result.deleted_count}")
        return result.deleted_count > 0

    # --- Task methods ---
    @classmethod
    @ensure_async
    async def load_all_tasks(cls):
        await cls._ensure_ready()
        logger.info("Loading all tasks from MongoDB.")
        cursor = cls._tasks.find().sort('updated_at', -1)
        tasks = []
        async for task in cursor:
            task.pop('_id', None)
            # Parse timestamps if needed
            created_at = task.get('created_at')
            updated_at = task.get('updated_at')
            if isinstance(created_at, str):
                try:
                    created_at = Timestamp(created_at)
                except Exception:
                    pass
            if isinstance(updated_at, str):
                try:
                    updated_at = Timestamp(updated_at)
                except Exception:
                    pass
            tasks.append(Task(
                task_id=task.get('task_id'),
                name=task.get('name'),
                post_ids=task.get('post_ids'),
                accounts=task.get('accounts'),
                action=task.get('action'),
                description=task.get('description'),
                status=task.get('status'),
                created_at=created_at,
                updated_at=updated_at
            ))
        logger.debug(f"Loaded {len(tasks)} tasks from MongoDB.")
        return tasks

    @classmethod
    @ensure_async
    async def add_task(cls, task):
        await cls._ensure_ready()
        logger.info(f"Adding task to MongoDB: {task}")
        if hasattr(task, 'to_dict'):
            task = task.to_dict()
        task_id = task.get('task_id')
        if not task_id:
            tasks = await cls.load_all_tasks()
            used_ids = set()
            for t in tasks:
                try:
                    used_ids.add(int(t.task_id))
                except Exception:
                    continue
            task_id = 1
            while task_id in used_ids:
                task_id += 1
            task['task_id'] = task_id
        existing_task = await cls.get_task(task_id)
        if existing_task:
            logger.debug(f"Task with task_id {task_id} exists in MongoDB. Updating.")
            return await cls.update_task(task_id, task)
        await cls._tasks.insert_one(task)
        logger.debug(f"Task with task_id {task_id} added to MongoDB.")
        return True

    @classmethod
    @ensure_async
    async def get_task(cls, task_id):
        await cls._ensure_ready()
        logger.info(f"Getting task from MongoDB with task_id: {task_id}")
        task = await cls._tasks.find_one({"task_id": task_id})
        if task and '_id' in task:
            task.pop('_id')
        if task:
            logger.debug(f"Task found in MongoDB: {task.get('task_id', '')}, status: {task.get('status', '')}, updated_at: {task.get('updated_at', '')}, posts: {len(task.get('post_ids', []))}, accounts: {len(task.get('accounts', []))}")
        return Task(**task) if task else None

    @classmethod
    @ensure_async
    async def update_task(cls, task_id, update_data):
        await cls._ensure_ready()
        logger.info(f"Upserting task {task_id} in MongoDB with data: {update_data}")
        if not isinstance(update_data, dict):
            raise ValueError(f"update_data must be a dict mapping field names to values, got {type(update_data)}: {update_data}")
        update_data.pop('_id', None)
        # Ensure task_id is set in the update data for upserts
        update_data['task_id'] = task_id
        result = await cls._tasks.update_one({"task_id": task_id}, {"$set": update_data}, upsert=True)
        logger.debug(f"Task {task_id} upsert result: modified={result.modified_count}, upserted_id={result.upserted_id}")
        return result.modified_count > 0 or result.upserted_id is not None

    @classmethod
    @ensure_async
    async def delete_task(cls, task_id):
        await cls._ensure_ready()
        logger.info(f"Deleting task from MongoDB with task_id: {task_id}")
        if hasattr(task_id, 'task_id'):
            task_id = task_id.task_id
        result = await cls._tasks.delete_one({"task_id": task_id})
        logger.debug(f"Task {task_id} delete result: {result.deleted_count}")
        return result.deleted_count > 0

    # --- User methods ---
    @classmethod
    @ensure_async
    async def create_user(cls, user_data: dict):
        """
        Create a new user in the database.
        
        Args:
            user_data: Dictionary containing user fields (username, password_hash, role, is_verified)
            
        Returns:
            True if user created successfully, False otherwise
        """
        await cls._ensure_ready()
        logger.info(f"Creating user in MongoDB: {user_data.get('username')}")
        
        # Check if user already exists
        existing_user = await cls.get_user(user_data.get('username'))
        if existing_user:
            logger.warning(f"User {user_data.get('username')} already exists")
            return False
        
        user_data.pop('_id', None)
        await cls._users.insert_one(user_data)
        logger.debug(f"User {user_data.get('username')} created successfully")
        return True

    @classmethod
    @ensure_async
    async def get_user(cls, username: str):
        """
        Get a user by username.
        
        Args:
            username: Username to search for
            
        Returns:
            User dictionary if found, None otherwise
        """
        await cls._ensure_ready()
        logger.info(f"Getting user from MongoDB: {username}")
        user = await cls._users.find_one({"username": username.lower()})
        if user and '_id' in user:
            user.pop('_id')
        if user:
            logger.debug(f"User found: {username}")
        return user

    @classmethod
    @ensure_async
    async def verify_user_credentials(cls, username: str, password: str) -> tuple[bool, dict | None]:
        """
        Verify user credentials.
        
        Args:
            username: Username to verify
            password: Plain text password to verify
            
        Returns:
            Tuple of (success: bool, user_data: dict | None)
        """
        from encryption import verify_password
        
        await cls._ensure_ready()
        logger.info(f"Verifying credentials for user: {username}")
        
        user = await cls.get_user(username)
        if not user:
            logger.warning(f"User {username} not found")
            return False, None
        
        # Verify password
        password_hash = user.get('password_hash')
        if not password_hash:
            logger.warning(f"User {username} has no password hash")
            return False, None
        
        if not verify_password(password, password_hash):
            logger.warning(f"Invalid password for user {username}")
            return False, None
        
        logger.info(f"Credentials verified for user: {username}")
        return True, user

    @classmethod
    @ensure_async
    async def update_user(cls, username: str, update_data: dict):
        """
        Update user data.
        
        Args:
            username: Username to update
            update_data: Dictionary of fields to update
            
        Returns:
            True if updated successfully, False otherwise
        """
        await cls._ensure_ready()
        logger.info(f"Updating user {username} with data: {update_data}")
        
        update_data.pop('_id', None)
        update_data.pop('username', None)  # Don't allow username changes
        
        result = await cls._users.update_one(
            {"username": username.lower()},
            {"$set": update_data}
        )
        logger.debug(f"User {username} update result: modified={result.modified_count}")
        return result.modified_count > 0

    # --- Reporter/Events/Runs methods ---
    @classmethod
    @ensure_async
    async def create_run(cls, run_id: str, task_id: str, meta: dict = None):
        """
        Create a new run record.
        
        Args:
            run_id: Unique identifier for the run
            task_id: Task identifier this run belongs to
            meta: Optional metadata dictionary
            
        Returns:
            run_id if successful
        """
        from datetime import datetime, timezone
        await cls._ensure_ready()
        logger.info(f"Creating run {run_id} for task {task_id}")
        
        doc = {
            "run_id": run_id,
            "task_id": task_id,
            "started_at": datetime.now(timezone.utc),
            "finished_at": None,
            "status": "running",
            "meta": meta or {}
        }
        await cls._runs.insert_one(doc)
        logger.debug(f"Run {run_id} created successfully")
        return run_id

    @classmethod
    @ensure_async
    async def end_run(cls, run_id: str, status: str = "success", meta_patch: dict = None):
        """
        Mark a run as completed.
        
        Args:
            run_id: Run identifier to update
            status: Final status (success, failed, etc.)
            meta_patch: Optional metadata updates
            
        Returns:
            True if updated successfully
        """
        from datetime import datetime, timezone
        await cls._ensure_ready()
        logger.info(f"Ending run {run_id} with status {status}")
        
        update = {"$set": {"finished_at": datetime.now(timezone.utc), "status": status}}
        if meta_patch:
            update["$set"]["meta"] = meta_patch
        
        result = await cls._runs.update_one({"run_id": run_id}, update)
        logger.debug(f"Run {run_id} end result: modified={result.modified_count}")
        return result.modified_count > 0

    @classmethod
    @ensure_async
    async def create_event(cls, event_data: dict):
        """
        Create a single event record.
        
        Args:
            event_data: Dictionary containing event fields (run_id, task_id, ts, level, code, message, payload)
            
        Returns:
            True if created successfully
        """
        await cls._ensure_ready()
        logger.debug(f"Creating event for run {event_data.get('run_id')}")
        
        event_data.pop('_id', None)
        await cls._events.insert_one(event_data)
        return True

    @classmethod
    @ensure_async
    async def create_events_batch(cls, events: list):
        """
        Create multiple event records in batch (optimized for performance).
        
        Args:
            events: List of event dictionaries
            
        Returns:
            Number of events inserted
        """
        await cls._ensure_ready()
        if not events:
            return 0
            
        logger.debug(f"Creating batch of {len(events)} events")
        
        # Remove _id fields if present
        for event in events:
            event.pop('_id', None)
        
        try:
            result = await cls._events.insert_many(events, ordered=False)
            return len(result.inserted_ids)
        except PyMongoError as exc:
            logger.warning(f"Batch insert partially failed: {exc}")
            # Fallback to individual inserts
            inserted = 0
            for event in events:
                try:
                    await cls._events.insert_one(event)
                    inserted += 1
                except PyMongoError as e:
                    logger.error(f"Failed to insert individual event: {e}")
            return inserted

    @classmethod
    @ensure_async
    async def get_runs_by_task(cls, task_id: str):
        """
        Get all runs for a given task, ordered by started_at descending.
        
        Args:
            task_id: Task identifier
            
        Returns:
            List of run documents
        """
        await cls._ensure_ready()
        logger.info(f"Getting runs for task {task_id}")
        
        cursor = cls._runs.find({'task_id': task_id}).sort('started_at', -1)
        runs = await cursor.to_list(length=None)
        
        # Remove _id from results
        for run in runs:
            run.pop('_id', None)
        
        logger.debug(f"Found {len(runs)} runs for task {task_id}")
        return runs

    @classmethod
    @ensure_async
    async def get_run(cls, run_id: str):
        """
        Get a single run by run_id.
        
        Args:
            run_id: Run identifier
            
        Returns:
            Run document or None
        """
        await cls._ensure_ready()
        logger.info(f"Getting run {run_id}")
        
        run = await cls._runs.find_one({"run_id": run_id})
        if run and '_id' in run:
            run.pop('_id')
        
        return run

    @classmethod
    @ensure_async
    async def get_events_by_run(cls, run_id: str):
        """
        Get all events for a given run, ordered by timestamp.
        
        Args:
            run_id: Run identifier
            
        Returns:
            List of event documents
        """
        await cls._ensure_ready()
        logger.info(f"Getting events for run {run_id}")
        
        cursor = cls._events.find({'run_id': run_id}).sort('ts', 1)
        events = await cursor.to_list(length=None)
        
        logger.debug(f"Found {len(events)} events for run {run_id}")
        return events

    @classmethod
    @ensure_async
    async def delete_run(cls, run_id: str):
        """
        Delete a run and all its associated events.
        
        Args:
            run_id: Run identifier
            
        Returns:
            Dictionary with counts of deleted runs and events
        """
        await cls._ensure_ready()
        logger.info(f"Deleting run {run_id} and associated events")
        
        run_result = await cls._runs.delete_one({'run_id': run_id})
        event_result = await cls._events.delete_many({'run_id': run_id})
        
        result = {
            'runs_deleted': run_result.deleted_count,
            'events_deleted': event_result.deleted_count
        }
        logger.debug(f"Deleted run {run_id}: {result}")
        return result

    @classmethod
    @ensure_async
    async def clear_runs_by_task(cls, task_id: str):
        """
        Delete all runs for a task and all associated events.
        
        Args:
            task_id: Task identifier
            
        Returns:
            Dictionary with counts of deleted runs and events
        """
        await cls._ensure_ready()
        logger.info(f"Clearing all runs for task {task_id}")
        
        # Get all run_ids first
        runs_cursor = cls._runs.find({'task_id': task_id}, {'run_id': 1})
        runs = await runs_cursor.to_list(length=None)
        run_ids = [run['run_id'] for run in runs]
        
        # Delete runs
        runs_result = await cls._runs.delete_many({'task_id': task_id})
        
        # Delete linked events
        events_deleted = 0
        if run_ids:
            events_result = await cls._events.delete_many({'run_id': {'$in': run_ids}})
            events_deleted = events_result.deleted_count
        
        result = {
            'runs_deleted': runs_result.deleted_count,
            'events_deleted': events_deleted
        }
        logger.debug(f"Cleared runs for task {task_id}: {result}")
        return result

    @classmethod
    @ensure_async
    async def get_all_task_summaries(cls):
        """
        Get summary of all tasks with run counts.
        
        Returns:
            List of dicts with task_id and run_count
        """
        await cls._ensure_ready()
        logger.info("Getting task summaries")
        
        pipeline = [
            {"$group": {"_id": "$task_id", "run_count": {"$sum": 1}}},
            {"$project": {"task_id": "$_id", "run_count": 1, "_id": 0}}
        ]
        cursor = cls._runs.aggregate(pipeline)
        results = await cursor.to_list(length=None)
        
        logger.debug(f"Found {len(results)} tasks with runs")
        return results

    @classmethod
    @ensure_async
    async def get_event_counts_for_runs(cls, run_ids: list):
        """
        Get event counts for multiple runs.
        
        Args:
            run_ids: List of run identifiers
            
        Returns:
            Dictionary mapping run_id to event count
        """
        await cls._ensure_ready()
        logger.debug(f"Getting event counts for {len(run_ids)} runs")
        
        pipeline = [
            {"$match": {"run_id": {"$in": run_ids}}},
            {"$group": {"_id": "$run_id", "event_count": {"$sum": 1}}}
        ]
        cursor = cls._events.aggregate(pipeline)
        results = await cursor.to_list(length=None)
        
        return {r["_id"]: r["event_count"] for r in results}

    @classmethod
    @ensure_async
    async def get_all_runs(cls):
        """
        Get all runs from the database.
        
        Returns:
            List of all run documents
        """
        await cls._ensure_ready()
        logger.info("Getting all runs")
        
        cursor = cls._runs.find()
        runs = await cursor.to_list(length=None)
        
        # Remove _id from results
        for run in runs:
            run.pop('_id', None)
        
        logger.debug(f"Found {len(runs)} total runs")
        return runs

    @classmethod
    @ensure_async
    async def get_all_events(cls):
        """
        Get all events from the database.
        
        Returns:
            List of all event documents
        """
        await cls._ensure_ready()
        logger.info("Getting all events")
        
        cursor = cls._events.find()
        events = await cursor.to_list(length=None)
        
        logger.debug(f"Found {len(events)} total events")
        return events

    @classmethod
    @ensure_async
    async def get_event_by_id(cls, event_id):
        """
        Get a single event by its MongoDB ObjectId.
        
        Args:
            event_id: MongoDB ObjectId (as string or ObjectId)
            
        Returns:
            Event document or None
        """
        from bson import ObjectId
        await cls._ensure_ready()
        logger.info(f"Getting event {event_id}")
        
        event = await cls._events.find_one({'_id': ObjectId(event_id)})
        if event and '_id' in event:
            event.pop('_id')
        
        return event

    @classmethod
    @ensure_async
    async def delete_event_by_id(cls, event_id):
        """
        Delete a single event by its MongoDB ObjectId.
        
        Args:
            event_id: MongoDB ObjectId (as string or ObjectId)
            
        Returns:
            Number of deleted events (0 or 1)
        """
        from bson import ObjectId
        await cls._ensure_ready()
        logger.info(f"Deleting event {event_id}")
        
        result = await cls._events.delete_one({'_id': ObjectId(event_id)})
        logger.debug(f"Event {event_id} delete result: {result.deleted_count}")
        return result.deleted_count

    @classmethod
    @ensure_async
    async def count_admin_users(cls):
        """
        Count verified admin users in the database.
        
        Returns:
            Number of verified admin users
        """
        await cls._ensure_ready()
        logger.info("Counting admin users")
        
        count = await cls._users.count_documents({"role": "admin", "is_verified": True})
        logger.debug(f"Found {count} verified admin users")
        return count

    # --- Proxy methods ---
    @classmethod
    @ensure_async
    async def add_proxy(cls, proxy_data: dict):
        """
        Add a new proxy configuration to the database.

        Args:
            proxy_data: Dictionary describing the proxy to add.

        Required fields:
            - proxy_name (str): unique, non-empty identifier for the proxy (used as the primary key).
            - host (str): proxy hostname or IP address (used as 'addr' in internal proxy dict). Practically required.
            - port (int): proxy port. Practically required.

        Optional fields (and defaults expected by _build_proxy_dict):
            - type (str): one of "socks5", "socks4", "http" (case-insensitive). Defaults to "socks5".
            - rdns (bool): whether to resolve DNS remotely. Defaults to True.
            - username (str): authentication username.
            - password (str): authentication password.
            - connected_accounts (int): connection count (defaults to 0).
            - active (bool): whether proxy is active (defaults to True).
            - Any other metadata keys (tags, notes, ssl, timeout, retries, created_at, updated_at, etc.) may be present.

        Returns:
            True if the proxy was added successfully, False on missing required fields, duplicate proxy_name,
            or insertion failure.
        """
        await cls._ensure_ready()
        logger.info(f"Adding proxy to MongoDB: {proxy_data.get('proxy_name')}")
        
        proxy_name = proxy_data.get('proxy_name')
        if not proxy_name:
            logger.error("Proxy name is required")
            return False
        
        # Check if proxy already exists
        existing_proxy = await cls.get_proxy(proxy_name)
        if existing_proxy:
            logger.warning(f"Proxy {proxy_name} already exists")
            return False
        
        # Encrypt password if provided
        if proxy_data.get('password'):
            from encryption import encrypt_secret, PURPOSE_PROXY_PASSWORD
            logger.debug(f"Encrypting password for proxy {proxy_name}")
            proxy_data['password_encrypted'] = encrypt_secret(proxy_data['password'], PURPOSE_PROXY_PASSWORD)
            proxy_data.pop('password')  # Remove plain password
        
        # Set default values
        proxy_data.setdefault('connected_accounts', 0)
        proxy_data.setdefault('active', True)
        proxy_data.setdefault('rdns', True)
        
        proxy_data.pop('_id', None)
        await cls._proxies.insert_one(proxy_data)
        logger.debug(f"Proxy {proxy_name} added to MongoDB")
        return True

    @classmethod
    @ensure_async
    async def get_proxy(cls, proxy_name: str):
        """
        Get a proxy by name and decrypt password if present.
        
        Args:
            proxy_name: Proxy name to search for
            
        Returns:
            Proxy dictionary if found (with decrypted password), None otherwise
        """
        from encryption import decrypt_secret, PURPOSE_PROXY_PASSWORD
        
        await cls._ensure_ready()
        logger.info(f"Getting proxy from MongoDB: {proxy_name}")
        proxy = await cls._proxies.find_one({"proxy_name": proxy_name})
        if proxy and '_id' in proxy:
            proxy.pop('_id')
        
        # Decrypt password if present
        if proxy and proxy.get('password_encrypted'):
            try:
                proxy['password'] = decrypt_secret(proxy['password_encrypted'], PURPOSE_PROXY_PASSWORD)
                logger.debug(f"Decrypted password for proxy {proxy_name}")
            except Exception as e:
                logger.error(f"Failed to decrypt password for proxy {proxy_name}: {e}")
                proxy['password'] = None
        
        return proxy

    @classmethod
    @ensure_async
    async def get_all_proxies(cls):
        """
        Get all proxies from the database with decrypted passwords.
        
        Returns:
            List of proxy dictionaries (with decrypted passwords)
        """
        from encryption import decrypt_secret, PURPOSE_PROXY_PASSWORD
        
        await cls._ensure_ready()
        logger.info("Loading all proxies from MongoDB")
        cursor = cls._proxies.find()
        proxies = []
        async for proxy in cursor:
            proxy.pop('_id', None)
            
            # Decrypt password if present
            if proxy.get('password_encrypted'):
                try:
                    proxy['password'] = decrypt_secret(proxy['password_encrypted'], PURPOSE_PROXY_PASSWORD)
                except Exception as e:
                    logger.error(f"Failed to decrypt password for proxy {proxy.get('proxy_name')}: {e}")
                    proxy['password'] = None
            
            proxies.append(proxy)
        logger.debug(f"Loaded {len(proxies)} proxies from MongoDB")
        return proxies

    @classmethod
    @ensure_async
    async def get_active_proxies(cls):
        """
        Get all active proxies from the database with decrypted passwords.
        
        Returns:
            List of active proxy dictionaries (with decrypted passwords)
        """
        from encryption import decrypt_secret, PURPOSE_PROXY_PASSWORD
        
        await cls._ensure_ready()
        logger.info("Loading active proxies from MongoDB")
        cursor = cls._proxies.find({"active": True})
        proxies = []
        async for proxy in cursor:
            proxy.pop('_id', None)
            
            # Decrypt password if present
            if proxy.get('password_encrypted'):
                try:
                    proxy['password'] = decrypt_secret(proxy['password_encrypted'], PURPOSE_PROXY_PASSWORD)
                except Exception as e:
                    logger.error(f"Failed to decrypt password for proxy {proxy.get('proxy_name')}: {e}")
                    proxy['password'] = None
            
            proxies.append(proxy)
        logger.debug(f"Loaded {len(proxies)} active proxies from MongoDB")
        return proxies

    @classmethod
    @ensure_async
    async def get_least_used_proxy(cls):
        """
        Get the active proxy with the least number of connected accounts (with decrypted password).
        
        Returns:
            Proxy dictionary if found (with decrypted password), None otherwise
        """
        from encryption import decrypt_secret, PURPOSE_PROXY_PASSWORD
        
        await cls._ensure_ready()
        logger.info("Getting least used active proxy from MongoDB")
        
        # Find active proxies sorted by connected_accounts ascending
        cursor = cls._proxies.find({"active": True}).sort("connected_accounts", 1).limit(1)
        proxy = await cursor.to_list(length=1)
        
        if proxy:
            proxy = proxy[0]
            proxy.pop('_id', None)
            
            # Decrypt password if present
            if proxy.get('password_encrypted'):
                try:
                    proxy['password'] = decrypt_secret(proxy['password_encrypted'], PURPOSE_PROXY_PASSWORD)
                except Exception as e:
                    logger.error(f"Failed to decrypt password for proxy {proxy.get('proxy_name')}: {e}")
                    proxy['password'] = None
            
            logger.debug(f"Found least used proxy: {proxy.get('proxy_name')} with {proxy.get('connected_accounts', 0)} connections")
            return proxy
        
        logger.warning("No active proxies found")
        return None

    @classmethod
    @ensure_async
    async def update_proxy(cls, proxy_name: str, update_data: dict):
        """
        Update proxy configuration. Encrypts password if provided.
        
        Args:
            proxy_name: Proxy name to update
            update_data: Dictionary of fields to update (password will be encrypted)
            
        Returns:
            True if updated successfully, False otherwise
        """
        await cls._ensure_ready()
        logger.info(f"Updating proxy {proxy_name} with data: {update_data}")
        
        update_data.pop('_id', None)
        update_data.pop('proxy_name', None)  # Don't allow proxy_name changes
        
        # Encrypt password if provided
        if update_data.get('password'):
            from encryption import encrypt_secret, PURPOSE_PROXY_PASSWORD
            logger.debug(f"Encrypting new password for proxy {proxy_name}")
            update_data['password_encrypted'] = encrypt_secret(update_data['password'], PURPOSE_PROXY_PASSWORD)
            update_data.pop('password')  # Remove plain password
        
        result = await cls._proxies.update_one(
            {"proxy_name": proxy_name},
            {"$set": update_data}
        )
        logger.debug(f"Proxy {proxy_name} update result: modified={result.modified_count}")
        return result.modified_count > 0

    @classmethod
    @ensure_async
    async def increment_proxy_usage(cls, proxy_name: str):
        """
        Increment the connected_accounts counter for a proxy.
        
        Args:
            proxy_name: Proxy name to increment
            
        Returns:
            True if incremented successfully, False otherwise
        """
        await cls._ensure_ready()
        logger.debug(f"Incrementing usage for proxy {proxy_name}")
        
        result = await cls._proxies.update_one(
            {"proxy_name": proxy_name},
            {"$inc": {"connected_accounts": 1}}
        )
        return result.modified_count > 0

    @classmethod
    @ensure_async
    async def decrement_proxy_usage(cls, proxy_name: str):
        """
        Decrement the connected_accounts counter for a proxy.
        
        Args:
            proxy_name: Proxy name to decrement
            
        Returns:
            True if decremented successfully, False otherwise
        """
        await cls._ensure_ready()
        logger.debug(f"Decrementing usage for proxy {proxy_name}")
        
        result = await cls._proxies.update_one(
            {"proxy_name": proxy_name},
            {"$inc": {"connected_accounts": -1}}
        )
        return result.modified_count > 0

    @classmethod
    @ensure_async
    async def delete_proxy(cls, proxy_name: str):
        """
        Delete a proxy from the database.
        
        Args:
            proxy_name: Proxy name to delete
            
        Returns:
            True if deleted successfully, False otherwise
        """
        await cls._ensure_ready()
        logger.info(f"Deleting proxy from MongoDB: {proxy_name}")
        
        result = await cls._proxies.delete_one({"proxy_name": proxy_name})
        logger.debug(f"Proxy {proxy_name} delete result: {result.deleted_count}")
        return result.deleted_count > 0

    @classmethod
    @ensure_async
    async def set_proxy_error(cls, proxy_name: str, error_message: str):
        """
        Set error status on a proxy when connection fails.
        
        Args:
            proxy_name: Proxy name to update
            error_message: Error message to store
            
        Returns:
            True if updated successfully, False otherwise
        """
        from datetime import datetime, timezone
        
        await cls._ensure_ready()
        logger.warning(f"Setting error status for proxy {proxy_name}: {error_message}")
        
        result = await cls._proxies.update_one(
            {"proxy_name": proxy_name},
            {"$set": {
                "last_error": error_message,
                "last_error_time": datetime.now(timezone.utc)
            }}
        )
        return result.modified_count > 0

    @classmethod
    @ensure_async
    async def clear_proxy_error(cls, proxy_name: str):
        """
        Clear error status on a proxy after successful connection.
        
        Args:
            proxy_name: Proxy name to update
            
        Returns:
            True if updated successfully, False otherwise
        """
        await cls._ensure_ready()
        logger.debug(f"Clearing error status for proxy {proxy_name}")
        
        result = await cls._proxies.update_one(
            {"proxy_name": proxy_name},
            {"$unset": {
                "last_error": "",
                "last_error_time": ""
            }}
        )
        return result.modified_count > 0

    # --- Reaction Palette methods ---
    @classmethod
    @ensure_async
    async def add_palette(cls, palette_data: dict):
        """
        Add a new reaction palette to the database.
        
        Args:
            palette_data: Dictionary containing palette fields (palette_name, emojis, ordered, description)
            
        Returns:
            True if palette added successfully, False if already exists
        """
        await cls._ensure_ready()
        logger.info(f"Adding palette to MongoDB: {palette_data.get('palette_name')}")
        
        palette_name = palette_data.get('palette_name')
        if not palette_name:
            logger.error("Palette name is required")
            return False
        
        # Check if palette already exists
        existing_palette = await cls.get_palette(palette_name)
        if existing_palette:
            logger.warning(f"Palette {palette_name} already exists")
            return False
        
        palette_data.pop('_id', None)
        
        # Ensure timestamps
        from datetime import datetime, timezone
        if 'created_at' not in palette_data:
            palette_data['created_at'] = datetime.now(timezone.utc)
        if 'updated_at' not in palette_data:
            palette_data['updated_at'] = datetime.now(timezone.utc)
        
        await cls._palettes.insert_one(palette_data)
        logger.debug(f"Palette {palette_name} added to MongoDB")
        return True

    @classmethod
    @ensure_async
    async def get_palette(cls, palette_name: str):
        """
        Get a reaction palette by name.
        
        Args:
            palette_name: Palette name to search for
            
        Returns:
            Palette dictionary if found, None otherwise
        """
        await cls._ensure_ready()
        logger.info(f"Getting palette from MongoDB: {palette_name}")
        
        palette = await cls._palettes.find_one({"palette_name": palette_name.lower()})
        if palette and '_id' in palette:
            del palette['_id']
        
        return palette

    @classmethod
    @ensure_async
    async def get_all_palettes(cls):
        """
        Get all reaction palettes.
        
        Returns:
            List of palette dictionaries
        """
        await cls._ensure_ready()
        logger.info("Getting all palettes from MongoDB")
        
        cursor = cls._palettes.find()
        palettes = []
        async for palette in cursor:
            if '_id' in palette:
                del palette['_id']
            palettes.append(palette)
        
        logger.debug(f"Found {len(palettes)} palettes")
        return palettes

    @classmethod
    @ensure_async
    async def update_palette(cls, palette_name: str, update_data: dict):
        """
        Update a reaction palette.
        
        Args:
            palette_name: Palette name to update
            update_data: Dictionary of fields to update
            
        Returns:
            True if updated successfully, False otherwise
        """
        await cls._ensure_ready()
        logger.info(f"Updating palette {palette_name} with data: {update_data}")
        
        update_data.pop('_id', None)
        update_data.pop('palette_name', None)  # Don't allow name changes
        
        # Update timestamp
        from datetime import datetime, timezone
        update_data['updated_at'] = datetime.now(timezone.utc)
        
        result = await cls._palettes.update_one(
            {"palette_name": palette_name.lower()},
            {"$set": update_data}
        )
        
        logger.debug(f"Palette {palette_name} update result: modified={result.modified_count}")
        return result.modified_count > 0

    @classmethod
    @ensure_async
    async def delete_palette(cls, palette_name: str):
        """
        Delete a reaction palette.
        
        Args:
            palette_name: Palette name to delete
            
        Returns:
            True if deleted successfully, False otherwise
        """
        await cls._ensure_ready()
        logger.info(f"Deleting palette from MongoDB: {palette_name}")
        
        result = await cls._palettes.delete_one({"palette_name": palette_name.lower()})
        logger.debug(f"Palette {palette_name} delete result: {result.deleted_count}")
        return result.deleted_count > 0

    @classmethod
    @ensure_async
    async def ensure_default_palettes(cls, palettes_data: dict = None):
        """
        Ensure default reaction palettes exist in the database.
        Creates palettes from provided data if they don't exist.
        
        Args:
            palettes_data: Optional dict mapping palette names to emoji lists
                          Format: {'positive': ['👍', '❤️'], 'negative': ['👎', '💩']}
                          If None, no palettes will be created.
        
        Returns:
            Number of default palettes created
        """
        await cls._ensure_ready()
        logger.info("Ensuring default reaction palettes exist")
        
        from datetime import datetime, timezone
        created_count = 0
        
        if not palettes_data:
            logger.warning("No palette data provided to ensure_default_palettes")
            return 0
        
        for palette_name, emojis in palettes_data.items():
            existing = await cls.get_palette(palette_name)
            if not existing:
                palette_data = {
                    'palette_name': palette_name.lower(),
                    'emojis': emojis,
                    'ordered': False,  # Default to random selection
                    'description': f"Default {palette_name} reactions palette",
                    'created_at': datetime.now(timezone.utc),
                    'updated_at': datetime.now(timezone.utc)
                }
                await cls.add_palette(palette_data)
                logger.info(f"Created default palette: {palette_name}")
                created_count += 1
        
        logger.debug(f"Ensured {created_count} default palettes")
        return created_count


def get_db() -> MongoStorage:
    return MongoStorage()