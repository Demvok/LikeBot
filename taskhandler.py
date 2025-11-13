from pandas import Timestamp
import asyncio, datetime, random

from telethon import errors
from pymongo import errors as mg_errors
from pandas import errors as pd_errors

from agent import Account, Client
from reporter import Reporter
from logger import setup_logger, load_config, crash_handler, handle_task_exception
from schemas import TaskStatus, status_name
from telethon_error_handler import map_telethon_exception, reporter_payload_from_mapping

config = load_config()


def _status_name(status) -> str:
    """Return a stable string for a status value that may be an Enum or a plain string."""
    # Delegate to central helper in schemas for consistent behavior across the repo
    try:
        from schemas import status_name as _sn
        return _sn(status)
    except Exception:
        try:
            if hasattr(status, 'name'):
                return status.name
        except Exception:
            pass
        return str(status)


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
    async def mass_validate_posts(cls, posts, clients, logger=None, max_clients_per_post=3):
        """Validate multiple posts asynchronously, trying multiple clients if one fails."""
        if logger:
            logger.info(f"Validating {len(posts)} posts...")
        if not posts:
            if logger:
                logger.warning("No posts to validate.")
            return []
        if not isinstance(posts, list):
            raise ValueError("Posts should be a list of Post objects.")
        if not clients:
            raise ValueError("No clients provided for validation.")
        
        # Ensure clients is a list
        if not isinstance(clients, list):
            clients = [clients]
        
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
                
                # Try validation with limited number of clients.
                # Skip any clients that are currently non-usable (status changed while running)
                validation_succeeded = False
                last_error = None
                usable_clients = [c for c in clients if getattr(c, 'account', None) and c.account.is_usable()]
                if not usable_clients:
                    # Nothing to try - all clients are non-usable
                    if logger:
                        logger.error(f"No usable clients available to validate post {post.post_id}.")
                    raise ValueError(f"No usable clients available to validate post {post.post_id}.")

                clients_to_try = min(max_clients_per_post, len(usable_clients))

                for client_idx, client in enumerate(usable_clients[:clients_to_try]):
                    try:
                        if logger:
                            logger.debug(f"Attempting to validate post {post.post_id} with client {client_idx + 1}/{clients_to_try}")
                        
                        await post.validate(client=client, logger=logger)
                        new_post = await db.get_post(post.post_id)
                        new_posts.append(new_post)

                        if new_post.is_validated:
                            newly_validated += 1
                            validation_succeeded = True
                            if logger:
                                logger.debug(f"Post {post.post_id} validated successfully with client {client_idx + 1}")
                            break
                        else:
                            if logger:
                                logger.warning(f"Post {post.post_id} validation returned but not validated with client {client_idx + 1}")
                    
                    except (errors.AuthKeyUnregisteredError, errors.AuthKeyInvalidError, errors.SessionRevokedError) as auth_error:
                        last_error = auth_error
                        if logger:
                            logger.error(f"Client {client.phone_number} has invalid/expired session while validating post {post.post_id}: {auth_error}")
                        # Use centralized mapping to decide action and status
                        mapping = map_telethon_exception(auth_error)
                        try:
                            if mapping.get('status'):
                                await client.account.update_status(mapping['status'], error=auth_error)
                                if logger:
                                    logger.info(f"Marked account {client.phone_number} as {mapping['status']}")
                        except Exception as update_error:
                            if logger:
                                logger.warning(f"Failed to update account status for {client.phone_number}: {update_error}")
                        # Try next client
                        continue
                    
                    except errors.UserDeactivatedBanError as ban_error:
                        last_error = ban_error
                        if logger:
                            logger.error(f"Client {client.phone_number} is banned: {ban_error}")
                        mapping = map_telethon_exception(ban_error)
                        try:
                            if mapping.get('status'):
                                await client.account.update_status(mapping['status'], error=ban_error)
                                if logger:
                                    logger.info(f"Marked account {client.phone_number} as {mapping['status']}")
                        except Exception as update_error:
                            if logger:
                                logger.warning(f"Failed to update account status for {client.phone_number}: {update_error}")
                        # Try next client
                        continue
                    
                    except errors.PhoneNumberBannedError as ban_error:
                        last_error = ban_error
                        if logger:
                            logger.error(f"Client {client.phone_number} phone number is banned: {ban_error}")
                        mapping = map_telethon_exception(ban_error)
                        try:
                            if mapping.get('status'):
                                await client.account.update_status(mapping['status'], error=ban_error)
                                if logger:
                                    logger.info(f"Marked account {client.phone_number} as {mapping['status']}")
                        except Exception as update_error:
                            if logger:
                                logger.warning(f"Failed to update account status for {client.phone_number}: {update_error}")
                        # Try next client
                        continue
                    
                    except errors.RPCError as rpc_error:
                        last_error = rpc_error
                        if logger:
                            logger.warning(f"Telegram error validating post {post.post_id} with client {client.phone_number}: {rpc_error}")
                        # Try next client
                        continue
                    
                    except Exception as client_error:
                        last_error = client_error
                        if logger:
                            logger.warning(f"Error validating post {post.post_id} with client {client.phone_number}: {client_error}")
                        # Try next client
                        continue
                
                # If all attempted clients failed, add to failed count and raise the last error
                if not validation_succeeded:
                    failed_validation += 1
                    error_type = type(last_error).__name__ if last_error else "Unknown"
                    if logger:
                        logger.error(f"Post {post.post_id} failed validation with {clients_to_try} clients. Last error ({error_type}): {last_error}")
                    
                    # Provide more helpful error message based on error type
                    if isinstance(last_error, (errors.AuthKeyUnregisteredError, errors.SessionRevokedError)):
                        raise ValueError(f"Post {post.post_id} validation failed: All {clients_to_try} client sessions are invalid/expired or revoked. Please re-login accounts.")
                    else:
                        raise last_error if last_error else ValueError(f"Post {post.post_id} failed validation with {clients_to_try} clients")

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
            'status': status_name(self.status),
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
        await db.update_task(self.task_id, {'status': status_name(self.status)})

    async def _mark_crashed(self, exc: Exception | None = None, context: str | None = None):
        """Mark the task as crashed and persist status to DB.

        This helper is safe to call from done-callbacks (schedule with create_task).
        """
        try:
            # Best-effort logging
            self.logger.error(f"Marking task {self.task_id} as CRASHED (context={context}) due to: {repr(exc)}")
        except Exception:
            pass
        try:
            self.status = Task.TaskStatus.CRASHED
            await self._update_status()
        except Exception as e:
            # Avoid raising from done-callbacks
            try:
                self.logger.warning(f"Failed to persist crashed status for task {self.task_id}: {e}")
            except Exception:
                pass

    async def _handle_worker_done(self, worker_task: asyncio.Task):
        """Async handler invoked when a worker finishes; if it errored, mark the parent task crashed."""
        try:
            # Obtain exception without raising
            exc = None
            try:
                exc = worker_task.exception()
            except asyncio.CancelledError:
                return
            if exc:
                # Do not swallow - mark task crashed in DB
                await self._mark_crashed(exc=exc, context='worker_task')
        except Exception:
            # Swallow everything in background callback
            return

    async def _handle_main_done(self, main_task: asyncio.Task):
        """Called when the main task finishes; persist crashed state if it finished with an exception."""
        try:
            exc = None
            try:
                exc = main_task.exception()
            except asyncio.CancelledError:
                # Treat cancellation as non-crash (handled elsewhere)
                return
            if exc:
                await self._mark_crashed(exc=exc, context='main_task')
        except Exception:
            return

