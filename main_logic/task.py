from pandas import Timestamp
import asyncio, random
from dataclasses import dataclass
from typing import Optional

from telethon import errors
from pymongo import errors as mg_errors
from pandas import errors as pd_errors

from main_logic.agent import Account, Client
from main_logic.post import Post
from auxilary_logic.reporter import Reporter
from utils.logger import setup_logger, load_config, crash_handler, handle_task_exception
from main_logic.schemas import TaskStatus, status_name
from auxilary_logic.telethon_error_handler import map_telethon_exception, reporter_payload_from_mapping

config = load_config()


@dataclass
class WorkerResult:
    """Result of a client worker execution.
    
    Attributes:
        success: True if worker completed its work successfully
        phone_number: Phone number of the account used by this worker
        failure_reason: Reason for failure if success is False (e.g., 'account_issue', 'error')
        error: Optional exception if worker failed due to error
    """
    success: bool
    phone_number: str
    failure_reason: Optional[str] = None  # 'account_issue', 'error', etc.
    error: Optional[Exception] = None


def _status_name(status) -> str:
    """Return a stable string for a status value that may be an Enum or a plain string."""
    # Delegate to central helper in schemas for consistent behavior across the repo
    try:
        from main_logic.schemas import status_name as _sn
        return _sn(status)
    except Exception:
        try:
            if hasattr(status, 'name'):
                return status.name
        except Exception:
            pass
        return str(status)


