from pandas import Timestamp
import re, yaml, asyncio
from enum import Enum, auto
from agent import Account, Client
from logger import setup_logger

def load_config():
    with open('config.yaml', 'r', encoding='utf-8') as file:
        return yaml.safe_load(file)
config = load_config()


class Post:

    def __init__(self, post_id:int, message_link:str, chat_id:int=None, message_id:int=None, created_at=None, updated_at=None):
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
    def from_keys(cls, post_id:int, message_link:str, chat_id:int=None, message_id:int=None):
        """Create a Post object from keys."""
        return cls(
            post_id=post_id,
            message_link=message_link,
            chat_id=chat_id,
            message_id=message_id
        )


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
        entity = await client.client.get_entity(chat_id)
        message = await client.client.get_messages(entity, ids=message_id)
        # Return only the IDs, not the objects
        return entity.id if hasattr(entity, 'id') else entity, message.id if hasattr(message, 'id') else message



    async def validate(self, client):
        """Validate the post by fetching its chat_id and message_id, and update the record in file."""
        from database import get_db
        db = get_db()
        chat_id, message_id = await self._get_message_ids(client, self.message_link)
        self.chat_id = chat_id
        self.message_id = message_id
        self.updated_at = Timestamp.now()
        db.update_post(self)
        return self
    
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
                already_validated += 1
                continue
            post = await post.validate(client=client.client)
            if post.is_validated:
                newly_validated += 1
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
        self._clients = None  # Store connected clients for pause/resume

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

# Property methods

    def get_posts(self):
        """Get a list of Post objects from a list of post IDs."""
        from database import get_db
        db = get_db()
        return [elem for elem in db.load_all_posts() if elem.post_id in self.post_ids]
    
    def get_accounts(self):
        return Account.get_accounts(self.accounts)

    def get_actions(self):
        """Get all actions as a list."""
        return self.action if isinstance(self.action, list) else []

    def get_actions_by_type(self, action_type):
        """Get actions filtered by type (react, comment, etc.)."""
        return [action for action in self.get_actions() if action.get('type') == action_type]

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

# Actions

    async def _run(self):
        try:
            accounts = self.get_accounts()
            posts = self.get_posts()

            await self._check_pause()  # Check for pause before connecting clients
            self._clients = await Client.connect_clients(accounts, self.logger)

            if self.has_action_type('react'):  # Iterate tasks !!!

                current_emojis = self.get_reaction_emojis()  # Get and set reaction palette  TO FIX!!!
                for client in self._clients:
                    client.active_emoji_palette = current_emojis

                await self._check_pause()  # Check for pause before validation
                if self._clients:
                    await Post.mass_validate_posts(posts, self._clients[0], self.logger)


                for post in posts:  # TO REVIEW, should be async
                    await self._check_pause()  # Check pause before each post
                    if post.is_validated:
                        for i, client in enumerate(self._clients):
                            try:
                                await client.react(post.message_id, post.chat_id)
                                self.logger.debug(f"Client {i} reacted to post {post.post_id}")
                            except Exception as e:
                                self.logger.warning(f"Client {i} failed to react to post {post.post_id}: {e}")
                        self.logger.info(f"All clients have reacted to post {post.post_id} with {self.get_reaction_palette_name()}")


            if self.has_action_type('comment'):  # Logic to handle comment actions can be added here
                self.logger.info("Comment actions are not implemented yet.")
                pass


            if len(self.get_actions()) == 0:
                raise ValueError("No actions defined for the task.")


            # If you get here - task succeeded
            self.logger.info(f"Task {self.task_id} completed successfully.")
            self.status = Task.TaskStatus.FINISHED

        except asyncio.CancelledError:
            self.logger.info(f"Task {self.task_id} was cancelled.")
            self.status = Task.TaskStatus.PENDING
        except Exception as e:
            self.logger.error(f"Error starting task {self.task_id}: {e}")
            self.status = Task.TaskStatus.CRASHED
            raise e
        finally:
            self._clients = await Client.disconnect_clients(self._clients, self.logger)
            self.updated_at = Timestamp.now()



    async def start(self):
        """Start the task."""
        if self._task is None or self._task.done():
            self._pause_event.set()
            self.logger.info(f"Starting task {self.task_id} - {self.name}...")
            self._task = asyncio.create_task(self._run())
            self.updated_at = Timestamp.now()
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
        """Check if task should be paused and wait if needed. Disconnect clients on pause, reconnect on resume."""
        if not self._pause_event.is_set():
            self.status = Task.TaskStatus.PAUSED
            self.logger.info(f"Task {self.task_id} is paused, disconnecting clients and waiting to resume...")
            # Disconnect clients if connected
            if self._clients:
                for client in self._clients:
                    try:
                        await client.client.disconnect()
                    except Exception as e:
                        self.logger.warning(f"Error disconnecting client: {e}")
                self._clients = None
            await self._pause_event.wait()
            self.status = Task.TaskStatus.RUNNING
            self.logger.info(f"Task {self.task_id} resumed. Reconnecting clients...")
            # Reconnect clients
            accounts = self.get_accounts()
            self._clients = await Client.connect_clients(accounts, self.logger)


    async def get_status(self):
        """Get the current status of the task."""
        # Additional info gathering logic can be added here
        # Will be used to create reports
        return self.status
    