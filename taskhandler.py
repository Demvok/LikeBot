from pandas import Timestamp
import asyncio, datetime
from enum import Enum, auto

from telethon import errors
from pymongo import errors as mg_errors
from pandas import errors as pd_errors

from agent import Account, Client
from reporter import Reporter
from logger import setup_logger, load_config, crash_handler, handle_task_exception
from schemas import TaskStatus

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
            'is_validated': self.is_validated,
            'created_at': self.created_at.isoformat() if isinstance(self.created_at, Timestamp) else self.created_at,
            'updated_at': self.updated_at.isoformat() if isinstance(self.updated_at, Timestamp) else self.updated_at
        }

    @classmethod
    def from_keys(cls, message_link:str, post_id:int=None, chat_id:int=None, message_id:int=None):
        """Create a Post object from keys."""
        return cls(
            post_id=post_id,
            message_link=message_link,
            chat_id=chat_id,
            message_id=message_id
        )



    async def validate(self, client: Client, logger=None):
        """Validate the post by fetching its chat_id and message_id, and update the record in file."""
        from database import get_db
        db = get_db()
        retries = config.get('delays', {}).get('action_retries', 5)
        delay = config.get('delays', {}).get('action_retry_delay', 3)
        attempt = 0
        while attempt < retries:
            try:
                chat_id, message_id = await client.get_message_ids(self.message_link)
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
                    logger.error(f"Failed to validate post {self.post_id} after {retries} attempts. Error: {e}")
                    raise
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
                raise
        
        if logger:
            logger.info(f"Validated {len(posts)} posts: {newly_validated} newly validated, {already_validated} already validated, {failed_validation} failed validation.")
        
        return new_posts



class Task:

    logger = setup_logger("main", "main.log")

    # Use centralized TaskStatus from schemas.py
    TaskStatus = TaskStatus

    def __init__(self, name, post_ids, accounts, action, task_id=None, description=None, status=None, created_at=None, updated_at=None):
        self.task_id = task_id
        self.name = name
        self.description = description
        self.post_ids = sorted(post_ids)
        self.accounts = accounts
        self.action = action
        self.status = status or Task.TaskStatus.PENDING
        self.created_at = created_at or Timestamp.now()
        self.updated_at = updated_at or Timestamp.now()
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Initially not paused
        self._task = None
        self._clients = None  # Store connected clients for pause/resume
        self._current_run_id = None

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

    async def get_reaction_emojis(self):
        """
        Get emojis for a specific action type and palette from database.
        
        Returns:
            Tuple of (list of emoji strings, ordered flag)
            
        Raises:
            ValueError: If palette not found in database
        """
        from database import get_db
        
        palette = self.get_reaction_palette_name()
        if palette is None:
            return [], False
        
        db = get_db()
        palette_data = await db.get_palette(palette)
        
        if not palette_data:
            raise ValueError(f"Reaction palette '{palette}' not found in database. Please run migrate_palettes.py to create it.")
        
        emojis = palette_data.get('emojis', [])
        if not emojis:
            raise ValueError(f"Reaction palette '{palette}' exists but has no emojis configured.")
        
        ordered = palette_data.get('ordered', False)
        
        self.logger.debug(f"Loaded palette '{palette}' from database with {len(emojis)} emojis, ordered={ordered}")
        return emojis, ordered

    async def _update_status(self):
        from database import get_db
        db = get_db()
        await db.update_task(self.task_id, {'status': self.status.name if isinstance(self.status, Task.TaskStatus) else self.status})

