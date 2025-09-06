import os, json, inspect
from pandas import read_csv, Timestamp, DataFrame
from abc import ABC, abstractmethod
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

def ensure_async(func):
    if inspect.iscoroutinefunction(func):
        return func

    @wraps(func)
    async def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


class StorageInterface(ABC):
    
    # Account managing
    @abstractmethod
    def load_all_accounts(self) -> list[Account]:
        pass

    @abstractmethod
    def save_all_accounts(self, accounts: list[Account]):
        pass
    
    @abstractmethod
    def add_account(self, account_data: dict) -> bool:
        pass

    @abstractmethod
    def get_account(self, account_id: str) -> Account | None:
        pass

    @abstractmethod
    def update_account(self, phone_number: str, update_data: dict) -> bool:
        pass

    @abstractmethod
    def delete_account(self, phone_number: str) -> bool:
        pass



    # Task managing
    @abstractmethod
    def load_all_tasks(self) -> list[Task]:
        pass

    @abstractmethod
    def save_all_tasks(self, tasks: list[Task]):
        pass

    @abstractmethod
    def add_task(self, task: Task):
        pass

    @abstractmethod
    def get_task(self, task_id: int) -> Task:
        pass

    @abstractmethod
    def update_task(self, task_id: int, update_data: dict) -> bool:
        pass

    @abstractmethod
    def delete_task(self, task_id: int) -> bool:
        pass



    # Post managing
    @abstractmethod
    def load_all_posts(self) -> list[Post]:
        pass

    @abstractmethod
    def save_all_posts(self, posts: list[Post]):
        pass

    @abstractmethod
    def add_post(self, post: Post):
        pass

    @abstractmethod
    def get_post(self, post_id: str) -> Post:
        pass

    @abstractmethod
    def update_post(self, post_id: str, update_data: dict) -> bool:
        pass

    @abstractmethod
    def delete_post(self, post_id: str) -> bool:
        pass


