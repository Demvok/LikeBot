from pandas import Timestamp
import re, yaml, asyncio, datetime
from enum import Enum, auto
from agent import Account, Client
from reporter import Reporter
from logger import setup_logger, load_config

config = load_config()


class Post:

    def __init__(self, message_link:str, post_id:int=None, chat_id:int=None, message_id:int=None, created_at=None, updated_at=None):
        self.post_id = post_id
        self.chat_id = chat_id
        self.message_id = message_id
        self.message_link = message_link
        self.created_at = created_at or Timestamp.now()
        self.updated_at = updated_at or Timestamp.now()

    def __repr__(self):
        return f"Post({self.post_id if self.post_id else 'unassigned'}, {'validated' if self.is_validated else 'unvalidated'}, {self.message_link})"

    @property
    def is_validated(self):
        """Check if the post has been validated by checking chat_id and message_id, and updated within 1 day."""
        if self.chat_id is None or self.message_id is None:
            return False
        # Check if updated_at is within 1 day from now
        now = Timestamp.now()
        if isinstance(self.updated_at, Timestamp):
            delta = now - self.updated_at
            return delta.days <= 1
        elif isinstance(self.updated_at, str):
            delta = now - Timestamp(self.updated_at)
            return delta.days <= 1
        elif isinstance(self.updated_at, datetime.datetime):
            delta = now.to_pydatetime() - self.updated_at
            return delta.days <= 1
        return False

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

    async def validate(self, client, logger=None):
        """Validate the post by fetching its chat_id and message_id, and update the record in file."""
        from database import get_db
        db = get_db()
        retries = config.get('delays', {}).get('action_retries', 5)
        delay = config.get('delays', {}).get('action_retry_delay', 3)
        attempt = 0
        while attempt < retries:
            try:
                chat_id, message_id = await self._get_message_ids(client, self.message_link)
                self.chat_id = chat_id
                self.message_id = message_id
                self.updated_at = Timestamp.now()
                await db.update_post(self.post_id, {
                    'chat_id': self.chat_id,
                    'message_id': self.message_id,
                    'updated_at': str(self.updated_at)
                })
                break  # If you got here - task succeeded
            except Exception as e:
                attempt += 1
                if attempt < retries:
                    await asyncio.sleep(delay)
                elif logger:
                    logger.error(f"Failed to validate post {self.post_id} after {retries} attempts.")
                    raise e
        return self
    
    @classmethod
    async def mass_validate_posts(cls, posts, client, logger=None):
        """Validate multiple posts asynchronously."""
        if logger:
            logger.info(f"Validating {len(posts)} posts...")
        if not posts:
            if logger:
                logger.warning("No posts to validate.")
            return
        if not isinstance(posts, list):
            raise ValueError("Posts should be a list of Post objects.")
        if not client:
            raise ValueError("Client is not initialized.")
        from database import get_db
        db = get_db()
        already_validated, newly_validated, failed_validation = 0, 0, 0
        new_posts = []
        for post in posts:
            try:               
                if post.is_validated:
                    already_validated += 1
                    new_posts.append(post)
                    continue
                
                if not post.is_validated:
                    await post.validate(client=client, logger=logger)
                    new_post = await db.get_post(post.post_id)
                    new_posts.append(new_post)

                    if new_post.is_validated:
                        newly_validated += 1
                        continue
    
                    failed_validation += 1
                    continue
                    
                if logger:
                    logger.warning(f"Post {post.post_id} failed validation after validate(). State: chat_id={post.chat_id}, message_id={post.message_id}, updated_at={post.updated_at}")

            except Exception as e:
                if logger:
                    logger.error(f"Exception during validation of post {getattr(post, 'post_id', None)}: {e}")
        
        if logger:
            logger.info(f"Validated {len(posts)} posts: {newly_validated} newly validated, {already_validated} already validated, {failed_validation} failed validation.")
        
        return new_posts



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

    def to_dict(self):
        """Convert Task object to dictionary with serializable timestamps."""
        return {
            'task_id': self.task_id,
            'name': self.name,
            'description': self.description,
            'post_ids': self.post_ids,
            'accounts': self.accounts,
            'action': self.action,
            'status': self.status.name if isinstance(self.status, Task.TaskStatus) else self.status,
            'created_at': self.created_at.isoformat() if isinstance(self.created_at, Timestamp) else self.created_at,
            'updated_at': self.updated_at.isoformat() if isinstance(self.updated_at, Timestamp) else self.updated_at
        }