# Actions

    @crash_handler
    async def _run(self):
        if not self.task_id:
            self.logger.error("Task ID is not set.")
            raise ValueError("Task ID is not set.")

        reporter = Reporter()
        # Ensure reporter.start() or run_context creation failures are handled
        try:
            await reporter.start()
            _run_ctx = await reporter.run_context(self.task_id, meta={"task_name": self.name, "action": self.get_action_type()})
        except Exception as e:
            # If reporter fails to init we should mark the task crashed and persist that
            import sys
            self.logger.error(f"Failed to initialize reporter for task {self.task_id}: {e}")
            try:
                # best-effort: reporter may be partially initialized
                await reporter.event(None, self.task_id, "ERROR", "error.reporter_init", f"Reporter init failed: {e}", {'error': repr(e)})
            except Exception:
                pass
            self.status = Task.TaskStatus.CRASHED
            await self._update_status()
            raise

        async with _run_ctx as run_id:
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
                
                # Filter accounts by status - only use usable accounts
                total_accounts = len(accounts)
                usable_accounts = [acc for acc in accounts if acc.is_usable()]
                unusable_accounts = [acc for acc in accounts if not acc.is_usable()]
                
                if unusable_accounts:
                    unusable_details = [f"{acc.phone_number} ({status_name(acc.status)})" for acc in unusable_accounts]
                    self.logger.warning(f"Excluding {len(unusable_accounts)}/{total_accounts} accounts with unusable status: {', '.join(unusable_details)}")
                    await reporter.event(run_id, self.task_id, "WARNING", "warn.accounts_filtered", 
                                       f"Excluding {len(unusable_accounts)}/{total_accounts} accounts with unusable status",
                                       {"excluded_accounts": unusable_details, "total": total_accounts, "usable": len(usable_accounts)})
                
                if not usable_accounts:
                    error_msg = f"No usable accounts available. All {total_accounts} accounts have non-usable status."
                    self.logger.error(error_msg)
                    await reporter.event(run_id, self.task_id, "ERROR", "error.no_usable_accounts", error_msg,
                                       {"unusable_accounts": unusable_details})
                    self.status = Task.TaskStatus.CRASHED
                    await self._update_status()
                    raise ValueError(error_msg)
                
                accounts = usable_accounts
                self.logger.info(f"Using {len(accounts)} usable accounts out of {total_accounts} total accounts")

                await self._check_pause(reporter, run_id)
                self._clients = await Client.connect_clients(accounts, self.logger)
                await reporter.event(run_id, self.task_id, "INFO", "info.connecting.client_connect", f"Connected {len(self._clients)} clients.")

                await self._check_pause(reporter, run_id)
                if self._clients:  # Validate posts to get corresponding ids
                    try:
                        posts = await Post.mass_validate_posts(posts, self._clients, self.logger)
                    except (errors.AuthKeyUnregisteredError, errors.AuthKeyInvalidError, errors.SessionRevokedError) as auth_exc:
                        self.logger.error(f"Session invalid/expired while validating posts for task {self.task_id}: {auth_exc}")
                        await reporter.event(run_id, self.task_id, "ERROR", "error.session_invalid_post_validation", f"Session invalid/expired while validating posts: {auth_exc}. Please re-login affected accounts.", {'error': repr(auth_exc)})
                        self.status = Task.TaskStatus.CRASHED
                        await self._update_status()
                        raise
                    except errors.RPCError as rpc_exc:
                        self.logger.error(f"Telegram API error while validating posts for task {self.task_id}: {rpc_exc}")
                        await reporter.event(run_id, self.task_id, "ERROR", "error.telegram_post_validation_failed", f"Telegram API error while validating posts: {rpc_exc}", {'error': repr(rpc_exc)})
                        self.status = Task.TaskStatus.CRASHED
                        await self._update_status()
                        raise
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

                # Pre-load reaction palette once for the whole task if action is 'react'.
                # The palette is identical for all clients in the task and can be copied to each client
                # to avoid repeated DB calls per-worker.
                if self.get_action_type() == 'react':
                    try:
                        emojis, palette_ordered = await self.get_reaction_emojis()
                        # Copy palette to each connected client so workers don't hit the DB again
                        if self._clients:
                            for client in self._clients:
                                # store a shallow copy to avoid accidental shared-mutations
                                client.active_emoji_palette = list(emojis)
                                client.palette_ordered = palette_ordered
                        await reporter.event(run_id, self.task_id, "INFO", "info.action.palette_loaded",
                                               f"Loaded reaction palette '{self.get_reaction_palette_name()}' with {len(emojis)} emojis.")
                        self.logger.info(f"Loaded reaction palette '{self.get_reaction_palette_name()}' with {len(emojis)} emojis for task {self.task_id}")
                    except Exception as e:
                        self.logger.error(f"Failed to load reaction palette for task {self.task_id}: {e}")
                        await reporter.event(run_id, self.task_id, "ERROR", "error.action.palette_load_failed",
                                               f"Failed to load reaction palette: {e}", {'error': repr(e)})
                        self.status = Task.TaskStatus.CRASHED
                        await self._update_status()
                        raise

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
                        # Keep existing crash reporting hook
                        worker.add_done_callback(handle_task_exception)
                        # Schedule parent task crash persistence if a worker errors
                        worker.add_done_callback(lambda t, s=self: asyncio.create_task(s._handle_worker_done(t)))

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
            # If you got here - run ended. Only mark FINISHED if not already marked CRASHED
            if self.status != Task.TaskStatus.CRASHED:
                self.status = Task.TaskStatus.FINISHED
                await self._update_status()
                self.logger.info(f"Task {self.task_id} completed successfully.")
            else:
                # status already persisted as CRASHED by callbacks/handlers
                try:
                    self.logger.info(f"Task {self.task_id} ended with status {status_name(self.status)}.")
                except Exception:
                    pass
            return
        await reporter.stop()  # stop reporter and flush            

    @crash_handler
    async def client_worker(self, client: Client, posts: list[Post], reporter: Reporter, run_id):
        # CRITICAL: Stagger worker starts to prevent all accounts hitting API simultaneously
        worker_delay_min = config.get('delays', {}).get('worker_start_delay_min', 2)
        worker_delay_max = config.get('delays', {}).get('worker_start_delay_max', 10)
        start_delay = random.uniform(worker_delay_min, worker_delay_max)
        self.logger.info(f"Worker for {client.phone_number} starting in {start_delay:.2f}s (anti-spam stagger)")
        await asyncio.sleep(start_delay)
        
        await reporter.event(run_id, self.task_id, "INFO", "info.worker", f"Worker started for client {client.phone_number}")
        if self.get_action_type() == 'react':
            await reporter.event(run_id, self.task_id, "DEBUG", "info.worker.action", "Worker proceeds to reacting")
            # The task preloads the palette once and copies it to each client during _run();
            # here we simply trust the client to already have the palette assigned.
            palette = getattr(client, 'active_emoji_palette', []) or []
            palette_ordered = getattr(client, 'palette_ordered', False)
            self.logger.debug(f"Client {client.phone_number} using palette with {len(palette)} emojis, ordered={palette_ordered}")
            
            retries = config.get('delays', {}).get('action_retries', 5)
            for post in posts:
                client = await self._check_pause_single(client, reporter, run_id)  # Check pause before each post
                if post.is_validated:
                    attempt = 0
                    while attempt < retries:
                        try:
                            # Use message_link for proper entity resolution (username-based links resolve better than bare IDs)
                            await client.react(message_link=post.message_link)
                            self.logger.debug(f"Client {client.account_id} reacted to post {post.post_id}")
                            await reporter.event(run_id, self.task_id, "DEBUG", "info.worker.react", 
                                                 f"Client {client.phone_number} reacted to post {post.post_id} with {self.get_reaction_palette_name()}",
                                                 {"client": client.phone_number, "post_id": post.post_id, "palette": self.get_reaction_palette_name()})
                            break  # Success, exit retry loop
                        except errors.FloodWaitError as e:
                            attempt += 1
                            wait_seconds = e.seconds
                            required_sleep = wait_seconds + 5
                            self.logger.error(f"Client {client.account_id} hit FloodWaitError on post {post.post_id}: wait for {wait_seconds} seconds. Attempt {attempt}/{retries}")
                            await reporter.event(run_id, self.task_id, "ERROR", "error.worker.flood_wait", 
                                                 f"Client {client.phone_number} hit FloodWaitError on post {post.post_id}: wait for {wait_seconds} seconds. Attempt {attempt}/{retries}",
                                                 {"client": client.phone_number, "post_id": post.post_id, "wait_seconds": wait_seconds, "required_sleep": required_sleep, "attempt": attempt, "flood_wait_seconds": wait_seconds})
                            # Update account with flood wait status
                            try:
                                await client.account.set_flood_wait(wait_seconds, error=e)
                                self.logger.info(f"Marked account {client.phone_number} as ERROR due to flood-wait until {wait_seconds}s from now (flood_wait_until set)")
                            except Exception as update_error:
                                self.logger.warning(f"Failed to update flood wait status for {client.phone_number}: {update_error}")
                            await asyncio.sleep(required_sleep)  # Sleep for the required time plus a buffer
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
                        except errors.PhoneNumberBannedError as e:
                            self.logger.error(f"Client {client.account_id} is banned. Stopping worker.")
                            mapping = map_telethon_exception(e)
                            payload = reporter_payload_from_mapping(mapping, e, {"client": client.phone_number})
                            await reporter.event(run_id, self.task_id, "ERROR", "error.worker.phone_banned",
                                                 f"Client {client.phone_number} is banned. Stopping worker.",
                                                 payload)
                            # Update account status using centralized mapping
                            try:
                                if mapping.get('status'):
                                    await client.account.update_status(mapping['status'], error=e)
                                    self.logger.info(f"Marked account {client.phone_number} as {mapping['status']}")
                            except Exception as update_error:
                                self.logger.warning(f"Failed to update account status for {client.phone_number}: {update_error}")
                            return
                        except errors.UserDeactivatedBanError as e:
                            self.logger.error(f"Client {client.account_id} account is deactivated/banned. Stopping worker.")
                            mapping = map_telethon_exception(e)
                            payload = reporter_payload_from_mapping(mapping, e, {"client": client.phone_number})
                            await reporter.event(run_id, self.task_id, "ERROR", "error.worker.user_deactivated_ban",
                                                 f"Client {client.phone_number} account is deactivated/banned. Stopping worker.",
                                                 payload)
                            # Update account status using centralized mapping
                            try:
                                if mapping.get('status'):
                                    await client.account.update_status(mapping['status'], error=e)
                                    self.logger.info(f"Marked account {client.phone_number} as {mapping['status']}")
                            except Exception as update_error:
                                self.logger.warning(f"Failed to update account status for {client.phone_number}: {update_error}")
                            return
                        except (errors.AuthKeyUnregisteredError, errors.AuthKeyInvalidError, errors.SessionRevokedError) as e:
                            self.logger.error(f"Client {client.account_id} has invalid/expired session. Stopping worker.")
                            mapping = map_telethon_exception(e)
                            payload = reporter_payload_from_mapping(mapping, e, {"client": client.phone_number})
                            await reporter.event(run_id, self.task_id, "ERROR", "error.worker.session_invalid",
                                                 f"Client {client.phone_number} has invalid/expired session. Stopping worker.",
                                                 payload)
                            # Update account status using centralized mapping
                            try:
                                if mapping.get('status'):
                                    await client.account.update_status(mapping['status'], error=e)
                                    self.logger.info(f"Marked account {client.phone_number} as {mapping['status']} due to invalid session")
                            except Exception as update_error:
                                self.logger.warning(f"Failed to update account status for {client.phone_number}: {update_error}")
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
                        except ValueError as e:
                            # Catches entity resolution errors like "Could not find the input entity"
                            if "Could not find the input entity" in str(e) or "PeerUser" in str(e):
                                self.logger.error(f"Client {client.account_id} could not resolve entity for post {post.post_id} (invalid chat_id or broken message link). Skipping post.")
                                await reporter.event(run_id, self.task_id, "ERROR", "error.worker.entity_not_found", 
                                                     f"Client {client.phone_number} could not resolve entity for post {post.post_id}. Possibly invalid chat_id or message link. Skipping post.",
                                                     {"client": client.phone_number, "post_id": post.post_id, "error": str(e)})
                                break
                            else:
                                # Other ValueError - raise it
                                raise
                        except Exception as e:
                            # Centralized mapping for unknown/other exceptions
                            self.logger.warning(f"Client {client.account_id} failed to react to post {post.post_id}: {e}")
                            mapping = map_telethon_exception(e)
                            payload = reporter_payload_from_mapping(mapping, e, {"client": client.phone_number, "post_id": post.post_id})
                            # Report event including message code and details
                            await reporter.event(run_id, self.task_id, "WARNING", "error.worker.react", f"Client {client.phone_number} failed to react to post {post.post_id}: {e}", payload)

                            # Decide next action based on mapping
                            action = mapping.get('action')
                            if action == 'retry' and attempt < retries:
                                attempt += 1
                                await asyncio.sleep(5)
                                continue
                            elif action == 'ignore':
                                break
                            elif mapping.get('status'):
                                # Mark account with mapped status and stop worker
                                try:
                                    await client.account.update_status(mapping['status'], error=e)
                                except Exception as update_error:
                                    self.logger.warning(f"Failed to update account status for {client.phone_number}: {update_error}")
                                return
                            else:
                                # Fallback: mark account as ERROR and stop
                                try:
                                    await client.account.update_status(Account.AccountStatus.ERROR, error=e)
                                except Exception as update_error:
                                    self.logger.warning(f"Failed to update account status for {client.phone_number}: {update_error}")
                                return
                    if attempt == retries:   # Optionally, log/report if all retries failed
                        self.logger.error(f"Client {client.account_id} failed to react to post {post.post_id} after {retries} attempts due to repeated FloodWaitError.")
                        await reporter.event(run_id, self.task_id, "ERROR", "error.worker.react.max_retries", f"Client {client.phone_number} failed to react to post {post.post_id} after {retries} FloodWaitError retries.", {"client": client.phone_number, "post_id": post.post_id, "retries": retries})
                
                # CRITICAL: Add delay between reactions to prevent spam detection
                min_delay = config.get('delays', {}).get('min_delay_between_reactions', 3)
                max_delay = config.get('delays', {}).get('max_delay_between_reactions', 8)
                inter_reaction_delay = random.uniform(min_delay, max_delay)
                self.logger.debug(f"Waiting {inter_reaction_delay:.2f}s before next reaction (anti-spam delay)")
                await asyncio.sleep(inter_reaction_delay)


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
            # Ensure that an unhandled exception in the main task is persisted
            self._task.add_done_callback(lambda t, s=self: asyncio.create_task(s._handle_main_done(t)))
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
