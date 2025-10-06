import os, inspect
from pandas import Timestamp
from functools import wraps
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from logger import setup_logger, load_config
from agent import Account
from taskhandler import Post, Task

config = load_config()
logger = setup_logger("DB", "main.log")

load_dotenv()
db_url = os.getenv('db_url')
db_name = os.getenv('db_name', 'LikeBot')

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

    @classmethod
    def _init(cls):
        if cls._accounts is None:
            logger.info("Initializing MongoDB client and collections.")
            client = AsyncIOMotorClient(db_url)
            cls._db = client[db_name]
            cls._accounts = cls._db["accounts"]
            cls._posts = cls._db["posts"]
            cls._tasks = cls._db["tasks"]

    @classmethod
    @ensure_async
    async def load_all_accounts(cls):
        cls._init()
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
    async def save_all_accounts(cls, accounts):
        # cls._init()
        # await cls._accounts.delete_many({})
        # docs = [acc.to_dict() if hasattr(acc, 'to_dict') else acc for acc in accounts]
        # for doc in docs:
        #     doc.pop('_id', None)
        # if docs:
        #     await cls._accounts.insert_many(docs)
        pass

    @classmethod
    @ensure_async
    async def add_account(cls, account_data):
        cls._init()
        logger.info(f"Adding account to MongoDB: {account_data}")
        if hasattr(account_data, 'to_dict'):
            account_data = account_data.to_dict()
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
        cls._init()
        logger.info(f"Getting account from MongoDB with phone number: {phone_number}")
        acc = await cls._accounts.find_one({"phone_number": phone_number})
        if acc and '_id' in acc:
            acc.pop('_id')
        if acc:
            logger.debug(f"Account found in MongoDB: {acc}")
        return Account(acc) if acc else None

    @classmethod
    @ensure_async
    async def update_account(cls, phone_number, update_data):
        cls._init()
        logger.info(f"Updating account {phone_number} in MongoDB with data: {update_data}")
        if not isinstance(update_data, dict):
            raise ValueError(f"update_data must be a dict mapping field names to values, got {type(update_data)}: {update_data}")
        update_data.pop('_id', None)
        result = await cls._accounts.update_one({"phone_number": phone_number}, {"$set": update_data})
        logger.debug(f"Account {phone_number} update result: {result.modified_count}")
        return result.modified_count > 0

    @classmethod
    @ensure_async
    async def delete_account(cls, phone_number):
        cls._init()
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
        cls._init()
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
    async def save_all_posts(cls, posts):
        # cls._init()
        # await cls._posts.delete_many({})
        # docs = [post.to_dict() if hasattr(post, 'to_dict') else post for post in posts]
        # for doc in docs:
        #     doc.pop('_id', None)
        # if docs:
        #     await cls._posts.insert_many(docs)
        pass

    @classmethod
    @ensure_async
    async def add_post(cls, post):
        cls._init()
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
        cls._init()
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
        cls._init()
        logger.info(f"Updating post {post_id} in MongoDB with data: {update_data}")
        if not isinstance(update_data, dict):
            raise ValueError(f"update_data must be a dict mapping field names to values, got {type(update_data)}: {update_data}")
        update_data.pop('_id', None)
        result = await cls._posts.update_one({"post_id": post_id}, {"$set": update_data})
        logger.debug(f"Post {post_id} update result: {result.modified_count}")
        return result.modified_count > 0

    @classmethod
    @ensure_async
    async def delete_post(cls, post_id):
        cls._init()
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
        cls._init()
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
    async def save_all_tasks(cls, tasks):
        # cls._init()
        # await cls._tasks.delete_many({})
        # docs = [task.to_dict() if hasattr(task, 'to_dict') else task for task in tasks]
        # for doc in docs:
        #     doc.pop('_id', None)
        # if docs:
        #     await cls._tasks.insert_many(docs)
        pass

    @classmethod
    @ensure_async
    async def add_task(cls, task):
        cls._init()
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
        cls._init()
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
        cls._init()
        logger.info(f"Updating task {task_id} in MongoDB with data: {update_data}")
        if not isinstance(update_data, dict):
            raise ValueError(f"update_data must be a dict mapping field names to values, got {type(update_data)}: {update_data}")
        update_data.pop('_id', None)
        result = await cls._tasks.update_one({"task_id": task_id}, {"$set": update_data})
        logger.debug(f"Task {task_id} update result: {result.modified_count}")
        return result.modified_count > 0

    @classmethod
    @ensure_async
    async def delete_task(cls, task_id):
        cls._init()
        logger.info(f"Deleting task from MongoDB with task_id: {task_id}")
        if hasattr(task_id, 'task_id'):
            task_id = task_id.task_id
        result = await cls._tasks.delete_one({"task_id": task_id})
        logger.debug(f"Task {task_id} delete result: {result.deleted_count}")
        return result.deleted_count > 0


def get_db() -> MongoStorage:
    return MongoStorage()