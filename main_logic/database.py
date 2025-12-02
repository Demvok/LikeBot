"""database.py
Async MongoDB storage for LikeBot.

Provides MongoStorage (get_db()) using motor.AsyncIOMotorClient:
- Lazy client/collection init, idempotent index creation (asyncio.Lock)
- CRUD for accounts, posts, tasks, users, runs, events, channels, proxies, and palettes
- Accepts domain objects or dicts, strips MongoDB _id on return
- Centralized database logic for all collections including reporter events/runs

Collections:
- accounts: Telegram account information
- posts: Post/message data
- tasks: Task definitions and status
- users: API user authentication
- runs: Task execution runs (reporter)
- events: Task execution events (reporter)
- channels: Telegram channel metadata
- proxies: Proxy configuration
- palettes: Reaction emoji palettes
- counters: Atomic ID generation

Environment:
- db_url (required), db_name (default "LikeBot"), db_timeout_ms (default 5000)
"""
import os, asyncio
from typing import Optional
from pandas import Timestamp
from datetime import datetime, timezone
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError
from utils.logger import setup_logger, load_config
from main_logic.agent import Account
from main_logic.post import Post
from main_logic.task import Task
from main_logic.channel import Channel
from main_logic.channel import normalize_chat_id

config = load_config()
logger = setup_logger("DB", "main.log")

load_dotenv()
db_url = os.getenv('db_url')
db_name = os.getenv('db_name', 'LikeBot')
mongo_timeout_ms = int(os.getenv('db_timeout_ms', '5000'))


