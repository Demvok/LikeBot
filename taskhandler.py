from pandas import Timestamp, read_csv, DataFrame
import json, re, yaml, asyncio
from enum import Enum, auto
from agent import Account, Client
from logger import setup_logger

def load_config():
    with open('config.yaml', 'r', encoding='utf-8') as file:
        return yaml.safe_load(file)
config = load_config()


class Post:
    
    def __init__(self, post_id, message_link, chat_id=None, message_id=None, created_at=None, updated_at=None):
        self.post_id = post_id
        self.chat_id = chat_id
        self.message_id = message_id
        self.message_link = message_link
        self.created_at = created_at or Timestamp.now()
        self.updated_at = updated_at or Timestamp.now()

    def __repr__(self):
        return f"Post({self.post_id}, {'validated' if self.is_validated else 'unvalidated'}, {self.message_link})"


    @property
    def is_validated(self):
        """Check if the post has been validated by checking chat_id and message_id."""
        return self.chat_id is not None and self.message_id is not None

    async def get_chat_id(self, client, file_path=config.get('filepaths', {}).get('posts', 'posts.json')):
        """Get chat_id, auto-validate and update if needed."""
        if not self.is_validated:
            await self.validate(client, file_path)
        return self.chat_id

    async def get_message_id(self, client, file_path=config.get('filepaths', {}).get('posts', 'posts.json')):
        """Get message_id, auto-validate and update if needed."""
        if not self.is_validated:
            await self.validate(client, file_path)
        return self.message_id


    async def validate(self, client, file_path=config.get('filepaths', {}).get('posts', 'posts.json')):
        """Validate the post by fetching its chat_id and message_id, and update the record in file."""
        chat_id, message_id = await self._get_message_ids(client, self.message_link)
        self.chat_id = chat_id
        self.message_id = message_id
        self.updated_at = Timestamp.now()
        # Update the record in file
        type(self).update_post(self, file_path)
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
        # Return only the IDs, not the objects
        return entity.id if hasattr(entity, 'id') else entity, message.id if hasattr(message, 'id') else message

    def to_dict(self):
        """Convert Post object to dictionary with serializable timestamps."""
        return {
            'post_id': self.post_id,
            'chat_id': self.chat_id,
            'message_id': self.message_id,
            'message_link': self.message_link,
            'created_at': self.created_at.isoformat() if isinstance(self.created_at, Timestamp) else self.created_at,
            'updated_at': self.updated_at.isoformat() if isinstance(self.updated_at, Timestamp) else self.updated_at
        }

    @classmethod
    def update_post(cls, updated_post, file_path=config.get('filepaths', {}).get('posts', 'posts.json')):
        """Update a post in the file by post_id."""
        posts = cls.load_posts(file_path)
        for i, post in enumerate(posts):
            if post.post_id == updated_post.post_id:
                posts[i] = updated_post
                break
        else:
            raise ValueError(f"Post with id {updated_post.post_id} not found.")
        cls.save_posts(posts, file_path)

    @classmethod
    def add_post(cls, new_post, file_path=config.get('filepaths', {}).get('posts', 'posts.json')):
        """Add a new post to the file."""
        posts = cls.load_posts(file_path)
        if any(post.post_id == new_post.post_id for post in posts):
            raise ValueError(f"Post with id {new_post.post_id} already exists.")
        posts.append(new_post)
        cls.save_posts(posts, file_path)

    @classmethod
    def delete_post(cls, post_id, file_path=config.get('filepaths', {}).get('posts', 'posts.json')):
        """Delete a post from the file by post_id."""
        posts = cls.load_posts(file_path)
        posts = [post for post in posts if post.post_id != post_id]
        cls.save_posts(posts, file_path)

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
        df = read_csv(file_path)
        posts = [Post(row['post_id'], row['chat_id'], row['message_id'], row['message_link'], row['created_at'], row['updated_at']) for index, row in df.iterrows()]
        return posts

    @classmethod
    def _save_posts_to_csv(cls, posts, file_path):
        """Save posts to a CSV file."""
        df = DataFrame([post.__dict__ for post in posts])
        df.to_csv(file_path, index=False)

    @classmethod
    def load_posts(cls, file_path=config.get('filepaths', {}).get('posts', 'posts.json')):
        """Load posts from a file, either JSON or CSV."""
        if file_path.endswith('.json'):
            return cls._load_posts_from_json(file_path)
        elif file_path.endswith('.csv'):
            return cls._load_posts_from_csv(file_path)
        else:
            raise ValueError("Unsupported file type. Use '.json' or '.csv' file extension.")

    @classmethod
    def save_posts(cls, posts, file_path=config.get('filepaths', {}).get('posts', 'posts.json')):
        """Save posts to a file, either JSON or CSV."""
        if file_path.endswith('.json'):
            cls._save_posts_to_json(posts, file_path)
        elif file_path.endswith('.csv'):
            cls._save_posts_to_csv(posts, file_path)
        else:
            raise ValueError("Unsupported file type. Use '.json' or '.csv' file extension.")

    @classmethod
    async def mass_validate_posts(cls, posts, client, logger=None):
        """Validate multiple posts asynchronously."""
        if logger:
            logger.debug(f"Validating {len(posts)} posts...")
        if not posts:
            if logger:
                logger.warning("No posts to validate.")
            return
        if not isinstance(posts, list):
            raise ValueError("Posts should be a list of Post objects.")
        if not client:
            raise ValueError("Client is not initialized.")
        already_validated, newly_validated = 0, 0
        for post in posts:
            if post.is_validated:
                # if logger:
                    # logger.info(f"Post {post.post_id} is already validated.")
                already_validated += 1
                continue
            post = await post.validate(client=client.client)
            if post.is_validated:
                newly_validated += 1
            # if logger:
                # logger.info(f"Validating post {post.post_id}...")
        if logger:
            logger.info(f"Validated {len(posts)} posts: {newly_validated} newly validated, {already_validated} already validated.")





