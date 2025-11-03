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
                await cls._accounts.create_index("phone_number", unique=True, name="ux_accounts_phone")
                await cls._accounts.create_index("account_id", unique=True, sparse=True, name="ux_accounts_account_id")
                await cls._posts.create_index("post_id", unique=True, name="ux_posts_post_id")
                await cls._tasks.create_index("task_id", unique=True, name="ux_tasks_task_id")
                await cls._users.create_index("username", unique=True, name="ux_users_username")
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
            logger.debug(f"Account found in MongoDB: {acc}")
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
            logger.debug(f"Post found in MongoDB: {post}")
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
            logger.debug(f"Task found in MongoDB: {task}")
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


def get_db() -> MongoStorage:
    return MongoStorage()