# Property methods

    async def get_posts(self):
        """Get a list of Post objects from a list of post IDs."""
        from database import get_db
        db = get_db()
        all_posts = await db.load_all_posts()
        return [elem for elem in all_posts if elem.post_id in self.post_ids]

    async def get_accounts(self):
        return await Account.get_accounts(self.accounts)

    def get_action(self):
        """Return the action as a dict."""
        return self.action if isinstance(self.action, dict) else None

    def get_action_type(self):
        """Return the action type if action is present, else None."""
        return self.get_action().get('type', None)

    def get_reaction_palette_name(self):
        """Get the palette for react action, if present, else None."""
        return self.get_action().get('palette', None)

    def get_reaction_emojis(self):
        """Get emojis for a specific action type and palette from config."""
        palette = self.get_reaction_palette_name()
        if palette == 'positive':
            return config.get('reactions_palettes', {}).get('positive', [])
        elif palette == 'negative':
            return config.get('reactions_palettes', {}).get('negative', [])
        elif palette is None:
            return []
        else:
            raise ValueError(f"Unknown reaction palette: {palette}")

# Actions

    async def _run(self):
        reporter = Reporter()
        await reporter.start()
        async with await reporter.run_context(self.task_id, meta={"task_name": self.name, "action": self.get_action_type()}) as run_id:
            try:
                await reporter.event(run_id, self.task_id, "INFO", "info.init.run_start", f"Starting run for task.")
                accounts = await self.get_accounts()
                posts = await self.get_posts()
                await reporter.event(run_id, self.task_id, "DEBUG", "info.init.data_loaded", f"Got accounts and posts objects.")

                await self._check_pause()
                self._clients = await Client.connect_clients(accounts, self.logger)
                await reporter.event(run_id, self.task_id, "INFO", "info.connecting.client_connect", f"Connected {len(self._clients)} clients.")

                await self._check_pause()
                if self._clients:  # Validate posts to get corresponding ids
                    posts = await Post.mass_validate_posts(posts, self._clients[0], self.logger)
                await reporter.event(run_id, self.task_id, "INFO", "info.connecting.posts_validated", f"Validated {len(posts)} posts.")

                if self.get_action() is None:
                    await reporter.event(run_id, self.task_id, "WARNING", "warning.no_action", "No action defined for the task.")
                    raise ValueError("No action defined for the task.")
                else:
                    await reporter.event(run_id, self.task_id, "DEBUG", "info.action.creating_workers", "Proceeding to worker creation")
                    workers = [
                        asyncio.create_task(self.client_worker(client, posts, reporter, run_id))
                        for client in self._clients
                    ]
                    results = await asyncio.gather(*workers, return_exceptions=True)
                    await reporter.event(run_id, self.task_id, "INFO", "info.action.workers_finished", "All workers have finished executing.")
                    for result in results:
                        if isinstance(result, Exception):
                            self.logger.error(f"Error in worker for client {self._clients[results.index(result)].account_id}: {result}")
                            await reporter.event(run_id, self.task_id, "WARNING", "error.worker_exception", f"Worker for client {self._clients[results.index(result)].account_id} raised an exception: {result}")
                    workers.clear()
                
            except asyncio.CancelledError:
                self.logger.info(f"Task {self.task_id} was cancelled.")
                await reporter.event(run_id, self.task_id, "WARNING", "info.run_cancelled", "Run was cancelled.")
                self.status = Task.TaskStatus.PENDING
            except Exception as e:
                self.logger.error(f"Error starting task {self.task_id}: {e}")
                await reporter.event(run_id, self.task_id, "ERROR", "error.run_failed", f"Run failed: {e}")
                self.status = Task.TaskStatus.CRASHED
                raise e
            finally:
                self._clients = await Client.disconnect_clients(self._clients, self.logger)
                self._clients = None
                self.updated_at = Timestamp.now()
                self._task = None  # Mark task as finished
            
            await reporter.event(run_id, self.task_id, "INFO", "info.run_end", "Run has ended.")
            # If you got here - task succeeded
            self.logger.info(f"Task {self.task_id} completed successfully.")
            self.status = Task.TaskStatus.FINISHED
            await reporter.stop()  # stop reporter and flush            


    async def client_worker(self, client, posts, reporter, run_id):
        await reporter.event(run_id, self.task_id, "INFO", "info.worker", f"Worker started for client {client.phone_number}")
        if self.get_action_type() == 'react':
            await reporter.event(run_id, self.task_id, "DEBUG", "info.worker.react", "Worker proceeds to reacting")
            client.active_emoji_palette = self.get_reaction_emojis()
            for post in posts:
                client = await self._check_pause_single(client)  # Check pause before each post
                if post.is_validated:
                    try:
                        await client.react(post.message_id, post.chat_id)
                        self.logger.debug(f"Client {client.account_id} reacted to post {post.post_id}")
                        await reporter.event(run_id, self.task_id, "DEBUG", "info.worker.react", 
                                             f"Client {client.phone_number} reacted to post {post.post_id} with {self.get_reaction_palette_name()}",
                                             {"client": client.phone_number, "post_id": post.post_id, "palette": self.get_reaction_palette_name()})
                    except Exception as e:
                        self.logger.warning(f"Client {client.account_id} failed to react to post {post.post_id}: {e}")
                        await reporter.event(run_id, self.task_id, "WARNING", "info.worker.react", f"Client {client.phone_number} failed to react to post {post.post_id}: {e}")

                # add per-post sleep


        if self.get_action_type() == 'comment':  # Logic to handle comment actions can be added here
            self.logger.warning("Comment actions are not implemented yet.")
            await reporter.event(run_id, self.task_id, "DEBUG", "info.worker.comment", "Worker proceeds to commenting. NYI")
            pass

        await reporter.event(run_id, self.task_id, "INFO", "info.worker", f"Worker finished for client {client.phone_number}")

    async def _check_pause(self):
        """Check if task should be paused and wait if needed. Disconnect clients on pause, reconnect on resume."""
        if not self._pause_event.is_set():
            self.logger.info(f"Task {self.task_id} is paused, disconnecting clients and waiting to resume...")
            # Disconnect clients if connected
            if self._clients:
                for client in self._clients:
                    try:
                        await client.client.disconnect()
                    except Exception as e:
                        self.logger.warning(f"Error disconnecting client {client.account_id}: {e}")
                self._clients = None
            await self._pause_event.wait()
            # Reconnect clients
            self.logger.info(f"Task {self.task_id} resumed. Reconnecting clients...")
            accounts = self.get_accounts()
            self._clients = await Client.connect_clients(accounts, self.logger)

    async def _check_pause_single(self, client, reporter, run_id):
        if not self._pause_event.is_set():
            self.logger.info(f"Task {self.task_id} is paused, disconnecting client {client.account_id} and waiting to resume...")
            await reporter.event(run_id, self.task_id, "INFO", "info.worker", f"Worker paused for client {client.phone_number}, disconnecting...")
            try:
                await client.client.disconnect()
            except Exception as e:
                self.logger.warning(f"Error disconnecting client {client.account_id}: {e}")
                await reporter.event(run_id, self.task_id, "WARNING", "info.worker", f"Worker failed to disconnect for client {client.phone_number}: {e}")
            client.client = None
            self.logger.debug(f"Task {self.task_id} for client {client.account_id} is paused.")
            await reporter.event(run_id, self.task_id, "DEBUG", "info.worker", f"Worker paused for client {client.phone_number}.")
            await self._pause_event.wait()
            await reporter.event(run_id, self.task_id, "DEBUG", "info.worker", f"Worker resumed for client {client.phone_number}.")
            try:
                await client.connect()
            except Exception as e:
                self.logger.warning(f"Error reconnecting client {client.account_id}: {e}")
                await reporter.event(run_id, self.task_id, "WARNING", "info.worker", f"Worker failed to reconnect for client {client.phone_number}: {e}")
            self.logger.info(f"Task {self.task_id} for client {client.account_id} resumed.")
            await reporter.event(run_id, self.task_id, "DEBUG", "info.worker", f"Worker resumed for client {client.phone_number}.")
            return client
        return client



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
            self.status = Task.TaskStatus.PAUSED
            self.logger.info(f"Task {self.task_id} paused.")

    async def resume(self):
        """Resume the task."""
        if self.status == Task.TaskStatus.PAUSED:
            self._pause_event.set()
            self.status = Task.TaskStatus.RUNNING
            self.logger.info(f"Task {self.task_id} resumed.")

    async def get_status(self):
        """Get the current status of the task."""
        # Additional info gathering logic can be added here
        # Will be used to create reports
        return self.status