def ensure_async(fn):
    """
    Decorator to wrap a sync function to run in asyncio.to_thread().
    
    Usage:
        @ensure_async
        def blocking_function(x, y):
            return x + y
            
        result = await blocking_function(1, 2)
    """
    async def wrapper(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
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
    _counters = None
    _channels = None
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
        cls._counters = cls._db["counters"]
        cls._channels = cls._db["channels"]
        
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
                from pymongo.errors import DuplicateKeyError, OperationFailure
                
                # Helper to create index with duplicate detection and name conflict handling
                async def create_unique_index_safe(collection, field_spec, index_name, sparse=False):
                    """Create unique index, handling duplicates and name conflicts."""
                    try:
                        if isinstance(field_spec, str):
                            await collection.create_index(field_spec, unique=True, sparse=sparse, name=index_name)
                        else:
                            await collection.create_index(field_spec, unique=True, sparse=sparse, name=index_name)
                    except (DuplicateKeyError, OperationFailure) as exc:
                        # Check if index already exists with different name
                        if 'IndexOptionsConflict' in str(exc) or 'already exists with a different name' in str(exc):
                            logger.warning(f"Index conflict for {index_name}: {exc}")
                            # Extract the existing index name from error message
                            existing_name = None
                            if 'different name:' in str(exc):
                                parts = str(exc).split('different name:')
                                if len(parts) > 1:
                                    existing_name = parts[1].split(',')[0].strip()
                            
                            if existing_name:
                                logger.info(f"Dropping existing index '{existing_name}' to recreate as '{index_name}'")
                                try:
                                    await collection.drop_index(existing_name)
                                except Exception as drop_exc:
                                    logger.warning(f"Failed to drop index {existing_name}: {drop_exc}")
                            
                            # Retry index creation
                            if isinstance(field_spec, str):
                                await collection.create_index(field_spec, unique=True, sparse=sparse, name=index_name)
                            else:
                                await collection.create_index(field_spec, unique=True, sparse=sparse, name=index_name)
                            logger.info(f"Successfully recreated index {index_name}")
                        # Check if error is due to duplicate keys
                        elif 'E11000' in str(exc) or 'duplicate key' in str(exc).lower():
                            logger.warning(f"Duplicate keys found for index {index_name}, attempting to resolve...")
                            
                            # Extract field name from field_spec
                            if isinstance(field_spec, str):
                                field_name = field_spec
                            elif isinstance(field_spec, list) and len(field_spec) > 0:
                                field_name = field_spec[0][0]  # First field in compound index
                            else:
                                raise
                            
                            # Find and remove duplicates, keeping the most recent
                            pipeline = [
                                {"$group": {
                                    "_id": f"${field_name}",
                                    "ids": {"$push": "$_id"},
                                    "count": {"$sum": 1}
                                }},
                                {"$match": {"count": {"$gt": 1}}}
                            ]
                            
                            duplicates = await collection.aggregate(pipeline).to_list(length=None)
                            
                            if duplicates:
                                logger.warning(f"Found {len(duplicates)} duplicate values for {field_name}, removing older entries...")
                                for dup in duplicates:
                                    # Keep first ID, delete the rest
                                    ids_to_delete = dup['ids'][1:]
                                    if ids_to_delete:
                                        result = await collection.delete_many({"_id": {"$in": ids_to_delete}})
                                        logger.info(f"Removed {result.deleted_count} duplicate entries for {field_name}={dup['_id']}")
                                
                                # Retry index creation
                                if isinstance(field_spec, str):
                                    await collection.create_index(field_spec, unique=True, sparse=sparse, name=index_name)
                                else:
                                    await collection.create_index(field_spec, unique=True, sparse=sparse, name=index_name)
                                logger.info(f"Successfully created index {index_name} after removing duplicates")
                            else:
                                # No duplicates found, might be a different issue
                                raise
                        else:
                            raise
                
                # Create unique indexes with duplicate handling
                await create_unique_index_safe(cls._accounts, "phone_number", "ux_accounts_phone")
                await create_unique_index_safe(cls._accounts, "account_id", "ux_accounts_account_id", sparse=True)
                await create_unique_index_safe(cls._posts, "post_id", "ux_posts_post_id")
                await create_unique_index_safe(cls._tasks, "task_id", "ux_tasks_task_id")
                await create_unique_index_safe(cls._users, "username", "ux_users_username")
                await create_unique_index_safe(cls._proxies, "proxy_name", "ux_proxies_proxy_name")
                await create_unique_index_safe(cls._channels, "chat_id", "ux_channels_chat_id")

                # Ensure counters collection has an index on _id for fast lookups
                try:
                    await cls._counters.create_index([("_id", 1)], unique=True)
                except Exception:
                    # Non-critical if index creation fails here; available migrations can handle it
                    logger.debug("Counters index creation skipped or failed during startup")
                
                # Non-unique indexes (can be created normally)
                await cls._proxies.create_index([("active", ASCENDING), ("connected_accounts", ASCENDING)], name="ix_proxies_active_usage")
                await cls._channels.create_index([("tags", ASCENDING)], name="ix_channels_tags")
                await cls._channels.create_index([("channel_name", ASCENDING)], name="ix_channels_name")
                await cls._channels.create_index([("url_aliases", ASCENDING)], name="ix_channels_url_aliases")
                
                # Reporter collection indexes
                await create_unique_index_safe(cls._runs, [("run_id", ASCENDING)], "ux_runs_run_id")
                
                # Events indexes - handle conflicts individually
                event_indexes = [
                    IndexModel([("run_id", ASCENDING), ("ts", ASCENDING)], name="ix_events_run_ts"),
                    IndexModel([("task_id", ASCENDING)], name="ix_events_task_id"),
                    IndexModel([("level", ASCENDING)], name="ix_events_level"),
                    IndexModel([("code", ASCENDING)], name="ix_events_code")
                ]
                
                for index_model in event_indexes:
                    try:
                        await cls._events.create_indexes([index_model])
                    except OperationFailure as exc:
                        if 'IndexOptionsConflict' in str(exc) or 'already exists with a different name' in str(exc):
                            # Extract existing index name and drop it
                            existing_name = None
                            if 'different name:' in str(exc):
                                parts = str(exc).split('different name:')
                                if len(parts) > 1:
                                    existing_name = parts[1].split(',')[0].strip()
                            
                            if existing_name:
                                logger.info(f"Dropping existing events index '{existing_name}' to recreate as '{index_model.document['name']}'")
                                try:
                                    await cls._events.drop_index(existing_name)
                                    await cls._events.create_indexes([index_model])
                                    logger.info(f"Successfully recreated events index {index_model.document['name']}")
                                except Exception as retry_exc:
                                    logger.error(f"Failed to recreate events index {index_model.document['name']}: {retry_exc}")
                                    raise
                        else:
                            raise
            except PyMongoError as exc:
                logger.error("Failed to ensure MongoDB indexes: %s", exc)
                raise RuntimeError("Failed to create required MongoDB indexes") from exc

            cls._indexes_initialized = True

# --- Account methods ---
    @classmethod
    async def load_all_accounts(cls):
        await cls._ensure_ready()
        logger.info("Loading all accounts from MongoDB.")
        cursor = cls._accounts.find()
        accounts = []
        skipped_count = 0
        async for acc in cursor:
            acc.pop('_id', None)
            # Defensive: wrap Account instantiation to skip malformed records
            try:
                accounts.append(Account(acc))
            except Exception as e:
                skipped_count += 1
                logger.error(
                    f"Skipping malformed account record (phone_number={acc.get('phone_number')}, account_id={acc.get('account_id')}): {e}. "
                    f"Record data: {acc}"
                )
                # Continue loading other accounts instead of crashing
                continue
        
        if skipped_count > 0:
            logger.warning(f"Loaded {len(accounts)} accounts from MongoDB, skipped {skipped_count} malformed records.")
        else:
            logger.debug(f"Loaded {len(accounts)} accounts from MongoDB.")
        return accounts

    @classmethod
    async def add_account(cls, account_data):
        await cls._ensure_ready()

        if hasattr(account_data, 'to_dict'):
            account_data = account_data.to_dict()
        logger.info("Adding account to MongoDB: phone=%s, account_id=%s", account_data.get('phone_number'), account_data.get('account_id'))
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
    async def update_account(cls, phone_number, update_data):
        await cls._ensure_ready()
        logger.info(f"Upserting account {phone_number} in MongoDB with data: {update_data.keys()}")
        if not isinstance(update_data, dict):
            raise ValueError(f"update_data must be a dict mapping field names to values, got {type(update_data)}: {update_data}")
        update_data.pop('_id', None)
        update_data['phone_number'] = phone_number
        result = await cls._accounts.update_one({"phone_number": phone_number}, {"$set": update_data}, upsert=True)
        logger.debug(f"Account {phone_number} upsert result: modified={result.modified_count}, upserted_id={result.upserted_id}")
        return result.modified_count > 0 or result.upserted_id is not None

    @classmethod
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
    async def load_all_posts(cls):
        await cls._ensure_ready()
        logger.info("Loading all posts from MongoDB.")
        cursor = cls._posts.find()
        posts = []
        skipped_count = 0
        async for post in cursor:
            post.pop('_id', None)
            post.pop('is_validated', None)
            # Defensive: wrap Post instantiation to skip malformed records
            try:
                posts.append(Post(**post))
            except Exception as e:
                skipped_count += 1
                logger.error(
                    f"Skipping malformed post record (post_id={post.get('post_id')}, message_link={post.get('message_link')}): {e}. "
                    f"Record data: {post}"
                )
                # Continue loading other posts instead of crashing
                continue
        
        if skipped_count > 0:
            logger.warning(f"Loaded {len(posts)} posts from MongoDB, skipped {skipped_count} malformed records.")
        else:
            logger.debug(f"Loaded {len(posts)} posts from MongoDB.")
        return posts

    @classmethod
    async def get_all_posts(cls):
        """
        Get all posts from the database.
        
        Returns:
            List of Post objects
        """
        await cls._ensure_ready()
        logger.info("Getting all posts from MongoDB")
        
        cursor = cls._posts.find()
        posts = []
        skipped_count = 0
        async for post in cursor:
            post.pop('_id', None)
            post.pop('is_validated', None)
            # Defensive: wrap Post instantiation to skip malformed records
            try:
                posts.append(Post(**post))
            except Exception as e:
                skipped_count += 1
                logger.error(
                    f"Skipping malformed post record (post_id={post.get('post_id')}, message_link={post.get('message_link')}): {e}. "
                    f"Record data: {post}"
                )
                # Continue loading other posts instead of crashing
                continue
        
        if skipped_count > 0:
            logger.warning(f"Found {len(posts)} posts, skipped {skipped_count} malformed records")
        else:
            logger.debug(f"Found {len(posts)} posts")
        return posts

    @classmethod
    async def add_post(cls, post):
        await cls._ensure_ready()
        if hasattr(post, 'to_dict'):
            post = post.to_dict()
        post_id = post.get('post_id')

        # If post_id is not provided, allocate an atomic numeric id from counters collection
        if not post_id:
            # Use a small retry loop in case of rare DuplicateKey after allocation
            from pymongo import ReturnDocument
            from pymongo.errors import DuplicateKeyError
            
            id_retries = config.get('database', {}).get('id_allocation_retries', 3)
            for attempt in range(id_retries):
                seq_doc = await cls._counters.find_one_and_update(
                    {"_id": "post_id"}, {"$inc": {"seq": 1}}, upsert=True,
                    return_document=ReturnDocument.AFTER
                )
                try:
                    post_id = int(seq_doc.get('seq'))
                except Exception:
                    post_id = None
                if post_id is None:
                    continue

                post['post_id'] = post_id

                # If a post with this id already exists, loop to get next id
                existing_post = await cls._posts.find_one({"post_id": post_id})
                if existing_post:
                    # Try next sequence number
                    continue
                break

            if post_id is None:
                # Fallback to 1 if something goes terribly wrong
                post_id = 1
                post['post_id'] = post_id

        logger.info("Adding post to MongoDB: post_id=%s, link=%s", post.get('post_id'), post.get('message_link'))

        # If post exists, update; else insert. Handle DuplicateKeyError with a retry if necessary.
        existing_post = await cls.get_post(post_id)
        if existing_post:
            logger.debug("Post with post_id %s exists in MongoDB. Updating.", post_id)
            return await cls.update_post(post_id, post)

        post.pop('_id', None)
        from pymongo.errors import DuplicateKeyError
        id_retries = config.get('database', {}).get('id_allocation_retries', 3)
        for attempt in range(id_retries):
            try:
                await cls._posts.insert_one(post)
                logger.debug("Post with post_id %s added to MongoDB.", post_id)
                return True
            except DuplicateKeyError:
                # Race: another writer inserted same post_id; get next sequence and retry
                seq_doc = await cls._counters.find_one_and_update({"_id": "post_id"}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
                try:
                    post_id = int(seq_doc.get('seq'))
                except Exception:
                    post_id = None
                if post_id is None:
                    continue
                post['post_id'] = post_id
                continue

        # If all retries failed, raise
        raise RuntimeError("Failed to insert post after several attempts due to ID conflicts")

    @classmethod
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
    async def get_post_by_link(cls, message_link: str):
        """
        Find a post document by its exact message_link.

        Returns:
            Post object if found, else None
        """
        await cls._ensure_ready()
        logger.info(f"Getting post from MongoDB by message_link: {message_link}")
        # Try exact match first
        post = await cls._posts.find_one({"message_link": message_link})
        if post and '_id' in post:
            post.pop('_id')
        if post:
            logger.debug(f"Post found in MongoDB by link: {post.get('post_id', '')}, link: {post.get('message_link', '')}")
            return Post(**post)

        # Fallback: try without scheme (strip https:// or http://) if exact match failed
        try:
            if message_link.startswith('https://') or message_link.startswith('http://'):
                stripped = message_link.split('://', 1)[1]
                post = await cls._posts.find_one({"message_link": stripped})
                if post and '_id' in post:
                    post.pop('_id')
                if post:
                    logger.debug(f"Post found in MongoDB by stripped link: {post.get('post_id', '')}, link: {post.get('message_link', '')}")
                    return Post(**post)
        except Exception:
            # Ignore fallback errors and return None
            logger.debug(f"Fallback lookup by stripped link failed for: {message_link}")

        logger.debug(f"No post found in MongoDB for link: {message_link}")
        return None

    @classmethod
    async def get_posts_by_chat_id(cls, chat_id: int):
        """
        Get all Post objects with the given chat_id.
        Accepts both normalized (2723750105) and -100 prefixed (-1002723750105) forms.
        Searches for both forms in database to handle existing records.
        
        Args:
            chat_id: Telegram chat ID to filter by (with or without -100 prefix)
            
        Returns:
            List of Post objects matching the chat_id
        """
        await cls._ensure_ready()
        # Normalize chat_id to handle -100 prefix
        normalized_id = normalize_chat_id(chat_id)
        # Also compute the -100 prefixed form
        prefixed_id = int(f"-100{normalized_id}")
        
        logger.info(f"Getting posts for chat_id: {chat_id} (searching for {normalized_id} or {prefixed_id})")
        
        # Search for either normalized OR -100 prefixed form
        cursor = cls._posts.find({"chat_id": {"$in": [normalized_id, prefixed_id]}})
        posts = []
        async for post in cursor:
            post.pop('_id', None)
            posts.append(Post(**post))
        
        logger.debug(f"Found {len(posts)} posts for chat_id {chat_id}")
        return posts

    @classmethod
    async def update_post(cls, post_id, update_data):
        await cls._ensure_ready()
        logger.info(f"Upserting post {post_id} in MongoDB with data: {update_data.keys()}")
        if not isinstance(update_data, dict):
            raise ValueError(f"update_data must be a dict mapping field names to values, got {type(update_data)}: {update_data}")
        update_data.pop('_id', None)
        # Ensure post_id is set in the update data for upserts
        update_data['post_id'] = post_id
        result = await cls._posts.update_one({"post_id": post_id}, {"$set": update_data}, upsert=True)
        logger.debug(f"Post {post_id} upsert result: modified={result.modified_count}, upserted_id={result.upserted_id}")
        return result.modified_count > 0 or result.upserted_id is not None

    @classmethod
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
    async def load_all_tasks(cls):
        await cls._ensure_ready()
        logger.info("Loading all tasks from MongoDB.")
        cursor = cls._tasks.find().sort('updated_at', -1)
        tasks = []
        skipped_count = 0
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
            
            # Defensive: wrap Task instantiation to skip malformed records
            try:
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
            except Exception as e:
                skipped_count += 1
                logger.error(
                    f"Skipping malformed task record (task_id={task.get('task_id')}, name={task.get('name')}): {e}. "
                    f"Record data: {task}"
                )
                # Continue loading other tasks instead of crashing
                continue
        
        if skipped_count > 0:
            logger.warning(f"Loaded {len(tasks)} tasks from MongoDB, skipped {skipped_count} malformed records.")
        else:
            logger.debug(f"Loaded {len(tasks)} tasks from MongoDB.")
        return tasks

    @classmethod
    async def add_task(cls, task):
        await cls._ensure_ready()
        if hasattr(task, 'to_dict'):
            task = task.to_dict()

        task_id = task.get('task_id')

        # If task_id not provided, allocate atomically from counters
        if not task_id:
            from pymongo import ReturnDocument
            from pymongo.errors import DuplicateKeyError
            
            id_retries = config.get('database', {}).get('id_allocation_retries', 3)
            for attempt in range(id_retries):
                seq_doc = await cls._counters.find_one_and_update(
                    {"_id": "task_id"}, {"$inc": {"seq": 1}}, upsert=True,
                    return_document=ReturnDocument.AFTER
                )
                try:
                    task_id = int(seq_doc.get('seq'))
                except Exception:
                    task_id = None
                if task_id is None:
                    continue

                task['task_id'] = task_id

                existing_task = await cls._tasks.find_one({"task_id": task_id})
                if existing_task:
                    continue
                break

            if task_id is None:
                task_id = 1
                task['task_id'] = task_id

        logger.info("Adding task to MongoDB: task_id=%s, name=%s", task.get('task_id'), task.get('name'))
        existing_task = await cls.get_task(task_id)
        if existing_task:
            logger.debug(f"Task with task_id {task_id} exists in MongoDB. Updating.")
            return await cls.update_task(task_id, task)

        if 'created_at' not in task:
            task['created_at'] = datetime.now(timezone.utc)
        if 'updated_at' not in task:
            task['updated_at'] = datetime.now(timezone.utc)

        # Insert with duplicate-key retry in case of race
        from pymongo.errors import DuplicateKeyError
        from pymongo import ReturnDocument
        
        id_retries = config.get('database', {}).get('id_allocation_retries', 3)
        for attempt in range(id_retries):
            try:
                await cls._tasks.insert_one(task)
                logger.debug("Task with task_id %s added to MongoDB.", task_id)
                return True
            except DuplicateKeyError:
                seq_doc = await cls._counters.find_one_and_update({"_id": "task_id"}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
                try:
                    task_id = int(seq_doc.get('seq'))
                except Exception:
                    task_id = None
                if task_id is None:
                    continue
                task['task_id'] = task_id
                continue

        raise RuntimeError("Failed to insert task after several attempts due to ID conflicts")

    @classmethod
    async def get_task(cls, task_id):
        await cls._ensure_ready()
        logger.info(f"Getting task from MongoDB with task_id: {task_id}")
        task = await cls._tasks.find_one({"task_id": task_id})

        # Normalize to plain dict to avoid pandas.Series.pop('_id') KeyError ("'_id' not found in axis")
        if task is not None and not isinstance(task, dict):
            try:
                task = dict(task)
            except Exception:
                if hasattr(task, "to_dict"):
                    try:
                        task = dict(task.to_dict())
                    except Exception:
                        # leave as-is if conversion fails
                        pass

        if isinstance(task, dict):
            task.pop('_id', None)

        if task:
            # Safe counts in case fields are None
            post_count = len(task.get('post_ids') or [])
            acc_count = len(task.get('accounts') or [])
            logger.debug(f"Task found in MongoDB: {task.get('task_id', '')}, status: {task.get('status', '')}, updated_at: {task.get('updated_at', '')}, posts: {post_count}, accounts: {acc_count}")
        return Task(**task) if task else None

    @classmethod
    async def update_task(cls, task_id, update_data):
        await cls._ensure_ready()
        logger.info(f"Upserting task {task_id} in MongoDB with data: {update_data.keys()}")
        if not isinstance(update_data, dict):
            raise ValueError(f"update_data must be a dict mapping field names to values, got {type(update_data)}: {update_data}")
        update_data.pop('_id', None)
        # Ensure task_id is set in the update data for upserts
        update_data['task_id'] = task_id
        # Update the modification timestamp
        from datetime import datetime, timezone
        update_data['updated_at'] = datetime.now(timezone.utc)
        result = await cls._tasks.update_one({"task_id": task_id}, {"$set": update_data}, upsert=True)
        logger.debug(f"Task {task_id} upsert result: modified={result.modified_count}, upserted_id={result.upserted_id}")
        return result.modified_count > 0 or result.upserted_id is not None

    @classmethod
    async def delete_task(cls, task_id):
        await cls._ensure_ready()
        logger.info(f"Deleting task from MongoDB with task_id: {task_id}")
        if hasattr(task_id, 'task_id'):
            task_id = task_id.task_id
        # Cascade: delete all runs and their events linked to this task first
        try:
            cleared = await cls.clear_runs_by_task(task_id)
            logger.info(f"Cleared runs for task {task_id} before deleting task: {cleared}")
        except Exception as e:
            # Log and proceed to delete the task record itself; do not fail the whole operation
            logger.warning(f"Failed to clear runs for task {task_id} prior to task deletion: {e}")

        result = await cls._tasks.delete_one({"task_id": task_id})
        logger.debug(f"Task {task_id} delete result: {result.deleted_count}")
        return result.deleted_count > 0

# --- User methods ---
    @classmethod
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
    async def verify_user_credentials(cls, username: str, password: str) -> tuple[bool, dict | None]:
        """
        Verify user credentials.
        
        Args:
            username: Username to verify
            password: Plain text password to verify
            
        Returns:
            Tuple of (success: bool, user_data: dict | None)
        """
        from auxilary_logic.encryption import verify_password
        
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
    async def get_all_users(cls):
        """
        Get all users from the database.
        
        Returns:
            List of user dictionaries (without password_hash for security)
        """
        await cls._ensure_ready()
        logger.info("Getting all users from MongoDB")
        
        cursor = cls._users.find()
        users = []
        async for user in cursor:
            user.pop('_id', None)
            users.append(user)
        
        logger.debug(f"Found {len(users)} users")
        return users

    @classmethod
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

    @classmethod
    async def delete_user(cls, username: str):
        """
        Delete a user from the database.
        
        Args:
            username: Username to delete
            
        Returns:
            True if deleted successfully, False otherwise
        """
        await cls._ensure_ready()
        logger.info(f"Deleting user from MongoDB: {username}")
        
        result = await cls._users.delete_one({"username": username.lower()})
        logger.debug(f"User {username} delete result: {result.deleted_count}")
        return result.deleted_count > 0

# --- Reporter/Events/Runs methods ---
    @classmethod
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

# --- Database validation and cleanup methods ---
    @classmethod
    async def validate_and_report_broken_objects(cls):
        """
        Scan all collections for malformed objects and return a report.
        Does NOT delete or modify any records - only reports issues.
        
        Returns:
            Dictionary with counts and details of broken objects per collection
        """
        await cls._ensure_ready()
        logger.info("Starting database validation scan for malformed objects")
        
        report = {
            'tasks': {'total': 0, 'broken': 0, 'details': []},
            'accounts': {'total': 0, 'broken': 0, 'details': []},
            'posts': {'total': 0, 'broken': 0, 'details': []},
        }
        
        # Validate tasks
        cursor = cls._tasks.find()
        async for task in cursor:
            report['tasks']['total'] += 1
            task_copy = task.copy()
            task_copy.pop('_id', None)
            
            try:
                # Try to instantiate Task object
                created_at = task_copy.get('created_at')
                updated_at = task_copy.get('updated_at')
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
                
                Task(
                    task_id=task_copy.get('task_id'),
                    name=task_copy.get('name'),
                    post_ids=task_copy.get('post_ids'),
                    accounts=task_copy.get('accounts'),
                    action=task_copy.get('action'),
                    description=task_copy.get('description'),
                    status=task_copy.get('status'),
                    created_at=created_at,
                    updated_at=updated_at
                )
            except Exception as e:
                report['tasks']['broken'] += 1
                report['tasks']['details'].append({
                    'task_id': task_copy.get('task_id'),
                    'name': task_copy.get('name'),
                    'error': str(e),
                    'error_type': type(e).__name__,
                    'record': task_copy
                })
        
        # Validate accounts
        cursor = cls._accounts.find()
        async for acc in cursor:
            report['accounts']['total'] += 1
            acc_copy = acc.copy()
            acc_copy.pop('_id', None)
            
            try:
                Account(acc_copy)
            except Exception as e:
                report['accounts']['broken'] += 1
                report['accounts']['details'].append({
                    'phone_number': acc_copy.get('phone_number'),
                    'account_id': acc_copy.get('account_id'),
                    'error': str(e),
                    'error_type': type(e).__name__,
                    'record': acc_copy
                })
        
        # Validate posts
        cursor = cls._posts.find()
        async for post in cursor:
            report['posts']['total'] += 1
            post_copy = post.copy()
            post_copy.pop('_id', None)
            post_copy.pop('is_validated', None)
            
            try:
                Post(**post_copy)
            except Exception as e:
                report['posts']['broken'] += 1
                report['posts']['details'].append({
                    'post_id': post_copy.get('post_id'),
                    'message_link': post_copy.get('message_link'),
                    'error': str(e),
                    'error_type': type(e).__name__,
                    'record': post_copy
                })
        
        # Log summary
        total_broken = report['tasks']['broken'] + report['accounts']['broken'] + report['posts']['broken']
        total_objects = report['tasks']['total'] + report['accounts']['total'] + report['posts']['total']
        
        logger.info(
            f"Database validation complete: {total_objects} total objects scanned, {total_broken} malformed objects found. "
            f"Tasks: {report['tasks']['broken']}/{report['tasks']['total']}, "
            f"Accounts: {report['accounts']['broken']}/{report['accounts']['total']}, "
            f"Posts: {report['posts']['broken']}/{report['posts']['total']}"
        )
        
        return report

    @classmethod
    async def delete_broken_objects(cls, collection_name: str, identifiers: list):
        """
        Delete broken objects from a specific collection.
        Use with caution - this permanently deletes records!
        
        Args:
            collection_name: One of 'tasks', 'accounts', 'posts'
            identifiers: List of identifiers to delete (task_id, phone_number, or post_id)
            
        Returns:
            Number of deleted objects
        """
        await cls._ensure_ready()
        logger.warning(f"Deleting {len(identifiers)} broken objects from {collection_name}")
        
        if collection_name == 'tasks':
            result = await cls._tasks.delete_many({'task_id': {'$in': identifiers}})
        elif collection_name == 'accounts':
            result = await cls._accounts.delete_many({'phone_number': {'$in': identifiers}})
        elif collection_name == 'posts':
            result = await cls._posts.delete_many({'post_id': {'$in': identifiers}})
        else:
            raise ValueError(f"Invalid collection_name: {collection_name}. Must be 'tasks', 'accounts', or 'posts'")
        
        deleted_count = result.deleted_count
        logger.info(f"Deleted {deleted_count} broken objects from {collection_name}")
        return deleted_count

# --- Proxy methods ---
    @classmethod
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
            from auxilary_logic.encryption import encrypt_secret, PURPOSE_PROXY_PASSWORD
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
    async def get_proxy(cls, proxy_name: str):
        """
        Get a proxy by name and decrypt password if present.
        
        Args:
            proxy_name: Proxy name to search for
            
        Returns:
            Proxy dictionary if found (with decrypted password), None otherwise
        """
        from auxilary_logic.encryption import decrypt_secret, PURPOSE_PROXY_PASSWORD
        
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
    async def get_all_proxies(cls):
        """
        Get all proxies from the database with decrypted passwords.
        
        Returns:
            List of proxy dictionaries (with decrypted passwords)
        """
        from auxilary_logic.encryption import decrypt_secret, PURPOSE_PROXY_PASSWORD
        
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
    async def get_active_proxies(cls):
        """
        Get all active proxies from the database with decrypted passwords.
        
        Returns:
            List of active proxy dictionaries (with decrypted passwords)
        """
        from auxilary_logic.encryption import decrypt_secret, PURPOSE_PROXY_PASSWORD
        
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
    async def get_least_used_proxy(cls):
        """
        Get the active proxy with the least number of connected accounts (with decrypted password).
        
        Returns:
            Proxy dictionary if found (with decrypted password), None otherwise
        """
        from auxilary_logic.encryption import decrypt_secret, PURPOSE_PROXY_PASSWORD
        
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
            from auxilary_logic.encryption import encrypt_secret, PURPOSE_PROXY_PASSWORD
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

# --- Channel methods ---
    @classmethod
    async def add_channel(cls, channel_data: dict):
        """
        Add a new channel to the database.
        
        Args:
            channel_data: Dictionary or Channel object with channel data
            
        Returns:
            True if successful
            
        Raises:
            ValueError: If channel already exists
        """
        await cls._ensure_ready()
        
        if hasattr(channel_data, 'to_dict'):
            channel_data = channel_data.to_dict()
        
        chat_id = channel_data.get('chat_id')
        logger.info(f"Adding channel to MongoDB: chat_id={chat_id}, name={channel_data.get('channel_name')}")
        
        # Check if channel already exists
        existing_channel = await cls.get_channel(chat_id)
        if existing_channel:
            logger.warning(f"Channel with chat_id {chat_id} already exists")
            raise ValueError(f"Channel with chat_id {chat_id} already exists")
        
        # Ensure timestamps
        from datetime import datetime, timezone
        if 'created_at' not in channel_data:
            channel_data['created_at'] = datetime.now(timezone.utc)
        if 'updated_at' not in channel_data:
            channel_data['updated_at'] = datetime.now(timezone.utc)
        
        # Ensure tags is a list
        if 'tags' not in channel_data:
            channel_data['tags'] = []
        
        # Ensure url_aliases is a list
        if 'url_aliases' not in channel_data:
            channel_data['url_aliases'] = []
        
        channel_data.pop('_id', None)
        await cls._channels.insert_one(channel_data)
        logger.debug(f"Channel with chat_id {chat_id} added to MongoDB")
        return True

    @classmethod
    async def get_channel(cls, chat_id: int):
        """
        Get a channel by chat_id.
        Accepts both normalized (2723750105) and -100 prefixed (-1002723750105) forms.
        Searches for both forms in database to handle existing records.
        
        Args:
            chat_id: Telegram chat ID (with or without -100 prefix)
            
        Returns:
            Channel object if found, None otherwise
        """
        await cls._ensure_ready()
        # Normalize chat_id to handle -100 prefix
        normalized_id = normalize_chat_id(chat_id)
        # Also compute the -100 prefixed form
        prefixed_id = int(f"-100{normalized_id}")
        
        logger.info(f"Getting channel from MongoDB with chat_id: {chat_id} (searching for {normalized_id} or {prefixed_id})")
        
        # Search for either normalized OR -100 prefixed form
        channel = await cls._channels.find_one({"chat_id": {"$in": [normalized_id, prefixed_id]}})
        if channel and '_id' in channel:
            channel.pop('_id')
        
        if channel:
            logger.debug(f"Found channel with chat_id {channel.get('chat_id')} in MongoDB")
        
        return Channel(**channel) if channel else None

    @classmethod
    async def get_channels_bulk(cls, chat_ids: list):
        """
        Get multiple channels by their chat_ids in a single query.
        Accepts both normalized and -100 prefixed forms for each chat_id.
        
        Args:
            chat_ids: List of Telegram chat IDs (with or without -100 prefix)
            
        Returns:
            List of Channel objects found (may be fewer than requested if some don't exist)
        """
        await cls._ensure_ready()
        
        if not chat_ids:
            return []
        
        # Build list of all possible ID forms (normalized and prefixed) for each chat_id
        all_possible_ids = []
        for chat_id in chat_ids:
            normalized_id = normalize_chat_id(chat_id)
            prefixed_id = int(f"-100{normalized_id}")
            all_possible_ids.extend([normalized_id, prefixed_id])
        
        logger.info(f"Getting {len(chat_ids)} channels from MongoDB in bulk")
        
        # Single query to get all matching channels
        cursor = cls._channels.find({"chat_id": {"$in": all_possible_ids}})
        channels = []
        async for channel in cursor:
            channel.pop('_id', None)
            channels.append(Channel(**channel))
        
        logger.debug(f"Found {len(channels)} channels out of {len(chat_ids)} requested")
        return channels

    @classmethod
    async def get_all_channels(cls):
        """
        Get all channels from the database.
        
        Returns:
            List of Channel objects
        """
        await cls._ensure_ready()
        logger.info("Loading all channels from MongoDB")
        
        cursor = cls._channels.find()
        channels = []
        async for channel in cursor:
            channel.pop('_id', None)
            channels.append(Channel(**channel))
        
        logger.debug(f"Loaded {len(channels)} channels from MongoDB")
        return channels

    @classmethod
    async def get_channels_by_tag(cls, tag: str):
        """
        Get all channels with a specific tag.
        
        Args:
            tag: Tag to filter by
            
        Returns:
            List of Channel objects
        """
        await cls._ensure_ready()
        logger.info(f"Getting channels with tag: {tag}")
        
        cursor = cls._channels.find({"tags": tag})
        channels = []
        async for channel in cursor:
            channel.pop('_id', None)
            channels.append(Channel(**channel))
        
        logger.debug(f"Found {len(channels)} channels with tag '{tag}'")
        return channels

    @classmethod
    async def search_channels_by_name(cls, name_query: str):
        """
        Search for channels by name using case-insensitive regex matching.
        
        Args:
            name_query: Search string to match against channel names
            
        Returns:
            List of Channel objects matching the search query
        """
        await cls._ensure_ready()
        logger.info(f"Searching channels by name: {name_query}")
        
        # Use regex for case-insensitive partial matching
        import re
        pattern = re.compile(re.escape(name_query), re.IGNORECASE)
        
        cursor = cls._channels.find({"channel_name": {"$regex": pattern}})
        channels = []
        async for channel in cursor:
            channel.pop('_id', None)
            channels.append(Channel(**channel))
        
        logger.debug(f"Found {len(channels)} channels matching '{name_query}'")
        return channels

    @classmethod
    async def get_subscribed_channels(cls, phone_number: str):
        """
        Get all Channel objects that an account is subscribed to.
        
        Args:
            phone_number: Phone number of the account
            
        Returns:
            List of Channel objects the account is subscribed to
        """
        await cls._ensure_ready()
        logger.info(f"Getting subscribed channels for account: {phone_number}")
        
        # Get account and its subscribed_to list
        account = await cls.get_account(phone_number)
        if not account:
            logger.warning(f"Account {phone_number} not found")
            return []
        
        subscribed_to = account.subscribed_to if hasattr(account, 'subscribed_to') else []
        if not subscribed_to:
            logger.debug(f"Account {phone_number} has no subscriptions")
            return []
        
        # Get all channels where chat_id is in subscribed_to list
        cursor = cls._channels.find({"chat_id": {"$in": subscribed_to}})
        channels = []
        async for channel in cursor:
            channel.pop('_id', None)
            channels.append(Channel(**channel))
        
        logger.debug(f"Found {len(channels)} subscribed channels for account {phone_number}")
        return channels

    @classmethod
    async def get_channel_subscribers(cls, chat_id: int):
        """
        Get all Account objects that are subscribed to a given channel.
        Uses native MongoDB query on the subscribed_to array field.
        Searches for both normalized and -100 prefixed forms of chat_id.
        
        Args:
            chat_id: Telegram chat ID (with or without -100 prefix)
            
        Returns:
            List of Account objects subscribed to the channel
        """
        await cls._ensure_ready()
        # Normalize chat_id to handle -100 prefix
        normalized_id = normalize_chat_id(chat_id)
        # Also compute the -100 prefixed form
        prefixed_id = int(f"-100{normalized_id}")
        
        logger.info(f"Getting subscribers for channel: {chat_id} (searching for {normalized_id} or {prefixed_id})")
        
        # MongoDB natively supports querying array fields - if subscribed_to contains
        # either the normalized or prefixed form, the account will be returned
        cursor = cls._accounts.find({
            "subscribed_to": {"$in": [normalized_id, prefixed_id]}
        })
        
        accounts = []
        skipped_count = 0
        async for acc in cursor:
            acc.pop('_id', None)
            try:
                accounts.append(Account(acc))
            except Exception as e:
                logger.warning(f"Skipping malformed account during channel subscriber lookup: {e}")
                skipped_count += 1
        
        if skipped_count > 0:
            logger.warning(f"Found {len(accounts)} subscribers for channel {chat_id}, skipped {skipped_count} malformed records")
        else:
            logger.debug(f"Found {len(accounts)} subscribers for channel {chat_id}")
        
        return accounts

    @classmethod
    async def update_channel(cls, chat_id: int, update_data: dict):
        """
        Update a channel's data.
        Accepts both normalized (2723750105) and -100 prefixed (-1002723750105) forms.
        Searches for both forms in database to handle existing records.
        
        Args:
            chat_id: Telegram chat ID (with or without -100 prefix)
            update_data: Dictionary of fields to update
            
        Returns:
            True if updated successfully, False otherwise
        """
        await cls._ensure_ready()
        # Normalize chat_id to handle -100 prefix
        normalized_id = normalize_chat_id(chat_id)
        # Also compute the -100 prefixed form
        prefixed_id = int(f"-100{normalized_id}")
        
        logger.info(f"Updating channel {chat_id} (searching for {normalized_id} or {prefixed_id}) with data: {update_data.keys()}")
        
        if not isinstance(update_data, dict):
            update_data = update_data.to_dict()
        
        update_data.pop('_id', None)
        update_data.pop('chat_id', None)  # Don't allow changing chat_id
        
        # Update the modification timestamp
        from datetime import datetime, timezone
        update_data['updated_at'] = datetime.now(timezone.utc)
        
        # Update whichever form exists in the database
        result = await cls._channels.update_one(
            {"chat_id": {"$in": [normalized_id, prefixed_id]}},
            {"$set": update_data}
        )
        
        logger.debug(f"Channel update result: modified={result.modified_count}")
        return result.modified_count > 0

    @classmethod
    async def delete_channel(cls, chat_id: int):
        """
        Delete a channel from the database.
        Accepts both normalized (2723750105) and -100 prefixed (-1002723750105) forms.
        Searches for both forms in database to handle existing records.
        
        Args:
            chat_id: Telegram chat ID (with or without -100 prefix)
            
        Returns:
            True if deleted successfully, False otherwise
        """
        await cls._ensure_ready()
        # Normalize chat_id to handle -100 prefix
        normalized_id = normalize_chat_id(chat_id)
        # Also compute the -100 prefixed form
        prefixed_id = int(f"-100{normalized_id}")
        
        logger.info(f"Deleting channel from MongoDB with chat_id: {chat_id} (searching for {normalized_id} or {prefixed_id})")
        
        # Delete whichever form exists in the database
        result = await cls._channels.delete_one({"chat_id": {"$in": [normalized_id, prefixed_id]}})
        logger.debug(f"Channel delete result: {result.deleted_count}")
        return result.deleted_count > 0

    @classmethod
    async def get_channel_by_url_alias(cls, alias: str):
        """
        Get a channel by one of its URL aliases.
        
        Args:
            alias: URL identifier (username, /c/ path, etc.)
            
        Returns:
            Channel object if found, None otherwise
        """
        await cls._ensure_ready()
        logger.info(f"Getting channel from MongoDB by url_alias: {alias}")
        
        # Search for channel with this alias
        channel = await cls._channels.find_one({"url_aliases": alias})
        if channel and '_id' in channel:
            channel.pop('_id')
        
        if channel:
            logger.debug(f"Found channel with chat_id {channel.get('chat_id')} for alias '{alias}'")
            return Channel(**channel)
        
        logger.debug(f"No channel found with alias '{alias}'")
        return None

    @classmethod
    async def add_channel_url_alias(cls, chat_id: int, alias: str):
        """
        Add a URL alias to a channel if it doesn't already exist.
        Uses $addToSet to avoid duplicates.
        
        Args:
            chat_id: Telegram chat ID (with or without -100 prefix)
            alias: URL identifier to add (username, /c/ path, etc.)
            
        Returns:
            True if alias was added or already existed, False if channel not found
        """
        await cls._ensure_ready()
        # Normalize chat_id to handle -100 prefix
        normalized_id = normalize_chat_id(chat_id)
        # Also compute the -100 prefixed form
        prefixed_id = int(f"-100{normalized_id}")
        
        logger.info(f"Adding url_alias '{alias}' to channel {chat_id}")
        
        # Update the modification timestamp along with adding alias
        from datetime import datetime, timezone
        
        result = await cls._channels.update_one(
            {"chat_id": {"$in": [normalized_id, prefixed_id]}},
            {
                "$addToSet": {"url_aliases": alias},
                "$set": {"updated_at": datetime.now(timezone.utc)}
            }
        )
        
        if result.matched_count > 0:
            logger.debug(f"Added alias '{alias}' to channel {chat_id}")
            return True
        else:
            logger.warning(f"Channel {chat_id} not found, couldn't add alias '{alias}'")
            return False

    @classmethod
    async def get_channels_with_post_counts(cls):
        """
        Get all channels with their post counts from the posts collection.
        
        Returns:
            List of dicts with channel data and post_count field:
            [
                {
                    'chat_id': 123,
                    'channel_name': 'Example Channel',
                    'is_private': False,
                    'tags': ['news'],
                    ...,
                    'post_count': 42
                },
                ...
            ]
        """
        await cls._ensure_ready()
        logger.info("Getting channels with post counts")
        
        # Aggregate post counts by chat_id
        pipeline = [
            {"$group": {"_id": "$chat_id", "post_count": {"$sum": 1}}}
        ]
        cursor = cls._posts.aggregate(pipeline)
        post_counts = {}
        async for result in cursor:
            if result.get('_id') is not None:
                post_counts[result['_id']] = result['post_count']
        
        # Get all channels
        channels_cursor = cls._channels.find()
        channels_with_counts = []
        async for channel in channels_cursor:
            channel.pop('_id', None)
            chat_id = channel.get('chat_id')
            channel['post_count'] = post_counts.get(chat_id, 0)
            channels_with_counts.append(channel)
        
        logger.debug(f"Found {len(channels_with_counts)} channels with post counts")
        return channels_with_counts


def get_db() -> MongoStorage:
    return MongoStorage()