class FileStorage(StorageInterface):

    @staticmethod
    @ensure_async
    async def load_all_accounts():
        """
        Load accounts from a JSON or CSV file and return a list of Account objects.
        """
        logger.info("Loading all accounts from file storage.")
        file_path = config.get('filepaths', {}).get('accounts', 'accounts.json')
        if os.path.exists(file_path):
            if file_path.endswith('.json'):
                with open(file_path, 'r', encoding='utf-8') as file:
                    accounts = json.load(file)
                    logger.debug(f"Loaded {len(accounts)} accounts from {file_path}.")
                    return [Account(account) for account in accounts]
            elif file_path.endswith('.csv'):
                df = read_csv(file_path)
                logger.debug(f"Loaded {len(df)} accounts from {file_path} (CSV).")
                return [Account(row.to_dict()) for _, row in df.iterrows()]
        raise FileNotFoundError(f"No accounts file found at {file_path}.")

    @staticmethod
    @ensure_async
    async def save_all_accounts(accounts):
        """
        Save a list of Account objects to a JSON or CSV file.
        """
        logger.info(f"Saving {len(accounts)} accounts to file storage.")
        file_path = config.get('filepaths', {}).get('accounts', 'accounts.json')
        data = [acc.to_dict() for acc in accounts]
        if file_path.endswith('.json'):
            with open(file_path, 'w', encoding='utf-8') as file:
                json.dump(data, file, indent=4)
            logger.debug(f"Accounts saved to {file_path} (JSON).")
        elif file_path.endswith('.csv'):
            df = DataFrame(data)
            df.to_csv(file_path, index=False)
            logger.debug(f"Accounts saved to {file_path} (CSV).")
        else:
            raise ValueError("Unsupported file type. Use 'json' or 'csv'.")

    @classmethod
    @ensure_async
    async def add_account(cls, account_data):
        """
        Add a new account to the accounts file.
        If an account with the same phone number exists, update it instead.
        """
        logger.info(f"Adding account: {account_data}")
        if isinstance(account_data, Account):
            account_data = account_data.to_dict()
        phone_number = account_data.get('phone_number')
        if await cls.get_account(phone_number):
            logger.debug(f"Account with phone number {phone_number} exists. Updating.")
            return await cls.update_account(phone_number, account_data)
        else:
            accounts = await cls.load_all_accounts()
            accounts.append(Account(account_data))
            await cls.save_all_accounts(accounts)
            logger.debug(f"Account with phone number {phone_number} added.")
            return True

    @classmethod
    @ensure_async
    async def get_account(cls, phone_number):
        """
        Read an account by phone number.
        """
        logger.info(f"Getting account with phone number: {phone_number}")
        accounts = await cls.load_all_accounts()
        for acc in accounts:
            if acc.phone_number == phone_number:
                logger.debug(f"Account found: {acc}")
                return acc
        return None

    @classmethod
    @ensure_async
    async def update_account(cls, phone_number, update_data):
        """
        Update an account by phone number.
        """
        logger.info(f"Updating account {phone_number} with data: {update_data}")
        accounts = await cls.load_all_accounts()
        updated = False
        for acc in accounts:
            if acc.phone_number == phone_number:
                for k, v in update_data.items():
                    setattr(acc, k, v)
                updated = True
        if updated:
            await cls.save_all_accounts(accounts)
            logger.debug(f"Account {phone_number} updated.")
        return updated

    @classmethod
    @ensure_async
    async def delete_account(cls, phone_number):
        """
        Delete an account by phone number.
        """
        logger.info(f"Deleting account with phone number: {phone_number}")
        accounts = await cls.load_all_accounts()
        new_accounts = [acc for acc in accounts if acc.phone_number != phone_number]
        if len(new_accounts) != len(accounts):
            await cls.save_all_accounts(new_accounts)
            logger.debug(f"Account {phone_number} deleted.")
            return True
        return False



    @staticmethod
    @ensure_async
    async def load_all_posts():
        """
        Load posts from a JSON or CSV file and return a list of Post objects.
        """
        logger.info("Loading all posts from file storage.")
        file_path = config.get('filepaths', {}).get('posts', 'posts.json')
        if os.path.exists(file_path):
            if file_path.endswith('.json'):
                with open(file_path, 'r', encoding='utf-8') as file:
                    data = json.load(file)
                    logger.debug(f"Loaded {len(data)} posts from {file_path}.")
                    # Only pass allowed keys to Post constructor
                    allowed_keys = {'post_id', 'chat_id', 'message_id', 'message_link', 'created_at', 'updated_at'}
                    return [Post(**{k: v for k, v in post.items() if k in allowed_keys}) for post in data]
            elif file_path.endswith('.csv'):
                df = read_csv(file_path)
                logger.debug(f"Loaded {len(df)} posts from {file_path} (CSV).")
                return [Post(row['post_id'], row['message_link'], row.get('chat_id'), row.get('message_id'), row.get('created_at'), row.get('updated_at')) for _, row in df.iterrows()]
        raise FileNotFoundError(f"No posts file found at {file_path}.")

    @staticmethod
    @ensure_async
    async def save_all_posts(posts):
        """
        Save a list of Post objects to a JSON or CSV file.
        """
        logger.info(f"Saving {len(posts)} posts to file storage.")
        file_path = config.get('filepaths', {}).get('posts', 'posts.json')
        data = [post.to_dict() for post in posts]
        if file_path.endswith('.json'):
            with open(file_path, 'w', encoding='utf-8') as file:
                json.dump(data, file, indent=4)
            logger.debug(f"Posts saved to {file_path} (JSON).")
        elif file_path.endswith('.csv'):
            df = DataFrame(data)
            df.to_csv(file_path, index=False)
            logger.debug(f"Posts saved to {file_path} (CSV).")
        else:
            raise ValueError("Unsupported file type. Use 'json' or 'csv'.")

    @classmethod
    @ensure_async
    async def add_post(cls, post):
        """
        Add a new post to the posts file.
        If a post with the same post_id exists, update it instead.
        """
        logger.info(f"Adding post: {post}")
        if isinstance(post, Post):
            post_data = post.to_dict()
        else:
            post_data = post
        post_id = post_data.get('post_id')
        posts = await cls.load_all_posts()
        if not post_id:
            used_ids = set()
            for p in posts:
                try:
                    used_ids.add(int(p.post_id))
                except Exception:
                    continue
            post_id = 1
            while post_id in used_ids:
                post_id += 1
            post_data['post_id'] = post_id
        existing_post = await cls.get_post(post_id)
        if existing_post:
            logger.debug(f"Post with post_id {post_id} exists. Updating.")
            return await cls.update_post(post_id, post_data)
        posts.append(Post(**post_data))
        await cls.save_all_posts(posts)
        logger.debug(f"Post with post_id {post_id} added.")
        return True

    @classmethod
    @ensure_async
    async def get_post(cls, post_id):
        """
        Get a post by post_id.
        """
        logger.info(f"Getting post with post_id: {post_id}")
        posts = await cls.load_all_posts()
        for post in posts:
            if str(post.post_id) == str(post_id):
                logger.debug(f"Post found: {post}")
                return post
        return None

    @classmethod
    @ensure_async
    async def update_post(cls, post_id, update_data):
        """
        Update a post by post_id.
        """
        logger.info(f"Updating post {post_id} with data: {update_data}")
        posts = await cls.load_all_posts()
        updated = False
        for post in posts:
            if str(post.post_id) == str(post_id):
                for k, v in update_data.items():
                    setattr(post, k, v)
                updated = True
        if updated:
            await cls.save_all_posts(posts)
            logger.debug(f"Post {post_id} updated.")
        return updated

    @classmethod
    @ensure_async
    async def delete_post(cls, post_id):
        """
        Delete a post by post_id.
        """
        logger.info(f"Deleting post with post_id: {post_id}")
        posts = await cls.load_all_posts()
        new_posts = [post for post in posts if str(post.post_id) != str(post_id)]
        if len(new_posts) != len(posts):
            await cls.save_all_posts(new_posts)
            logger.debug(f"Post {post_id} deleted.")
            return True
        return False
    


    @staticmethod
    @ensure_async
    async def load_all_tasks():
        """
        Load tasks from a JSON or CSV file and return a list of Task objects.
        """
        logger.info("Loading all tasks from file storage.")
        file_path = config.get('filepaths', {}).get('tasks', 'tasks.json')
        def parse_task(task):
            # Ensure action is a dict, status is a string, timestamps are parsed
            action = task.get('action')
            status = task.get('status')
            created_at = task.get('created_at')
            updated_at = task.get('updated_at')
            # Parse timestamps if they are strings
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
            return Task(
                task_id=task.get('task_id'),
                name=task.get('name'),
                post_ids=task.get('post_ids'),
                accounts=task.get('accounts'),
                action=action,
                description=task.get('description'),
                status=status,
                created_at=created_at,
                updated_at=updated_at
            )
        if os.path.exists(file_path):
            if file_path.endswith('.json'):
                with open(file_path, 'r', encoding='utf-8') as file:
                    data = json.load(file)
                    logger.debug(f"Loaded {len(data)} tasks from {file_path}.")
                    return [parse_task(task) for task in data]
            elif file_path.endswith('.csv'):
                df = read_csv(file_path)
                logger.debug(f"Loaded {len(df)} tasks from {file_path} (CSV).")
                tasks = []
                for _, row in df.iterrows():
                    post_ids = eval(row['post_ids']) if isinstance(row['post_ids'], str) else row['post_ids']
                    accounts = eval(row['accounts']) if isinstance(row['accounts'], str) else row['accounts']
                    action = eval(row['action']) if isinstance(row['action'], str) else row['action']
                    status = row.get('status')
                    created_at = row.get('created_at')
                    updated_at = row.get('updated_at')
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
                    task = Task(
                        row['task_id'],
                        row['name'],
                        post_ids,
                        accounts,
                        action,
                        row.get('description'),
                        status,
                        created_at,
                        updated_at
                    )
                    tasks.append(task)
                return tasks
        raise FileNotFoundError(f"No tasks file found at {file_path}.")

    @staticmethod
    @ensure_async
    async def save_all_tasks(tasks):
        """
        Save a list of Task objects to a JSON or CSV file.
        """
        logger.info(f"Saving {len(tasks)} tasks to file storage.")
        from pandas import DataFrame
        import json
        file_path = config.get('filepaths', {}).get('tasks', 'tasks.json')
        data = [task.to_dict(rich=False) for task in tasks]
        if file_path.endswith('.json'):
            with open(file_path, 'w', encoding='utf-8') as file:
                json.dump(data, file, indent=4)
            logger.debug(f"Tasks saved to {file_path} (JSON).")
        elif file_path.endswith('.csv'):
            df = DataFrame(data)
            df.to_csv(file_path, index=False)
            logger.debug(f"Tasks saved to {file_path} (CSV).")
        else:
            raise ValueError("Unsupported file type. Use 'json' or 'csv'.")

    @classmethod
    @ensure_async
    async def add_task(cls, task):
        """
        Add a new task to the tasks file.
        If a task with the same task_id exists, update it instead.
        """
        logger.info(f"Adding task: {task}")
        if isinstance(task, Task):
            task_data = task.to_dict(rich=False)
        else:
            task_data = task
        task_id = task_data.get('task_id')
        tasks = await cls.load_all_tasks()
        if not task_id:
            used_ids = set()
            for t in tasks:
                try:
                    used_ids.add(int(t.task_id))
                except Exception:
                    continue
            task_id = 1
            while task_id in used_ids:
                task_id += 1
            task_data['task_id'] = task_id
        existing_task = await cls.get_task(task_id)
        if existing_task:
            logger.debug(f"Task with task_id {task_id} exists. Updating.")
            return await cls.update_task(task_id, task_data)
        from pandas import Timestamp
        action = task_data.get('action')
        status = task_data.get('status')
        created_at = task_data.get('created_at')
        updated_at = task_data.get('updated_at')
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
            task_id=task_id,
            name=task_data.get('name'),
            post_ids=task_data.get('post_ids'),
            accounts=task_data.get('accounts'),
            action=action,
            description=task_data.get('description'),
            status=status,
            created_at=created_at,
            updated_at=updated_at
        ))
        await cls.save_all_tasks(tasks)
        logger.debug(f"Task with task_id {task_id} added.")
        return True

    @classmethod
    @ensure_async
    async def get_task(cls, task_id):
        """
        Get a task by task_id.
        """
        logger.info(f"Getting task with task_id: {task_id}")
        tasks = await cls.load_all_tasks()
        for task in tasks:
            if str(task.task_id) == str(task_id):
                logger.debug(f"Task found: {task}")
                return task
        return None

    @classmethod
    @ensure_async
    async def update_task(cls, task_id, update_data):
        """
        Update a task by task_id.
        """
        logger.info(f"Updating task {task_id} with data: {update_data}")
        tasks = await cls.load_all_tasks()
        updated = False
        for task in tasks:
            if str(task.task_id) == str(task_id):
                for k, v in update_data.items():
                    setattr(task, k, v)
                updated = True
        if updated:
            await cls.save_all_tasks(tasks)
            logger.debug(f"Task {task_id} updated.")
        return updated

    @classmethod
    @ensure_async
    async def delete_task(cls, task_id):
        """
        Delete a task by task_id.
        """
        logger.info(f"Deleting task with task_id: {task_id}")
        tasks = await cls.load_all_tasks()
        new_tasks = [task for task in tasks if str(task.task_id) != str(task_id)]
        if len(new_tasks) != len(tasks):
            await cls.save_all_tasks(new_tasks)
            logger.debug(f"Task {task_id} deleted.")
            return True
        return False


class MongoStorage(StorageInterface):
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
            cls._db = client["LikeBot"]
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
        cursor = cls._tasks.find()
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





def get_db() -> StorageInterface:
    use_db = config.get('filepaths', {}).get('use_db', False)
    if use_db:
        return MongoStorage()
    else:
        return FileStorage()