# Actions

    @crash_handler
    async def _run(self):
        if not self.task_id:
            self.logger.error("Task ID is not set.")
            raise ValueError("Task ID is not set.")

        reporter = Reporter()
        await reporter.start()
        async with await reporter.run_context(self.task_id, meta={"task_name": self.name, "action": self.get_action_type()}) as run_id:
            try:
                self._current_run_id = run_id
                await reporter.event(run_id, self.task_id, "INFO", "info.init.run_start", f"Starting run for task.")
                
                # Load accounts and posts with specific DB error handling
                try:
                    accounts = await self.get_accounts()
                    posts = await self.get_posts()
                except (mg_errors.PyMongoError, ConnectionError) as db_exc:
                    self.logger.error(f"MongoDB error while loading accounts or posts for task {self.task_id}: {db_exc}")
                    await reporter.event(run_id, self.task_id, "ERROR", "error.db_mongo_load_failed", f"MongoDB error while loading accounts or posts: {db_exc}", {'error': repr(db_exc)})
                    self.status = Task.TaskStatus.CRASHED
                    await self._update_status()
                    raise
                except (FileNotFoundError, PermissionError, OSError) as file_exc:
                    self.logger.error(f"File error while loading accounts or posts for task {self.task_id}: {file_exc}")
                    await reporter.event(run_id, self.task_id, "ERROR", "error.db_file_load_failed", f"File error while loading accounts or posts: {file_exc}", {'error': repr(file_exc)})
                    self.status = Task.TaskStatus.CRASHED
                    await self._update_status()
                    raise
                except (pd_errors.ParserError, ValueError) as parse_exc:
                    self.logger.error(f"Parsing error while loading accounts or posts for task {self.task_id}: {parse_exc}")
                    await reporter.event(run_id, self.task_id, "ERROR", "error.db_parse_load_failed", f"Parsing error while loading accounts or posts: {parse_exc}", {'error': repr(parse_exc)})
                    self.status = Task.TaskStatus.CRASHED
                    await self._update_status()
                    raise
                except Exception as db_exc:
                    self.logger.error(f"Unknown database error while loading accounts or posts for task {self.task_id}: {db_exc}")
                    await reporter.event(run_id, self.task_id, "ERROR", "error.db_load_failed", f"Unknown database error while loading accounts or posts: {db_exc}", {'error': repr(db_exc)})
                    self.status = Task.TaskStatus.CRASHED
                    await self._update_status()
                    raise
                await reporter.event(run_id, self.task_id, "DEBUG", "info.init.data_loaded", f"Got accounts and posts objects.")

                await self._check_pause(reporter, run_id)
                self._clients = await Client.connect_clients(accounts, self.logger)
                await reporter.event(run_id, self.task_id, "INFO", "info.connecting.client_connect", f"Connected {len(self._clients)} clients.")

                await self._check_pause(reporter, run_id)
                if self._clients:  # Validate posts to get corresponding ids
                    try:
                        posts = await Post.mass_validate_posts(posts, self._clients[0], self.logger)
                    except (mg_errors.PyMongoError, ConnectionError) as db_exc:
                        self.logger.error(f"MongoDB error while validating posts for task {self.task_id}: {db_exc}")
                        await reporter.event(run_id, self.task_id, "ERROR", "error.db_mongo_post_validation_failed", f"MongoDB error while validating posts: {db_exc}", {'error': repr(db_exc)})
                        self.status = Task.TaskStatus.CRASHED
                        await self._update_status()
                        raise
                    except (FileNotFoundError, PermissionError, OSError) as file_exc:
                        self.logger.error(f"File error while validating posts for task {self.task_id}: {file_exc}")
                        await reporter.event(run_id, self.task_id, "ERROR", "error.db_file_post_validation_failed", f"File error while validating posts: {file_exc}", {'error': repr(file_exc)})
                        self.status = Task.TaskStatus.CRASHED
                        await self._update_status()
                        raise
                    except (pd_errors.ParserError, ValueError) as parse_exc:
                        self.logger.error(f"Parsing error while validating posts for task {self.task_id}: {parse_exc}")
                        await reporter.event(run_id, self.task_id, "ERROR", "error.db_parse_post_validation_failed", f"Parsing error while validating posts: {parse_exc}", {'error': repr(parse_exc)})
                        self.status = Task.TaskStatus.CRASHED
                        await self._update_status()
                        raise
                    except Exception as db_exc:
                        self.logger.error(f"Unknown database error while validating posts for task {self.task_id}: {db_exc}")
                        await reporter.event(run_id, self.task_id, "ERROR", "error.db_post_validation_failed", f"Unknown database error while validating posts: {db_exc}", {'error': repr(db_exc)})
                        self.status = Task.TaskStatus.CRASHED
                        await self._update_status()
                        raise
                await reporter.event(run_id, self.task_id, "INFO", "info.connecting.posts_validated", f"Validated {len(posts)} posts.")

                if self.get_action() is None:
                    self.logger.error("No action defined for the task.")
                    await reporter.event(run_id, self.task_id, "WARNING", "error.no_action", "No action defined for the task.")
                    raise ValueError("No action defined for the task.")
                else:
                    self.logger.info(f"Task {self.task_id} proceeding with action: {self.get_action_type()}")
                    await reporter.event(run_id, self.task_id, "DEBUG", "info.action.creating_workers", "Proceeding to worker creation")
                    workers = [
                        asyncio.create_task(self.client_worker(client, posts, reporter, run_id))
                        for client in self._clients
                    ]
                    for worker in workers:
                        worker.add_done_callback(handle_task_exception)

                    self.logger.info(f"Created {len(workers)} workers for task {self.task_id}.")
                    results = await asyncio.gather(*workers, return_exceptions=True)
                    self.logger.info(f"All workers for task {self.task_id} have finished executing.")
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
                await self._update_status()
            except errors.PhoneNumberInvalidError as e:
                self.logger.error(f"Task {self.task_id} encountered a PhoneNumberInvalidError: {e}")
                await reporter.event(run_id, self.task_id, "ERROR", "error.phone_number_invalid", f"Encountered PhoneNumberInvalidError: {e}", {'error': repr(e)})
            except Exception as e:
                import sys
                self.logger.error(f"Error starting task {self.task_id}: {e}")
                await reporter.event(run_id, self.task_id, "ERROR", "error.run_failed", f"Unhandled error, run failed: {e}", {'error': repr(e)})
                self.status = Task.TaskStatus.CRASHED
                await self._update_status()
                raise e
            finally:
                self._clients = await Client.disconnect_clients(self._clients, self.logger)
                self._clients = None
                self.updated_at = Timestamp.now()
                self._task = None  # Mark task as finished
            
            await reporter.event(run_id, self.task_id, "INFO", "info.run_end", "Run has ended.")
            # If you got here - task succeeded
            self.status = Task.TaskStatus.FINISHED
            await self._update_status()
            self.logger.info(f"Task {self.task_id} completed successfully.")
            return
        await reporter.stop()  # stop reporter and flush            

    @crash_handler
    async def client_worker(self, client, posts, reporter, run_id):
        await reporter.event(run_id, self.task_id, "INFO", "info.worker", f"Worker started for client {client.phone_number}")
        if self.get_action_type() == 'react':
            await reporter.event(run_id, self.task_id, "DEBUG", "info.worker.action", "Worker proceeds to reacting")
            
            # Get palette emojis and ordering flag, then set on client
            emojis, palette_ordered = await self.get_reaction_emojis()
            client.active_emoji_palette = emojis
            client.palette_ordered = palette_ordered
            
            self.logger.debug(f"Client {client.phone_number} using palette with {len(emojis)} emojis, ordered={palette_ordered}")
            
            retries = config.get('delays', {}).get('action_retries', 5)
            for post in posts:
                client = await self._check_pause_single(client, reporter, run_id)  # Check pause before each post
                if post.is_validated:
                    attempt = 0
                    while attempt < retries:
                        try:
                            await client.react(post.message_id, post.chat_id)
                            self.logger.debug(f"Client {client.account_id} reacted to post {post.post_id}")
                            await reporter.event(run_id, self.task_id, "DEBUG", "info.worker.react", 
                                                 f"Client {client.phone_number} reacted to post {post.post_id} with {self.get_reaction_palette_name()}",
                                                 {"client": client.phone_number, "post_id": post.post_id, "palette": self.get_reaction_palette_name()})
                            break  # Success, exit retry loop
                        except errors.FloodWaitError as e:
                            attempt += 1
                            self.logger.error(f"Client {client.account_id} hit FloodWaitError on post {post.post_id}: wait for {e.seconds} seconds. Attempt {attempt}/{retries}")
                            await reporter.event(run_id, self.task_id, "ERROR", "error.worker.flood_wait", 
                                                 f"Client {client.phone_number} hit FloodWaitError on post {post.post_id}: wait for {e.seconds} seconds. Attempt {attempt}/{retries}",
                                                 {"client": client.phone_number, "post_id": post.post_id, "wait_seconds": e.seconds, "attempt": attempt})
                            await asyncio.sleep(e.seconds + 5)  # Sleep for the required time plus a buffer
                        except errors.SessionPasswordNeededError:
                            self.logger.error(f"Client {client.account_id} requires 2FA password to proceed. Stopping worker.")
                            await reporter.event(run_id, self.task_id, "ERROR", "error.worker.2fa_required", 
                                                 f"Client {client.phone_number} requires 2FA password to proceed. Stopping worker.",
                                                 {"client": client.phone_number})
                            return
                        except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError):  # To move to auth codeblock
                            self.logger.error(f"Client {client.account_id} has invalid or expired phone code. Stopping worker.")
                            await reporter.event(run_id, self.task_id, "ERROR", "error.worker.phone_code_invalid", 
                                                 f"Client {client.phone_number} has invalid or expired phone code. Stopping worker.",
                                                 {"client": client.phone_number})
                            return
                        except errors.UserNotParticipantError:
                            self.logger.error(f"Client {client.account_id} is not a participant of the chat for post {post.post_id}. Cannot react. Skipping post.")
                            await reporter.event(run_id, self.task_id, "ERROR", "error.worker.not_participant", 
                                                 f"Client {client.phone_number} is not a participant of the chat for post {post.post_id}. Cannot react. Skipping post.",
                                                 {"client": client.phone_number, "post_id": post.post_id})
                            break  # Skip to next post
                        except errors.ChatAdminRequiredError:
                            self.logger.error(f"Client {client.account_id} requires admin privileges to react in the chat for post {post.post_id}. Skipping post.")
                            await reporter.event(run_id, self.task_id, "ERROR", "error.worker.admin_required", 
                                                 f"Client {client.phone_number} requires admin privileges to react in the chat for post {post.post_id}. Skipping post.",
                                                 {"client": client.phone_number, "post_id": post.post_id})
                            break
                        except errors.ChannelPrivateError:
                            self.logger.error(f"Client {client.account_id} cannot access the chat for post {post.post_id} (channel might be private). Skipping post.")
                            await reporter.event(run_id, self.task_id, "ERROR", "error.worker.channel_private", 
                                                 f"Client {client.phone_number} cannot access the chat for post {post.post_id} (channel might be private). Skipping post.",
                                                 {"client": client.phone_number, "post_id": post.post_id})
                            break
                        except errors.PhoneNumberBannedError:
                            self.logger.error(f"Client {client.account_id} is banned. Stopping worker.")
                            await reporter.event(run_id, self.task_id, "ERROR", "error.worker.phone_banned", 
                                                 f"Client {client.phone_number} is banned. Stopping worker.",
                                                 {"client": client.phone_number})
                            return
                        except ConnectionError as e:
                            attempt += 1
                            self.logger.error(f"Client {client.account_id} encountered ConnectionError on post {post.post_id}: {e}. Attempt {attempt}/{retries}")
                            await reporter.event(run_id, self.task_id, "ERROR", "error.worker.connection_error", 
                                                 f"Client {client.phone_number} encountered ConnectionError on post {post.post_id}: {e}. Attempt {attempt}/{retries}",
                                                 {"client": client.phone_number, "post_id": post.post_id, "error": str(e), "attempt": attempt})
                            await asyncio.sleep(5)
                        except TimeoutError as e:
                            attempt += 1
                            self.logger.error(f"Client {client.account_id} encountered TimeoutError on post {post.post_id}: {e}. Attempt {attempt}/{retries}")
                            await reporter.event(run_id, self.task_id, "ERROR", "error.worker.timeout_error", 
                                                 f"Client {client.phone_number} encountered TimeoutError on post {post.post_id}: {e}. Attempt {attempt}/{retries}",
                                                 {"client": client.phone_number, "post_id": post.post_id, "error": str(e), "attempt": attempt})
                            await asyncio.sleep(5)
                        except errors.RPCError as e:
                            attempt += 1
                            self.logger.error(f"Client {client.account_id} encountered RPCError on post {post.post_id}: {e}. Attempt {attempt}/{retries}")
                            await reporter.event(run_id, self.task_id, "ERROR", "error.worker.rpc_error", 
                                                 f"Client {client.phone_number} encountered RPCError on post {post.post_id}: {e}. Attempt {attempt}/{retries}",
                                                 {"client": client.phone_number, "post_id": post.post_id, "error": str(e), "attempt": attempt})
                            await asyncio.sleep(5)
                        except errors.ServerError as e:
                            attempt += 1
                            self.logger.error(f"Client {client.account_id} encountered ServerError on post {post.post_id}: {e}. Attempt {attempt}/{retries}")
                            await reporter.event(run_id, self.task_id, "ERROR", "error.worker.server_error", 
                                                 f"Client {client.phone_number} encountered ServerError on post {post.post_id}: {e}. Attempt {attempt}/{retries}",
                                                 {"client": client.phone_number, "post_id": post.post_id, "error": str(e), "attempt": attempt})
                            await asyncio.sleep(5)
                        except errors.MessageIdInvalidError:
                            self.logger.error(f"Client {client.account_id} encountered MessageIdInvalidError on post {post.post_id}. Skipping post.")
                            await reporter.event(run_id, self.task_id, "ERROR", "error.worker.message_id_invalid", 
                                                 f"Client {client.phone_number} encountered MessageIdInvalidError on post {post.post_id}. Skipping post.",
                                                 {"client": client.phone_number, "post_id": post.post_id})
                            break                        
                        except Exception as e:
                            self.logger.warning(f"Client {client.account_id} failed to react to post {post.post_id}: {e}")
                            await reporter.event(run_id, self.task_id, "WARNING", "error.worker.react", f"Client {client.phone_number} failed to react to post {post.post_id}: {e}", {'error': repr(e)})
                            raise  # On other errors, do not retry
                    if attempt == retries:   # Optionally, log/report if all retries failed
                        self.logger.error(f"Client {client.account_id} failed to react to post {post.post_id} after {retries} attempts due to repeated FloodWaitError.")
                        await reporter.event(run_id, self.task_id, "ERROR", "error.worker.react.max_retries", f"Client {client.phone_number} failed to react to post {post.post_id} after {retries} FloodWaitError retries.", {"client": client.phone_number, "post_id": post.post_id, "retries": retries})
                # add per-post sleep


        if self.get_action_type() == 'comment':  # Logic to handle comment actions can be added here
            self.logger.warning("Comment actions are not implemented yet.")
            await reporter.event(run_id, self.task_id, "DEBUG", "info.worker.action", "Worker proceeds to commenting. NYI")
            pass

        await reporter.event(run_id, self.task_id, "INFO", "info.worker", f"Worker finished for client {client.phone_number}")

    @crash_handler
    async def _check_pause(self, reporter, run_id):
        """Check if task should be paused and wait if needed. Disconnect clients on pause, reconnect on resume."""
        if not self._pause_event.is_set():
            self.logger.info(f"Task {self.task_id} is paused, disconnecting clients and waiting to resume...")
            await reporter.event(run_id, self.task_id, "INFO", "info.status", f"Task {self.task_id} is paused.")
            # Disconnect clients if connected
            if self._clients:
                for client in self._clients:
                    try:
                        await client.client.disconnect()
                    except Exception as e:
                        self.logger.warning(f"Error disconnecting client {client.account_id}: {e}")
                        await reporter.event(run_id, self.task_id, "ERROR", "error", f"Task {self.task_id} failed to disconnect: {e}", {'error': repr(e)})
                self._clients = None
            await self._pause_event.wait()
            # Reconnect clients
            self.logger.info(f"Task {self.task_id} resumed. Reconnecting clients...")
            await reporter.event(run_id, self.task_id, "INFO", "info.status", f"Task {self.task_id} is resumed.")
            accounts = await self.get_accounts()
            self._clients = await Client.connect_clients(accounts, self.logger)
            await reporter.event(run_id, self.task_id, "INFO", "info.status", f"Successfully reconnected {len(self._clients)} clients.")

    @crash_handler
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
                await reporter.event(run_id, self.task_id, "WARNING", "error.worker", f"Worker failed to reconnect for client {client.phone_number}: {e}")
            self.logger.info(f"Task {self.task_id} for client {client.account_id} resumed.")
            await reporter.event(run_id, self.task_id, "DEBUG", "info.worker", f"Worker resumed for client {client.phone_number}.")
            return client
        return client



    async def start(self):
        """Start the task."""
        if self._task and not self._task.done():
            self.logger.warning(f"Task {self.task_id} is already running (run_id={self.current_run_id}).")
            return

        if self._task is None or self._task.done():
            self._pause_event.set()
            self._current_run_id = None  # Will empty if exists, but if exists and finished will be used for statistics
            self.logger.info(f"Starting task {self.task_id} - {self.name}...")
            self._task = asyncio.create_task(self._run())
            self.updated_at = Timestamp.now()
            self.status = Task.TaskStatus.RUNNING
            await self._update_status()

    async def run_and_wait(self):  # I'm not sure about this, maybe not needed
        """Start the task and wait for it to complete."""
        await self.start()
        if self._task:
            try:
                await self._task
            except Exception as e:
                self.logger.error(f"Task {self.task_id} failed: {e}")
                raise

    async def pause(self):
        """Pause the task."""
        if self.status == Task.TaskStatus.RUNNING:
            self._pause_event.clear()
            self.status = Task.TaskStatus.PAUSED
            await self._update_status()
            self.logger.info(f"Task {self.task_id} paused.")

    async def resume(self):
        """Resume the task."""
        if self.status == Task.TaskStatus.PAUSED:
            self._pause_event.set()
            self.status = Task.TaskStatus.RUNNING
            await self._update_status()
            self.logger.info(f"Task {self.task_id} resumed.")

    async def get_status(self):
        """Get the current status of the task."""
        return self.status
    
    @crash_handler
    async def get_report(self, type='success'):
        """Get the report for the current task run. If it is running you will need to refresh."""
        if not self._current_run_id:
            self.logger.warning(f'Task {self.task_id} is not currently running or ran previously.')
            return None

        from reporter import RunEventManager, create_report
        eventManager = RunEventManager()

        events = await eventManager.get_events(self._current_run_id)

        return await create_report(events, type)
