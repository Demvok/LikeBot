import uuid, os, yaml, asyncio, inspect
from abc import ABC, abstractmethod
from functools import wraps
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from logger import setup_logger
from agent import Account
from taskhandler import Post, Task

load_dotenv()
db_url = os.getenv('db_url')

def load_config():
    with open('config.yaml', 'r', encoding='utf-8') as file:
        return yaml.safe_load(file)
config = load_config()

logger = setup_logger("DB", "main.log")

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
        from pandas import read_csv
        import json
        file_path = config.get('filepaths', {}).get('accounts', 'accounts.json')
        if os.path.exists(file_path):
            if file_path.endswith('.json'):
                with open(file_path, 'r', encoding='utf-8') as file:
                    accounts = json.load(file)
                    return [Account(account) for account in accounts]
            elif file_path.endswith('.csv'):
                df = read_csv(file_path)
                return [Account(row.to_dict()) for _, row in df.iterrows()]
        raise FileNotFoundError(f"No accounts file found at {file_path}.")

    @staticmethod
    @ensure_async
    async def save_all_accounts(accounts):
        """
        Save a list of Account objects to a JSON or CSV file.
        """
        from pandas import DataFrame
        import json
        file_path = config.get('filepaths', {}).get('accounts', 'accounts.json')
        data = [acc.to_dict() for acc in accounts]
        if file_path.endswith('.json'):
            with open(file_path, 'w', encoding='utf-8') as file:
                json.dump(data, file, indent=4)
        elif file_path.endswith('.csv'):
            df = DataFrame(data)
            df.to_csv(file_path, index=False)
        else:
            raise ValueError("Unsupported file type. Use 'json' or 'csv'.")

    @classmethod
    @ensure_async
    async def add_account(cls, account_data):
        """
        Add a new account to the accounts file.
        If an account with the same phone number exists, update it instead.
        """
        if isinstance(account_data, Account):
            account_data = account_data.to_dict()
        phone_number = account_data.get('phone_number')
        if await cls.get_account(phone_number):
            return await cls.update_account(phone_number, account_data)
        else:
            accounts = await cls.load_all_accounts()
            accounts.append(Account(account_data))
            await cls.save_all_accounts(accounts)
            return True

    @classmethod
    @ensure_async
    async def get_account(cls, phone_number):
        """
        Read an account by phone number.
        """
        accounts = await cls.load_all_accounts()
        for acc in accounts:
            if acc.phone_number == phone_number:
                return acc
        return None

    @classmethod
    @ensure_async
    async def update_account(cls, phone_number, update_data):
        """
        Update an account by phone number.
        """
        accounts = await cls.load_all_accounts()
        updated = False
        for acc in accounts:
            if acc.phone_number == phone_number:
                for k, v in update_data.items():
                    setattr(acc, k, v)
                updated = True
        if updated:
            await cls.save_all_accounts(accounts)
        return updated

    @classmethod
    @ensure_async
    async def delete_account(cls, phone_number):
        """
        Delete an account by phone number.
        """
        accounts = await cls.load_all_accounts()
        new_accounts = [acc for acc in accounts if acc.phone_number != phone_number]
        if len(new_accounts) != len(accounts):
            await cls.save_all_accounts(new_accounts)
            return True
        return False



    @staticmethod
    @ensure_async
    async def load_all_posts():
        """
        Load posts from a JSON or CSV file and return a list of Post objects.
        """
        from pandas import read_csv
        import json
        file_path = config.get('filepaths', {}).get('posts', 'posts.json')
        if os.path.exists(file_path):
            if file_path.endswith('.json'):
                with open(file_path, 'r', encoding='utf-8') as file:
                    data = json.load(file)
                    # Only pass allowed keys to Post constructor
                    allowed_keys = {'post_id', 'chat_id', 'message_id', 'message_link', 'created_at', 'updated_at'}
                    return [Post(**{k: v for k, v in post.items() if k in allowed_keys}) for post in data]
            elif file_path.endswith('.csv'):
                df = read_csv(file_path)
                return [Post(row['post_id'], row['message_link'], row.get('chat_id'), row.get('message_id'), row.get('created_at'), row.get('updated_at')) for _, row in df.iterrows()]
        raise FileNotFoundError(f"No posts file found at {file_path}.")

    @staticmethod
    @ensure_async
    async def save_all_posts(posts):
        """
        Save a list of Post objects to a JSON or CSV file.
        """
        from pandas import DataFrame
        import json
        file_path = config.get('filepaths', {}).get('posts', 'posts.json')
        data = [post.to_dict() for post in posts]
        if file_path.endswith('.json'):
            with open(file_path, 'w', encoding='utf-8') as file:
                json.dump(data, file, indent=4)
        elif file_path.endswith('.csv'):
            df = DataFrame(data)
            df.to_csv(file_path, index=False)
        else:
            raise ValueError("Unsupported file type. Use 'json' or 'csv'.")

    @classmethod
    @ensure_async
    async def add_post(cls, post):
        """
        Add a new post to the posts file.
        If a post with the same post_id exists, update it instead.
        """
        if isinstance(post, Post):
            post_data = post.to_dict()
        else:
            post_data = post
        post_id = post_data.get('post_id')
        existing_post = await cls.get_post(post_id)
        if existing_post:
            return await cls.update_post(post_id, post_data)
        posts = await cls.load_all_posts()
        posts.append(Post(**post_data))
        await cls.save_all_posts(posts)
        return True

    @classmethod
    @ensure_async
    async def get_post(cls, post_id):
        """
        Get a post by post_id.
        """
        posts = await cls.load_all_posts()
        for post in posts:
            if str(post.post_id) == str(post_id):
                return post
        return None

    @classmethod
    @ensure_async
    async def update_post(cls, post_id, update_data):
        """
        Update a post by post_id.
        """
        posts = await cls.load_all_posts()
        updated = False
        for post in posts:
            if str(post.post_id) == str(post_id):
                for k, v in update_data.items():
                    setattr(post, k, v)
                updated = True
        if updated:
            await cls.save_all_posts(posts)
        return updated

    @classmethod
    @ensure_async
    async def delete_post(cls, post_id):
        """
        Delete a post by post_id.
        """
        posts = await cls.load_all_posts()
        new_posts = [post for post in posts if str(post.post_id) != str(post_id)]
        if len(new_posts) != len(posts):
            await cls.save_all_posts(new_posts)
            return True
        return False
    


    @staticmethod
    @ensure_async
    async def load_all_tasks():
        """
        Load tasks from a JSON or CSV file and return a list of Task objects.
        """
        from pandas import read_csv
        import json
        file_path = config.get('filepaths', {}).get('tasks', 'tasks.json')
        if os.path.exists(file_path):
            if file_path.endswith('.json'):
                with open(file_path, 'r', encoding='utf-8') as file:
                    data = json.load(file)
                    return [Task(**task) for task in data]
            elif file_path.endswith('.csv'):
                df = read_csv(file_path)
                tasks = []
                for _, row in df.iterrows():
                    post_ids = eval(row['post_ids']) if isinstance(row['post_ids'], str) else row['post_ids']
                    accounts = eval(row['accounts']) if isinstance(row['accounts'], str) else row['accounts']
                    action = eval(row['action']) if isinstance(row['action'], str) else row['action']
                    task = Task(
                        row['task_id'],
                        row['name'],
                        post_ids,
                        accounts,
                        action,
                        row.get('description'),
                        row.get('status'),
                        row.get('created_at'),
                        row.get('updated_at')
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
        from pandas import DataFrame
        import json
        file_path = config.get('filepaths', {}).get('tasks', 'tasks.json')
        data = [task.to_dict() for task in tasks]
        if file_path.endswith('.json'):
            with open(file_path, 'w', encoding='utf-8') as file:
                json.dump(data, file, indent=4)
        elif file_path.endswith('.csv'):
            df = DataFrame(data)
            df.to_csv(file_path, index=False)
        else:
            raise ValueError("Unsupported file type. Use 'json' or 'csv'.")

    @classmethod
    @ensure_async
    async def add_task(cls, task):
        """
        Add a new task to the tasks file.
        If a task with the same task_id exists, update it instead.
        """
        if isinstance(task, Task):
            task_data = task.to_dict()
        else:
            task_data = task
        task_id = task_data.get('task_id')
        existing_task = await cls.get_task(task_id)
        if existing_task:
            return await cls.update_task(task_id, task_data)
        tasks = await cls.load_all_tasks()
        tasks.append(Task(**task_data))
        await cls.save_all_tasks(tasks)
        return True

    @classmethod
    @ensure_async
    async def get_task(cls, task_id):
        """
        Get a task by task_id.
        """
        tasks = await cls.load_all_tasks()
        for task in tasks:
            if str(task.task_id) == str(task_id):
                return task
        return None

    @classmethod
    @ensure_async
    async def update_task(cls, task_id, update_data):
        """
        Update a task by task_id.
        """
        tasks = await cls.load_all_tasks()
        updated = False
        for task in tasks:
            if str(task.task_id) == str(task_id):
                for k, v in update_data.items():
                    setattr(task, k, v)
                updated = True
        if updated:
            await cls.save_all_tasks(tasks)
        return updated

    @classmethod
    @ensure_async
    async def delete_task(cls, task_id):
        """
        Delete a task by task_id.
        """
        tasks = await cls.load_all_tasks()
        new_tasks = [task for task in tasks if str(task.task_id) != str(task_id)]
        if len(new_tasks) != len(tasks):
            await cls.save_all_tasks(new_tasks)
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
            client = AsyncIOMotorClient(db_url)
            cls._db = client["LikeBot"]
            cls._accounts = cls._db["accounts"]
            cls._posts = cls._db["posts"]
            cls._tasks = cls._db["tasks"]

    @classmethod
    @ensure_async
    async def load_all_accounts(cls):
        cls._init()
        cursor = cls._accounts.find()
        accounts = []
        async for acc in cursor:
            acc.pop('_id', None)
            accounts.append(Account(acc))
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
        if hasattr(account_data, 'to_dict'):
            account_data = account_data.to_dict()
        phone_number = account_data.get('phone_number')
        existing_account = await cls.get_account(phone_number)
        if existing_account:
            return await cls.update_account(phone_number, account_data)
        account_data.pop('_id', None)
        await cls._accounts.insert_one(account_data)
        return True

    @classmethod
    @ensure_async
    async def get_account(cls, phone_number):
        cls._init()
        acc = await cls._accounts.find_one({"phone_number": phone_number})
        if acc and '_id' in acc:
            acc.pop('_id')
        return Account(acc) if acc else None

    @classmethod
    @ensure_async
    async def update_account(cls, phone_number, update_data):
        cls._init()
        if hasattr(phone_number, 'phone_number'):
            phone_number = phone_number.phone_number
        if hasattr(update_data, 'to_dict'):
            update_data = update_data.to_dict()
        elif isinstance(update_data, str):
            update_data = {'value': update_data}
        elif not isinstance(update_data, dict):
            raise ValueError(f"update_data must be a dict mapping field names to values, got {type(update_data)}: {update_data}")
        update_data.pop('_id', None)
        result = await cls._accounts.update_one({"phone_number": phone_number}, {"$set": update_data})
        return result.modified_count > 0

    @classmethod
    @ensure_async
    async def delete_account(cls, phone_number):
        cls._init()
        if hasattr(phone_number, 'phone_number'):
            phone_number = phone_number.phone_number
        result = await cls._accounts.delete_one({"phone_number": phone_number})
        return result.deleted_count > 0

    # --- Post methods ---
    @classmethod
    @ensure_async
    async def load_all_posts(cls):
        cls._init()
        cursor = cls._posts.find()
        posts = []
        async for post in cursor:
            post.pop('_id', None)
            posts.append(Post(**post))
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
        if hasattr(post, 'to_dict'):
            post = post.to_dict()
        post_id = post.get('post_id')
        existing_post = await cls.get_post(post_id)
        if existing_post:
            return await cls.update_post(post_id, post)
        post.pop('_id', None)
        await cls._posts.insert_one(post)
        return True

    @classmethod
    @ensure_async
    async def get_post(cls, post_id):
        cls._init()
        post = await cls._posts.find_one({"post_id": post_id})
        if post and '_id' in post:
            post.pop('_id')
        return Post(**post) if post else None

    @classmethod
    @ensure_async
    async def update_post(cls, post_id, update_data):
        cls._init()
        if hasattr(post_id, 'post_id'):
            post_id = post_id.post_id
        if hasattr(update_data, 'to_dict'):
            update_data = update_data.to_dict()
        elif isinstance(update_data, str):
            update_data = {'value': update_data}
        elif not isinstance(update_data, dict):
            raise ValueError(f"update_data must be a dict mapping field names to values, got {type(update_data)}: {update_data}")
        update_data.pop('_id', None)
        result = await cls._posts.update_one({"post_id": post_id}, {"$set": update_data})
        return result.modified_count > 0

    @classmethod
    @ensure_async
    async def delete_post(cls, post_id):
        cls._init()
        if hasattr(post_id, 'post_id'):
            post_id = post_id.post_id
        result = await cls._posts.delete_one({"post_id": post_id})
        return result.deleted_count > 0

    # --- Task methods ---
    @classmethod
    @ensure_async
    async def load_all_tasks(cls):
        cls._init()
        cursor = cls._tasks.find()
        tasks = []
        async for task in cursor:
            task.pop('_id', None)
            tasks.append(Task(**task))
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
        if hasattr(task, 'to_dict'):
            task = task.to_dict()
        task_id = task.get('task_id')
        existing_task = await cls.get_task(task_id)
        if existing_task:
            return await cls.update_task(task_id, task)
        await cls._tasks.insert_one(task)
        return True

    @classmethod
    @ensure_async
    async def get_task(cls, task_id):
        cls._init()
        task = await cls._tasks.find_one({"task_id": task_id})
        if task and '_id' in task:
            task.pop('_id')
        return Task(**task) if task else None

    @classmethod
    @ensure_async
    async def update_task(cls, task_id, update_data):
        cls._init()
        if hasattr(task_id, 'task_id'):
            task_id = task_id.task_id
        if hasattr(update_data, 'to_dict'):
            update_data = update_data.to_dict()
        elif isinstance(update_data, str):
            update_data = {'value': update_data}
        elif not isinstance(update_data, dict):
            raise ValueError(f"update_data must be a dict mapping field names to values, got {type(update_data)}: {update_data}")
        update_data.pop('_id', None)
        result = await cls._tasks.update_one({"task_id": task_id}, {"$set": update_data})
        return result.modified_count > 0

    @classmethod
    @ensure_async
    async def delete_task(cls, task_id):
        cls._init()
        if hasattr(task_id, 'task_id'):
            task_id = task_id.task_id
        result = await cls._tasks.delete_one({"task_id": task_id})
        return result.deleted_count > 0





def get_db() -> StorageInterface:
    if config.get('filepaths', {}).get('use_db', False):
        return MongoStorage()
    else:
        return FileStorage()