class Task:

    logger = setup_logger("main", "main.log")

    class TaskStatus(Enum):
        PENDING = auto()
        RUNNING = auto()
        PAUSED = auto()
        FINISHED = auto()
        CRASHED = auto()
        def __str__(self):
            return self.name
        def __repr__(self):
            return self.name

    def __init__(self, task_id, name, post_ids, accounts, action, description=None, status=None, created_at=None, updated_at=None):
        self.task_id = task_id
        self.name = name
        self.description = description
        self.post_ids = post_ids
        self.accounts = accounts
        self.action = action
        self.status = status or Task.TaskStatus.PENDING
        self.created_at = created_at or Timestamp.now()
        self.updated_at = updated_at or Timestamp.now()
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Initially not paused
        self._task = None

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
            'action': self.action,
            'status': self.status.name if isinstance(self.status, Task.TaskStatus) else self.status,
            'created_at': self.created_at.isoformat() if isinstance(self.created_at, Timestamp) else self.created_at,
            'updated_at': self.updated_at.isoformat() if isinstance(self.updated_at, Timestamp) else self.updated_at
        }

    def get_posts(self):
        """Get a list of Post objects from a list of post IDs."""
        return [elem for elem in Post.load_posts() if elem.post_id in self.post_ids]
    
    def get_accounts(self):
        return Account.get_accounts(self.accounts)

    def get_actions(self):
        """Get all actions as a list."""
        return self.action if isinstance(self.action, list) else []

    def get_actions_by_type(self, action_type):
        """Get actions filtered by type (react, comment, etc.)."""
        return [action for action in self.get_actions() if action.get('type') == action_type]

    def get_react_actions(self):  # Possibly useless
        """Get all react actions."""
        return self.get_actions_by_type('react')

    def get_comment_actions(self):  # Possibly useless
        """Get all comment actions."""
        return self.get_actions_by_type('comment')

    def has_action_type(self, action_type):
        """Check if task has a specific action type."""
        return len(self.get_actions_by_type(action_type)) > 0

    def get_reaction_palette_name(self):
        """Get the palette for a specific action type."""
        actions = self.get_actions_by_type('react')
        return actions[0].get('palette') if actions else None

    def get_reaction_emojis(self):
        """Get emojis for a specific action type and palette from config."""
        palette = self.get_reaction_palette_name()
        if palette == 'positive':
            return config.get('reactions_palettes', {}).get('positive', [])
        elif palette == 'negative':
            return config.get('reactions_palettes', {}).get('negative', [])
        else:
            raise ValueError(f"Unknown reaction palette: {palette}")

    async def _run(self):
        try:
            self.status = Task.TaskStatus.RUNNING
            self.updated_at = Timestamp.now()
            self.logger.info(f"Starting task {self.task_id} - {self.name}...")

            accounts = self.get_accounts()
            posts = self.get_posts()

            # Check for pause before connecting clients
            await self._check_pause()
            
            clients = await Client.connect_clients(accounts, self.logger)

            if self.has_action_type('react'):
                current_emojis = self.get_reaction_emojis()  # Get reaction palette

                for client in clients:
                    client.active_emoji_palette = current_emojis

                # Check for pause before validation
                await self._check_pause()
                
                if clients:
                    await Post.mass_validate_posts(posts, clients[0], self.logger)

                # React to posts with pause checking
                for post in posts:
                    await self._check_pause()  # Check pause before each post
                    
                    if post.is_validated:
                        for i, client in enumerate(clients):
                            try:
                                await client.react(post.message_id, post.chat_id)
                                self.logger.debug(f"Client {i} reacted to post {post.post_id}")
                            except Exception as e:
                                self.logger.warning(f"Client {i} failed to react to post {post.post_id}: {e}")
                        self.logger.info(f"Reacted to post {post.post_id} with {self.get_reaction_palette_name()}")

                # Disconnect clients
                for client in clients:
                    try:
                        await client.client.disconnect()
                    except Exception as e:
                        self.logger.warning(f"Error disconnecting client: {e}")

            if self.has_action_type('comment'):
            # Logic to handle comment actions can be added here
                self.logger.info("Comment actions are not implemented yet.")
                # Disconnect clients
                for client in clients:
                    try:
                        await client.client.disconnect()
                    except Exception as e:
                        self.logger.warning(f"Error disconnecting client: {e}")
                pass
            if len(self.get_actions()) == 0:
                # Disconnect clients
                for client in clients:
                    try:
                        await client.client.disconnect()
                    except Exception as e:
                        self.logger.warning(f"Error disconnecting client: {e}")
                raise ValueError("No actions defined for the task.")
            
            self.status = Task.TaskStatus.FINISHED
            self.updated_at = Timestamp.now()
            self.logger.info(f"Task {self.task_id} completed successfully.")
        except asyncio.CancelledError:
            self.logger.info(f"Task {self.task_id} was cancelled.")
            self.status = Task.TaskStatus.FINISHED
            # Disconnect clients
            for client in clients:
                try:
                    await client.client.disconnect()
                except Exception as e:
                    self.logger.warning(f"Error disconnecting client: {e}")

            self.updated_at = Timestamp.now()
        except Exception as e:
            self.logger.error(f"Error starting task {self.task_id}: {e}")
            self.status = Task.TaskStatus.CRASHED
            # Disconnect clients
            for client in clients:
                try:
                    await client.client.disconnect()
                except Exception as e:
                    self.logger.warning(f"Error disconnecting client: {e}")

            self.updated_at = Timestamp.now()
            raise e


    async def start(self):
        """Start the task."""
        if self._task is None or self._task.done():
            self._pause_event.set()
            self._task = asyncio.create_task(self._run())
            self.status = Task.TaskStatus.RUNNING       

    async def pause(self):
        """Pause the task."""
        if self.status == Task.TaskStatus.RUNNING:
            self._pause_event.clear()
            self.status = Task.TaskStatus.PAUSED  # Add this line
            self.logger.info(f"Task {self.task_id} paused.")

    async def resume(self):
        """Resume the task."""
        if self.status == Task.TaskStatus.PAUSED:
            self._pause_event.set()
            self.status = Task.TaskStatus.RUNNING  # Add this line
            self.logger.info(f"Task {self.task_id} resumed.")

    async def _check_pause(self):
        """Check if task should be paused and wait if needed."""
        if not self._pause_event.is_set():
            self.status = Task.TaskStatus.PAUSED
            self.logger.info(f"Task {self.task_id} is paused, waiting to resume...")
            await self._pause_event.wait()
            self.status = Task.TaskStatus.RUNNING
            self.logger.info(f"Task {self.task_id} resumed.")

    async def get_status(self):
        """Get the current status of the task."""
        # Additional info gathering logic can be added here
        # Will be used to create reports
        return self.status
    


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
        df = read_csv(file_path)
        tasks = []
        for index, row in df.iterrows():
            # Convert string representations back to lists/objects if needed
            post_ids = eval(row['post_ids']) if isinstance(row['post_ids'], str) else row['post_ids']
            accounts = eval(row['accounts']) if isinstance(row['accounts'], str) else row['accounts']
            action = eval(row['action']) if isinstance(row['action'], str) else row['action']  # Changed from reaction to action
            
            task = cls(
                row['task_id'], 
                row['name'], 
                post_ids,
                accounts, 
                action,
                row['description'], 
                row['status'], 
                row['created_at'], 
                row['updated_at']
            )
            tasks.append(task)
        return tasks

    @classmethod
    def _save_tasks_to_csv(cls, tasks, file_path):
        """Save tasks to a CSV file."""
        df = DataFrame([task.__dict__ for task in tasks])
        df.to_csv(file_path, index=False)

    @classmethod
    def load_tasks(cls, file_path=config.get('filepaths', {}).get('tasks', 'tasks.json')):
        """Load tasks from a file, either JSON or CSV."""
        if file_path.endswith('.json'):
            return cls._load_tasks_from_json(file_path)
        elif file_path.endswith('.csv'):
            return cls._load_tasks_from_csv(file_path)
        else:
            raise ValueError("Unsupported file type. Use '.json' or '.csv' file extension.")
        
    @classmethod
    def save_tasks(cls, tasks, file_path=config.get('filepaths', {}).get('tasks', 'tasks.json')):
        """Save tasks to a file, either JSON or CSV."""
        if file_path.endswith('.json'):
            cls._save_tasks_to_json(tasks, file_path)
        elif file_path.endswith('.csv'):
            cls._save_tasks_to_csv(tasks, file_path)
        else:
            raise ValueError("Unsupported file type. Use '.json' or '.csv' file extension.")