class Task:

    logger = setup_logger("main", "main.log")

    # Use centralized TaskStatus from schemas.py
    TaskStatus = TaskStatus

    def __init__(self, name, post_ids, accounts, action, task_id=None, description=None, status=None, created_at=None, updated_at=None):
        self.task_id = task_id
        self.name = name
        self.description = description
        self.post_ids = sorted(post_ids) if post_ids is not None else []
        self.accounts = accounts if accounts is not None else []
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
        from main_logic.database import get_db
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
        from main_logic.database import get_db
        
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
        from main_logic.database import get_db
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
                self._clients = await Client.connect_clients(accounts, self.logger, task_id=self.task_id)
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
                    
                    # Analyze worker results to determine task status
                    successful_workers = 0
                    account_failure_workers = 0
                    exception_workers = 0
                    
                    for idx, result in enumerate(results):
                        if isinstance(result, Exception):
                            # Worker raised an unhandled exception
                            exception_workers += 1
                            client_info = self._clients[idx].account_id if idx < len(self._clients) else "unknown"
                            self.logger.error(f"Error in worker for client {client_info}: {result}")
                            await reporter.event(run_id, self.task_id, "WARNING", "error.worker_exception", 
                                               f"Worker for client {client_info} raised an exception: {result}")
                        elif isinstance(result, WorkerResult):
                            # Worker returned a result object
                            if result.success:
                                successful_workers += 1
                            elif result.failure_reason == 'account_issue':
                                account_failure_workers += 1
                            else:
                                exception_workers += 1
                        else:
                            # Legacy: worker returned without explicit result (assume success)
                            successful_workers += 1
                    
                    total_workers = len(results)
                    self.logger.info(f"Worker results for task {self.task_id}: {successful_workers} successful, "
                                   f"{account_failure_workers} account failures, {exception_workers} exceptions "
                                   f"(total: {total_workers})")
                    await reporter.event(run_id, self.task_id, "INFO", "info.action.worker_summary",
                                       f"Worker summary: {successful_workers} successful, {account_failure_workers} account failures, {exception_workers} exceptions",
                                       {"successful": successful_workers, "account_failures": account_failure_workers, 
                                        "exceptions": exception_workers, "total": total_workers})
                    
                    # Determine final status based on results:
                    # - If any worker succeeded -> FINISHED
                    # - If all workers failed due to account issues (no exceptions) -> FAILED
                    # - If there were exceptions -> CRASHED (handled by done callbacks)
                    if successful_workers > 0:
                        self._final_status = Task.TaskStatus.FINISHED
                    elif account_failure_workers > 0 and exception_workers == 0:
                        # All failures were due to account issues, task itself ran correctly
                        self._final_status = Task.TaskStatus.FAILED
                        self.logger.warning(f"Task {self.task_id} failed: all {account_failure_workers} workers failed due to account issues")
                        await reporter.event(run_id, self.task_id, "WARNING", "warn.task_failed",
                                           f"Task failed: all {account_failure_workers} workers failed due to account issues")
                    else:
                        # Exception workers present -> will be marked CRASHED by handlers
                        self._final_status = Task.TaskStatus.CRASHED
                    
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
                self._clients = await Client.disconnect_clients(self._clients, self.logger, task_id=self.task_id)
                self._clients = None
                self.updated_at = Timestamp.now()
                self._task = None  # Mark task as finished
            
            await reporter.event(run_id, self.task_id, "INFO", "info.run_end", "Run has ended.")
            # Determine final status based on worker results
            # Use _final_status if set by worker analysis, otherwise fall back to FINISHED
            final_status = getattr(self, '_final_status', Task.TaskStatus.FINISHED)
            
            # Only update if not already marked CRASHED by exception handlers
            if self.status != Task.TaskStatus.CRASHED:
                self.status = final_status
                await self._update_status()
                if self.status == Task.TaskStatus.FINISHED:
                    self.logger.info(f"Task {self.task_id} completed successfully.")
                elif self.status == Task.TaskStatus.FAILED:
                    self.logger.warning(f"Task {self.task_id} failed due to account issues (all workers failed).")
                else:
                    self.logger.info(f"Task {self.task_id} ended with status {status_name(self.status)}.")
            else:
                # status already persisted as CRASHED by callbacks/handlers
                try:
                    self.logger.info(f"Task {self.task_id} ended with status {status_name(self.status)}.")
                except Exception:
                    pass
            return
        await reporter.stop()  # stop reporter and flush            

    @crash_handler
    async def client_worker(self, client: Client, posts: list[Post], reporter: Reporter, run_id) -> WorkerResult:
        """Execute work for a single client across all posts.
        
        Returns:
            WorkerResult indicating success or failure with reason.
        """
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
                            return WorkerResult(success=False, phone_number=client.phone_number, failure_reason='account_issue')
                        except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError):  # To move to auth codeblock
                            self.logger.error(f"Client {client.account_id} has invalid or expired phone code. Stopping worker.")
                            await reporter.event(run_id, self.task_id, "ERROR", "error.worker.phone_code_invalid", 
                                                 f"Client {client.phone_number} has invalid or expired phone code. Stopping worker.",
                                                 {"client": client.phone_number})
                            return WorkerResult(success=False, phone_number=client.phone_number, failure_reason='account_issue')
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
                            return WorkerResult(success=False, phone_number=client.phone_number, failure_reason='account_issue', error=e)
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
                            return WorkerResult(success=False, phone_number=client.phone_number, failure_reason='account_issue', error=e)
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
                            return WorkerResult(success=False, phone_number=client.phone_number, failure_reason='account_issue', error=e)
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
                                return WorkerResult(success=False, phone_number=client.phone_number, failure_reason='account_issue', error=e)
                            else:
                                # Fallback: mark account as ERROR and stop
                                try:
                                    await client.account.update_status(Account.AccountStatus.ERROR, error=e)
                                except Exception as update_error:
                                    self.logger.warning(f"Failed to update account status for {client.phone_number}: {update_error}")
                                return WorkerResult(success=False, phone_number=client.phone_number, failure_reason='account_issue', error=e)
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
        return WorkerResult(success=True, phone_number=client.phone_number)

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
            self._clients = await Client.connect_clients(accounts, self.logger, task_id=self.task_id)
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
                await client.connect(task_id=self.task_id)
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

        from auxilary_logic.reporter import RunEventManager, create_report
        eventManager = RunEventManager()

        events = await eventManager.get_events(self._current_run_id)

        return await create_report(events, type)
