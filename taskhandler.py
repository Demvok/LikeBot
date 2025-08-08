import pandas as pd
import json
import re
from enum import Enum, auto
from agent import Account

class Post:
    def __init__(self, post_id, message_link, chat_id=None, message_id=None, created_at=None, updated_at=None):
        self.post_id = post_id
        self.chat_id = chat_id
        self.message_id = message_id
        self.message_link = message_link
        self.created_at = created_at or pd.Timestamp.now()
        self.updated_at = updated_at or pd.Timestamp.now()

    def __repr__(self):
        return f"Post({self.post_id}, {'validated' if self.is_validated else 'unvalidated'}, {self.message_link})"

    @property
    def is_validated(self):
        """Check if the post has been validated by checking chat_id and message_id."""
        return self.chat_id is not None and self.message_id is not None

    async def validate(self, client):
        """Validate the post by fetching its chat_id and message_id."""            
        chat_id, message_id = await self._get_message_ids(client, self.message_link)
        self.chat_id = chat_id
        self.message_id = message_id
        self.updated_at = pd.Timestamp.now()
        return self

    async def _get_message_ids(self, client, link):
        # Example link: https://t.me/c/123456789/12345 or https://t.me/username/12345
        match = re.match(r'https://t\.me/(c/)?([\w\d_]+)/(\d+)', link)
        if not match:
            raise ValueError("Invalid Telegram message link format.")

        is_private = match.group(1) == 'c/'
        chat_part = match.group(2)
        message_id = int(match.group(3))

        if is_private:
            # For private groups/channels, chat_id is -100 + chat_part
            chat_id = int(f"-100{chat_part}")
        else:
            # For public, chat_part is username
            chat_id = chat_part

        # Get entity and message using TelegramClient
        entity = await client.get_entity(chat_id)
        message = await client.get_messages(entity, ids=message_id)
        return entity, message

    def to_dict(self):
        """Convert Post object to dictionary with serializable timestamps."""
        return {
            'post_id': self.post_id,
            'chat_id': self.chat_id,
            'message_id': self.message_id,
            'message_link': self.message_link,
            'created_at': self.created_at.isoformat() if isinstance(self.created_at, pd.Timestamp) else self.created_at,
            'updated_at': self.updated_at.isoformat() if isinstance(self.updated_at, pd.Timestamp) else self.updated_at
        }

    @classmethod
    def _load_posts_from_json(cls, file_path):
        """Load posts from a JSON file."""
        with open(file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
            posts = [Post(**post) for post in data]
        return posts

    @classmethod
    def _save_posts_to_json(cls, posts, file_path):
        """Save posts to a JSON file."""
        with open(file_path, 'w', encoding='utf-8') as file:
            json.dump([post.to_dict() for post in posts], file, indent=4)

    @classmethod
    def _load_posts_from_csv(cls, file_path):
        """Load posts from a CSV file."""
        df = pd.read_csv(file_path)
        posts = [Post(row['post_id'], row['chat_id'], row['message_id'], row['message_link'], row['created_at'], row['updated_at']) for index, row in df.iterrows()]
        return posts

    @classmethod
    def _save_posts_to_csv(cls, posts, file_path):
        """Save posts to a CSV file."""
        df = pd.DataFrame([post.__dict__ for post in posts])
        df.to_csv(file_path, index=False)

    @classmethod
    def load_posts(cls, file_path='posts.json', file_type='json'):
        """Load posts from a file, either JSON or CSV."""
        if file_type == 'json':
            return cls._load_posts_from_json(file_path)
        elif file_type == 'csv':
            return cls._load_posts_from_csv(file_path)
        else:
            raise ValueError("Unsupported file type. Use 'json' or 'csv'.")

    @classmethod
    def save_posts(cls, posts, file_path='posts.json', file_type='json'):
        """Save posts to a file, either JSON or CSV."""
        if file_type == 'json':
            cls._save_posts_to_json(posts, file_path)
        elif file_type == 'csv':
            cls._save_posts_to_csv(posts, file_path)
        else:
            raise ValueError("Unsupported file type. Use 'json' or 'csv'.")

    @classmethod
    async def mass_validate_posts(cls, posts, client, logger=None):
        for post in posts:
            if post.is_validated:
                if logger:
                    logger.info(f"Post {post.post_id} is already validated.")
                continue
            post = await post.validate(client=client)
            if logger:
                logger.info(f"Validating post {post.post_id}...")


class Task:

    class TaskStatus(Enum):
        PENDING = auto()
        RUNNING = auto()
        FINISHED = auto()
        CRASHED = auto()

    def __init__(self, task_id, name, post_ids, accounts, reaction, description=None, status=None, created_at=None, updated_at=None):
        self.task_id = task_id
        self.name = name
        self.description = description
        self.post_ids = post_ids
        self.accounts = accounts
        self.reaction = reaction
        self.status = status or Task.TaskStatus.PENDING
        self.created_at = created_at or pd.Timestamp.now()
        self.updated_at = updated_at or pd.Timestamp.now()

    def __repr__(self):
        return f"Task({self.task_id}, {self.name}, {self.status}, {self.created_at}, {self.updated_at})"
    
    def __str__(self):
        return f"Task: {self.name} (ID: {self.task_id})"

    def to_dict(self, rich=False):
        """Convert Task object to dictionary with serializable timestamps."""
        return {
            'task_id': self.task_id,
            'name': self.name,
            'description': self.description,
            'post_ids': self.post_ids if not rich else self.get_posts(),
            'accounts': self.accounts if not rich else self.get_accounts(),
            'reaction': self.reaction,
            'status': self.status.name if isinstance(self.status, Task.TaskStatus) else self.status,
            'created_at': self.created_at.isoformat() if isinstance(self.created_at, pd.Timestamp) else self.created_at,
            'updated_at': self.updated_at.isoformat() if isinstance(self.updated_at, pd.Timestamp) else self.updated_at
        }


    async def start(self):
        """Start the task."""
        self.status = 'in_progress'
        self.updated_at = pd.Timestamp.now()
        # Logic to start the task goes here

    async def pause(self):
        """Pause the task."""
        self.status = 'paused'
        self.updated_at = pd.Timestamp.now()
        # Logic to pause the task goes here

    async def stop(self):
        """Stop the task."""
        self.status = 'stopped'
        self.updated_at = pd.Timestamp.now()
        # Logic to stop the task goes here
    
    async def get_status(self):
        """Get the current status of the task."""
        # Additional info gathering logic can be added here
        # Will be used to create reports
        return self.status
    
    def get_posts(self):
        """Get a list of Post objects from a list of post IDs."""
        return [elem for elem in Post.load_posts() if elem.post_id in self.post_ids]
    
    def get_accounts(self):
        return Account.get_accounts(self.accounts)

    @classmethod
    def _load_tasks_from_json(cls, file_path):
        """Load tasks from a JSON file."""
        with open(file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
            tasks = [cls(**task) for task in data]
        return tasks

    @classmethod
    def _save_tasks_to_json(cls, tasks, file_path):
        """Save tasks to a JSON file."""
        with open(file_path, 'w', encoding='utf-8') as file:
            json.dump([task.to_dict() for task in tasks], file, indent=4)

    @classmethod
    def _load_tasks_from_csv(cls, file_path):
        """Load tasks from a CSV file."""
        df = pd.read_csv(file_path)
        tasks = []
        for index, row in df.iterrows():
            # Convert string representations back to lists/objects if needed
            post_ids = eval(row['post_ids']) if isinstance(row['post_ids'], str) else row['post_ids']
            accounts = eval(row['accounts']) if isinstance(row['accounts'], str) else row['accounts']
            
            task = cls(
                row['task_id'], 
                row['name'], 
                row['description'], 
                post_ids, 
                accounts, 
                row['reaction'], 
                row['status'], 
                row['created_at'], 
                row['updated_at']
            )
            tasks.append(task)
        return tasks

    @classmethod
    def _save_tasks_to_csv(cls, tasks, file_path):
        """Save tasks to a CSV file."""
        df = pd.DataFrame([task.__dict__ for task in tasks])
        df.to_csv(file_path, index=False)

    @classmethod
    def load_tasks(cls, file_path='tasks.json', file_type='json'):
        """Load tasks from a file, either JSON or CSV."""
        if file_type == 'json':
            return cls._load_tasks_from_json(file_path)
        elif file_type == 'csv':
            return cls._load_tasks_from_csv(file_path)
        else:
            raise ValueError("Unsupported file type. Use 'json' or 'csv'.")
        
    @classmethod
    def save_tasks(cls, tasks, file_path='tasks.json', file_type='json'):
        """Save tasks to a file, either JSON or CSV."""
        if file_type == 'json':
            cls._save_tasks_to_json(tasks, file_path)
        elif file_type == 'csv':
            cls._save_tasks_to_csv(tasks, file_path)
        else:
            raise ValueError("Unsupported file type. Use 'json' or 'csv'.")
        
    
