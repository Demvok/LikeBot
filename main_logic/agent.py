"""
Telegram client management utilities using Telethon.

Defines the Client class to manage Telegram connections, session handling,
and common actions (react/comment/undo).

The Account class has been moved to main_logic/account.py but is re-exported
here for backwards compatibility.
"""

import os, random, asyncio, time
from telethon.tl.functions.messages import SendReactionRequest, GetMessagesViewsRequest
from telethon import TelegramClient, functions, types, errors
from telethon.sessions import StringSession
from utils.logger import setup_logger, load_config
from dotenv import load_dotenv
from main_logic.schemas import AccountStatus
from auxilary_logic.telethon_error_handler import map_telethon_exception
from urllib.parse import urlparse, unquote
from auxilary_logic.encryption import (
    decrypt_secret,
    PURPOSE_STRING_SESSION,
)
from auxilary_logic.humaniser import rate_limiter, estimate_reading_time, TelegramAPIRateLimiter
from collections import OrderedDict
from main_logic.channel import normalize_chat_id, Channel

# Re-export Account for backwards compatibility
from main_logic.account import Account

# Explicit exports for `from main_logic.agent import *`
__all__ = ['Account', 'Client', 'TelegramAPIRateLimiter', 'AccountLockManager', 'AccountLockError', 'get_account_lock_manager']

load_dotenv()
api_id = os.getenv('api_id')
api_hash = os.getenv('api_hash')

config = load_config()


# ============= ACCOUNT LOCKING =============

class AccountLockError(Exception):
    """Raised when attempting to use an account that is already locked by another task."""
    def __init__(self, phone_number: str, locked_by_task_id: int, message: str = None):
        self.phone_number = phone_number
        self.locked_by_task_id = locked_by_task_id
        self.message = message or f"Account {phone_number} is already in use by task {locked_by_task_id}"
        super().__init__(self.message)


class AccountLockManager:
    """
    Thread-safe manager for account locks.
    
    Tracks which accounts are currently in use by tasks to prevent
    concurrent access from multiple tasks.
    
    Usage:
        lock_manager = get_account_lock_manager()
        
        # Acquire lock (raises AccountLockError if already locked)
        lock_manager.acquire(phone_number, task_id)
        
        # Release lock
        lock_manager.release(phone_number, task_id)
        
        # Check if locked
        if lock_manager.is_locked(phone_number):
            info = lock_manager.get_lock_info(phone_number)
    """
    
    _instance = None
    _lock = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._locks = {}  # {phone_number: {'task_id': int, 'locked_at': datetime}}
            cls._instance._async_lock = None  # Will be initialized on first use
        return cls._instance
    
    async def _ensure_lock(self):
        """Ensure async lock is initialized (must be done in async context)."""
        if self._async_lock is None:
            import asyncio
            self._async_lock = asyncio.Lock()
    
    async def acquire(self, phone_number: str, task_id: int = None, force: bool = False) -> bool:
        """
        Acquire a lock on an account.
        
        Args:
            phone_number: The phone number of the account to lock
            task_id: Optional task ID that is acquiring the lock
            force: If True, forcefully acquire lock even if already locked (use with caution)
            
        Returns:
            True if lock was acquired successfully
            
        Raises:
            AccountLockError: If account is already locked by another task
        """
        from datetime import datetime, timezone
        
        await self._ensure_lock()
        
        async with self._async_lock:
            if phone_number in self._locks:
                existing = self._locks[phone_number]
                existing_task_id = existing.get('task_id')
                
                # Same task re-acquiring is OK
                if existing_task_id == task_id and task_id is not None:
                    return True
                
                if not force:
                    raise AccountLockError(
                        phone_number=phone_number,
                        locked_by_task_id=existing_task_id,
                        message=f"Account {phone_number} is already in use by task {existing_task_id} (locked since {existing.get('locked_at')})"
                    )
                
                # Force acquire - log warning
                logger = setup_logger("AccountLock", "main.log")
                logger.warning(f"Force-acquiring lock on {phone_number} from task {existing_task_id} for task {task_id}")
            
            self._locks[phone_number] = {
                'task_id': task_id,
                'locked_at': datetime.now(timezone.utc)
            }
            return True
    
    async def release(self, phone_number: str, task_id: int = None) -> bool:
        """
        Release a lock on an account.
        
        Args:
            phone_number: The phone number of the account to unlock
            task_id: Optional task ID releasing the lock (for validation)
            
        Returns:
            True if lock was released, False if not locked
        """
        await self._ensure_lock()
        
        async with self._async_lock:
            if phone_number not in self._locks:
                return False
            
            existing = self._locks[phone_number]
            existing_task_id = existing.get('task_id')
            
            # Validate task_id if provided
            if task_id is not None and existing_task_id != task_id:
                logger = setup_logger("AccountLock", "main.log")
                logger.warning(
                    f"Task {task_id} attempted to release lock on {phone_number} "
                    f"but it was locked by task {existing_task_id}"
                )
                return False
            
            del self._locks[phone_number]
            return True
    
    async def release_all_for_task(self, task_id: int) -> int:
        """
        Release all locks held by a specific task.
        
        Args:
            task_id: The task ID whose locks should be released
            
        Returns:
            Number of locks released
        """
        await self._ensure_lock()
        
        async with self._async_lock:
            to_release = [
                phone for phone, info in self._locks.items()
                if info.get('task_id') == task_id
            ]
            for phone in to_release:
                del self._locks[phone]
            return len(to_release)
    
    def is_locked(self, phone_number: str) -> bool:
        """Check if an account is currently locked."""
        return phone_number in self._locks
    
    def get_lock_info(self, phone_number: str) -> dict:
        """
        Get lock information for an account.
        
        Returns:
            Dict with 'task_id' and 'locked_at', or None if not locked
        """
        return self._locks.get(phone_number)
    
    def get_all_locks(self) -> dict:
        """Get all current locks. Returns a copy to prevent external modification."""
        return dict(self._locks)
    
    async def clear_all(self) -> int:
        """Clear all locks (use for cleanup/testing). Returns number of locks cleared."""
        await self._ensure_lock()
        async with self._async_lock:
            count = len(self._locks)
            self._locks.clear()
            return count


# Singleton accessor
def get_account_lock_manager() -> AccountLockManager:
    """Get the singleton AccountLockManager instance."""
    return AccountLockManager()


class Client(object):

    def __init__(self, account):
        self.account = account
        # Copy non-conflicting attributes from Account to Client instance.
        # Some names (like phone_number, status, etc.) are exposed on Client
        # as @property delegating to self.account; attempting to setattr on
        # those will raise AttributeError because properties have no setter.
        # To avoid that, skip attributes that are defined as properties on the
        # Client class.
        for attr, val in vars(account).items():
            cls_attr = getattr(self.__class__, attr, None)
            if isinstance(cls_attr, property):  # property exists on Client, skip copying to avoid AttributeError                
                continue
            # set plain attribute on instance
            try:
                setattr(self, attr, val)
            except Exception:
                # Be defensive: if setting fails for any reason, skip it.
                # The Client still retains a reference to the Account so
                # callers can access authoritative values via client.account.
                continue
        
        self.active_emoji_palette = []  # Active emoji palette will be set during task execution from database
        self.palette_ordered = False  # Whether to use emojis sequentially or randomly
        
        self.proxy_name = None # Initialize proxy_name as None - will be set during connection
        
        # Task context for locking
        self._task_id = None  # Task ID that owns this client connection
        self._is_locked = False  # Whether this client holds a lock on the account
        
        # Entity cache with LRU eviction (max 100 entities, 5 min TTL)
        self._entity_cache = OrderedDict()  # {identifier: (entity, timestamp)}
        self._entity_cache_max_size = 100
        self._entity_cache_ttl = 300  # 5 minutes
        
        self.logger = setup_logger(f"{self.phone_number}", f"accounts/account_{self.phone_number}.log")
        self.logger.info(f"Initializing client for {self.phone_number}. Awaiting connection...")
        self.client = None

    def __repr__(self):
        return f"Client({self.account}) connected: {self.is_connected}"
    
    def __str__(self):
        return f"Client ({'connected' if self.is_connected else 'disconnected'}) for {self.phone_number} with session {self.session_name}"

    @property
    def phone_number(self):
        return self.account.phone_number

    @property
    def account_id(self):
        return self.account.account_id

    @property
    def status(self):
        return self.account.status

    @property
    def last_error(self):
        return self.account.last_error

    @property
    def last_error_type(self):
        return self.account.last_error_type

    @property
    def last_error_time(self):
        return self.account.last_error_time

    @property
    def flood_wait_until(self):
        return self.account.flood_wait_until

    @property
    def is_connected(self):
        return self.client.is_connected()

# Connection methods

    async def _get_proxy_config(self):
        """
        Get proxy configuration for this connection.
        Selects the least-used active proxy for load balancing.
        Returns a tuple (proxy_candidates, proxy_data) where:
        - proxy_candidates is a list of proxy dicts to try (ordered by preference)
        - proxy_data is the raw proxy data from database
        """
        from auxilary_logic.proxy import get_proxy_config
        
        candidates, proxy_data = await get_proxy_config(self.phone_number, self.logger)
        
        if proxy_data:
            # Store proxy name for usage tracking (not a permanent assignment)
            self.proxy_name = proxy_data.get('proxy_name')
        
        return candidates, proxy_data

    async def _get_session(self, force_new=False):
        """
        Get the Telethon session for this account.
        
        Args:
            force_new: If True, clears the current session (used when session becomes invalid)
            
        Returns:
            StringSession object
            
        Raises:
            ValueError: If no session exists (user must login via API first)
        """
        if self.session_encrypted and not force_new:
            self.logger.info(f"Using existing session for {self.phone_number}.")
            return StringSession(decrypt_secret(self.session_encrypted, PURPOSE_STRING_SESSION))
        else:
            # No session exists - user must login through the API endpoint
            error_msg = (
                f"No session found for {self.phone_number}. "
                "Please use the /accounts/create API endpoint to login this account first."
            )
            self.logger.error(error_msg)
            raise ValueError(error_msg)

    async def connect(self, task_id: int = None):
        """
        Connect the client to Telegram.
        
        Args:
            task_id: Optional task ID for account locking. If provided, acquires
                     a lock on this account. If the account is already locked by
                     another task, logs a warning but continues (best-effort locking).
        """
        # Attempt to acquire lock if task_id provided
        if task_id is not None:
            self._task_id = task_id
            lock_manager = get_account_lock_manager()
            try:
                await lock_manager.acquire(self.phone_number, task_id)
                self._is_locked = True
                self.logger.debug(f"Acquired lock on account {self.phone_number} for task {task_id}")
            except AccountLockError as e:
                # Log warning but don't fail - allow task to proceed with caution
                self.logger.warning(
                    f"⚠️ ACCOUNT LOCK CONFLICT: {self.phone_number} is already in use by task {e.locked_by_task_id}. "
                    f"Proceeding anyway, but this may cause issues. Consider pausing the other task first."
                )
                self._is_locked = False
        
        retries = config.get('delays', {}).get('connection_retries', 5)
        delay = config.get('delays', {}).get('reconnect_delay', 3)
        
        # Proxy configuration
        proxy_mode = config.get('proxy', {}).get('mode', 'soft')  # 'strict' or 'soft'
        
        session_created = False
        force_new_session = False
        proxy_assigned = False
        proxy_failed = False
        
        for attempt in range(1, retries + 1):
            try:    
                # Try to get session only once per connect() call, unless we need to force a new one
                if not session_created or force_new_session:
                    try:
                        session = await self._get_session(force_new=force_new_session)
                        session_created = True
                        force_new_session = False  # Reset the flag
                    except Exception as e:
                        self.logger.error(f"Session creation failed: {e}")
                        # Don't retry session creation here - it has its own retry logic
                        raise
                
                # Get proxy configuration (skip if proxy already failed in strict mode)
                if proxy_mode == 'strict' and proxy_failed:
                    raise ConnectionError("Strict proxy mode: Proxy connection failed, cannot proceed")
                
                # Get proxy (or None if soft mode and proxy previously failed)
                if proxy_failed and proxy_mode == 'soft':
                    proxy_candidates, proxy_data = None, None
                    self.logger.info(f"Connecting without proxy (fallback after proxy failure)")
                else:
                    proxy_candidates, proxy_data = await self._get_proxy_config()
                    if proxy_candidates:
                        self.logger.info(f"Connecting with proxy record: {proxy_data.get('proxy_name')}")
                    else:
                        self.logger.info(f"Connecting without proxy")

                    selected_candidate = None
                    # If proxy_candidates is a list, try them in order until one connects
                    if proxy_candidates:
                        from main_logic.database import get_db
                        db = get_db()
                        for candidate in proxy_candidates:
                            try:
                                self.logger.debug(f"Attempting connection via proxy candidate {candidate.get('addr')}:{candidate.get('port')}")
                                self.client = TelegramClient(
                                    session=session,
                                    api_id=api_id,
                                    api_hash=api_hash,
                                    proxy=candidate
                                )
                                if not self.client:
                                    raise ValueError("TelegramClient is not initialized.")

                                await self.client.connect()
                                # success for this candidate
                                selected_candidate = candidate
                                # Clear proxy error on success
                                await db.clear_proxy_error(proxy_data.get('proxy_name'))
                                # Increment proxy usage counter once per assigned proxy record
                                if not proxy_assigned:
                                    await db.increment_proxy_usage(proxy_data.get('proxy_name'))
                                    proxy_assigned = True
                                    self.logger.debug(f"Incremented usage counter for proxy {proxy_data.get('proxy_name')}")
                                break
                            except (OSError, TimeoutError, ConnectionError) as proxy_error:
                                # Candidate failed - record the error and try next candidate
                                error_msg = f"Proxy candidate {candidate.get('addr')}:{candidate.get('port')} failed: {type(proxy_error).__name__}: {str(proxy_error)}"
                                self.logger.warning(error_msg)
                                await db.set_proxy_error(proxy_data.get('proxy_name'), error_msg)
                                # try next candidate
                                continue
                        # If none succeeded, raise a ConnectionError to be handled below
                        if selected_candidate is None:
                            proxy_failed = True
                            if proxy_mode == 'strict':
                                raise ConnectionError(f"Strict mode - all proxy candidates failed for {proxy_data.get('proxy_name')}")
                            else:
                                self.logger.warning("Soft proxy mode: all proxy candidates failed, will retry without proxy")
                                # Loop will continue and attempt without proxy
                                continue
                    else:
                        # No proxy - normal client creation
                        self.client = TelegramClient(
                            session=session,
                            api_id=api_id,
                            api_hash=api_hash
                        )
                        if not self.client:
                            raise ValueError("TelegramClient is not initialized.")

                        self.logger.debug(f"Starting client for {self.phone_number}...")
                        await self.client.connect()
                
                # Verify the session is still valid
                try:
                    await self.client.get_me()
                    self.logger.debug(f"Client for {self.phone_number} started successfully.")
                    # Update status to ACTIVE on successful connection
                    await self.account.update_status(AccountStatus.ACTIVE, success=True)
                except errors.AuthKeyUnregisteredError as auth_error:
                    # Centralized handling for auth-key invalid / revoked sessions
                    self.logger.warning(f"Session for {self.phone_number} is invalid/expired. Creating new session...")
                    self.session_encrypted = None
                    force_new_session = True
                    session_created = False

                    mapping = map_telethon_exception(auth_error)
                    try:
                        if mapping.get('status'):
                            await self.account.update_status(mapping['status'], error=auth_error)
                    except Exception as _u:
                        self.logger.warning(f"Failed to update account status for {self.phone_number}: {_u}")

                    # Update database to clear invalid session
                    from main_logic.database import get_db
                    db = get_db()
                    await db.update_account(self.phone_number, {
                        'session_encrypted': None
                    })

                    await self.client.disconnect()

                    # Decrement proxy usage if it was incremented
                    if proxy_assigned and proxy_data:
                        await db.decrement_proxy_usage(proxy_data.get('proxy_name'))
                        proxy_assigned = False
                        self.logger.debug(f"Decremented usage counter for proxy {proxy_data.get('proxy_name')}")

                    continue  # Retry with new session
                except (errors.AuthKeyInvalidError, errors.UserDeactivatedError) as auth_error:
                    self.logger.error(f"Account {self.phone_number} authentication failed: {auth_error}")
                    mapping = map_telethon_exception(auth_error)
                    try:
                        if mapping.get('status'):
                            await self.account.update_status(mapping['status'], error=auth_error)
                    except Exception as _u:
                        self.logger.warning(f"Failed to update account status for {self.phone_number}: {_u}")
                    raise
                except errors.UserDeactivatedBanError as ban_error:
                    self.logger.error(f"Account {self.phone_number} has been banned.")
                    mapping = map_telethon_exception(ban_error)
                    try:
                        if mapping.get('status'):
                            await self.account.update_status(mapping['status'], error=ban_error)
                    except Exception as _u:
                        self.logger.warning(f"Failed to update account status for {self.phone_number}: {_u}")
                    raise
                except errors.UserDeactivatedBanError:
                    self.logger.error(f"Account {self.phone_number} has been banned.")
                    raise
                
                if not self.account_id:
                    await self.update_account_id_from_telegram()
            
                return self
                
            except (errors.AuthKeyUnregisteredError, errors.AuthKeyInvalidError, errors.UserDeactivatedError, errors.UserDeactivatedBanError):
                # Don't retry on these errors - status already updated above
                raise
            except ValueError as e:
                # Session creation errors - don't retry connection
                self.logger.error(f"Failed to create session for {self.phone_number}: {e}")
                await self.account.update_status(AccountStatus.ERROR, error=e)
                raise
            except Exception as e:
                self.logger.error(f"Failed to connect client for {self.phone_number} (attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    await asyncio.sleep(delay)
                else:
                    self.logger.critical(f"All connection attempts failed for {self.phone_number}. Error: {e}")
                    await self.account.update_status(AccountStatus.ERROR, error=e)
                    raise

    async def disconnect(self):
        """Disconnect the client and release account lock if held."""
        retries = config.get('delays', {}).get('connection_retries', 5)
        delay = config.get('delays', {}).get('reconnect_delay', 3)
        attempt = 0
        while attempt < retries:
            try:
                await self.client.disconnect()
                self.logger.info(f"Client for {self.phone_number} disconnected.")
                
                # Release account lock if we hold one
                if self._is_locked:
                    lock_manager = get_account_lock_manager()
                    released = await lock_manager.release(self.phone_number, self._task_id)
                    if released:
                        self.logger.debug(f"Released lock on account {self.phone_number} for task {self._task_id}")
                    self._is_locked = False
                
                # Decrement proxy usage counter if a proxy was used
                if self.proxy_name:
                    from main_logic.database import get_db
                    db = get_db()
                    await db.decrement_proxy_usage(self.proxy_name)
                    self.logger.debug(f"Decremented usage counter for proxy {self.proxy_name}")
                    # Clear proxy name after disconnecting
                    self.proxy_name = None
                
                break
            except Exception as e:
                attempt += 1
                self.logger.error(f"Failed to disconnect client for {self.phone_number} (attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    await asyncio.sleep(delay)
                else:
                    # Even on failure, try to release lock
                    if self._is_locked:
                        try:
                            lock_manager = get_account_lock_manager()
                            await lock_manager.release(self.phone_number, self._task_id)
                            self._is_locked = False
                        except Exception:
                            pass
                    self.logger.critical(f"All disconnection attempts failed for {self.phone_number}. Error: {e}")
                    raise

    async def ensure_connected(self):
        if not self.client or not self.is_connected:
            self.logger.info(f"Client for {self.phone_number} is not connected. Reconnecting...")
            await self.connect()

# Caching with rate limiting for get_entity

    def _get_cache_key(self, identifier):
        """Convert identifier to cache key (normalize format)."""
        if isinstance(identifier, int):
            return f"id:{identifier}"
        elif isinstance(identifier, str):
            # Normalize username format
            username = identifier.strip().lstrip('@').lower()
            # Remove URL parts if present
            if '/' in username:
                username = username.split('/')[-1]
            return f"username:{username}"
        return str(identifier)
    
    def _cleanup_entity_cache(self):
        """Remove expired entries and enforce max size (LRU)."""
        now = time.time()
        # Remove expired entries
        expired_keys = [
            key for key, (entity, timestamp) in self._entity_cache.items()
            if now - timestamp > self._entity_cache_ttl
        ]
        for key in expired_keys:
            del self._entity_cache[key]
        
        # Enforce max size (LRU: remove oldest)
        while len(self._entity_cache) > self._entity_cache_max_size:
            self._entity_cache.popitem(last=False)
    
    async def get_entity_cached(self, identifier):
        """
        Get entity with caching and rate limiting.
        
        Args:
            identifier: Can be username, user_id, or other entity identifier
            
        Returns:
            Entity object from Telegram
        """
        cache_key = self._get_cache_key(identifier)
        now = time.time()
        
        # Check cache first
        if cache_key in self._entity_cache:
            entity, timestamp = self._entity_cache[cache_key]
            if now - timestamp < self._entity_cache_ttl:
                self.logger.debug(f"Cache hit for entity: {cache_key}")
                # Move to end (mark as recently used)
                self._entity_cache.move_to_end(cache_key)
                return entity
            else:
                # Expired, remove it
                del self._entity_cache[cache_key]
        
        # Cache miss - fetch from Telegram with rate limiting
        self.logger.debug(f"Cache miss for entity: {cache_key}, fetching from Telegram")
        await rate_limiter.wait_if_needed('get_entity')
        
        await self.ensure_connected()
        entity = await self.client.get_entity(identifier)
        
        # Store in cache
        self._entity_cache[cache_key] = (entity, now)
        self._cleanup_entity_cache()
        
        return entity
    
    async def _get_or_fetch_channel_data(self, chat_id: int, entity=None):
        """
        Get channel data from database or fetch from Telegram if not exists.
        Minimizes API calls by reusing entity if provided.
        
        Args:
            chat_id: Normalized chat ID
            entity: Optional entity object already fetched (to avoid redundant API calls)
        
        Returns:
            Channel object from database (existing or newly created)
        """
        from main_logic.database import get_db
        from main_logic.channel import Channel
        from datetime import datetime, timezone
        
        db = get_db()
        
        # First, check if channel exists in database
        channel = await db.get_channel(chat_id)
        if channel:
            self.logger.debug(f"Channel {chat_id} found in database")
            return channel
        
        # Channel not in DB - fetch from Telegram
        self.logger.info(f"Channel {chat_id} not in database, fetching from Telegram")
        
        await self.ensure_connected()
        
        # Use provided entity or fetch it
        if entity is None:
            entity = await self.get_entity_cached(chat_id)
        
        # Extract channel data from entity (same as in fetch_and_update_subscribed_channels)
        channel_data = {
            'chat_id': chat_id,
            'is_private': not getattr(entity, 'username', None),
            'channel_name': getattr(entity, 'title', None),
            'has_enabled_reactions': getattr(entity, 'reactions_enabled', True),
            'tags': []
        }
        
        # Get channel hash for private channels
        if hasattr(entity, 'access_hash') and entity.access_hash:
            channel_data['channel_hash'] = str(entity.access_hash)
        else:
            channel_data['channel_hash'] = ""
        
        # Try to get full channel info for discussion group and reaction settings
        try:
            full_channel = await self.client(functions.channels.GetFullChannelRequest(
                channel=entity
            ))
            
            # Check for linked discussion group
            if hasattr(full_channel.full_chat, 'linked_chat_id'):
                channel_data['discussion_chat_id'] = full_channel.full_chat.linked_chat_id
            else:
                channel_data['discussion_chat_id'] = None
            
            # Check reaction settings more accurately
            if hasattr(full_channel.full_chat, 'available_reactions'):
                reactions = full_channel.full_chat.available_reactions
                if reactions is None:
                    channel_data['has_enabled_reactions'] = False
                elif hasattr(reactions, 'reactions'):
                    channel_data['has_enabled_reactions'] = len(reactions.reactions) > 0
            
            # Check if reactions are only for subscribers
            channel_data['reactions_only_for_subscribers'] = False
            
        except Exception as e:
            self.logger.warning(f"Could not fetch full channel info for {chat_id}: {e}")
            channel_data['discussion_chat_id'] = None
            channel_data['reactions_only_for_subscribers'] = False
        
        # Add timestamps
        channel_data['created_at'] = datetime.now(timezone.utc)
        channel_data['updated_at'] = datetime.now(timezone.utc)
        
        # Save to database
        try:
            await db.add_channel(channel_data)
            self.logger.info(f"Added new channel to database: {channel_data['channel_name']} ({chat_id})")
        except ValueError:
            # Channel was added by another process (race condition)
            self.logger.debug(f"Channel {chat_id} already exists (race condition), fetching from DB")
            channel = await db.get_channel(chat_id)
            if channel:
                return channel
        
        return Channel.from_dict(channel_data)
    
    async def _check_subscription(self, chat_id: int) -> bool:
        """
        Check if account is subscribed to a channel.
        
        Args:
            chat_id: Normalized chat ID to check
        
        Returns:
            True if subscribed, False otherwise
        """
        # Check account's subscribed_to list
        if hasattr(self.account, 'subscribed_to') and self.account.subscribed_to:
            is_subscribed = chat_id in self.account.subscribed_to
            self.logger.debug(f"Subscription check for {chat_id}: {is_subscribed}")
            return is_subscribed
        
        self.logger.debug(f"No subscription list available for account, assuming not subscribed to {chat_id}")
        return False

# Mass methods

    @classmethod
    async def connect_clients(cls, accounts: list[Account], logger, task_id: int = None):
        """
        Connect multiple clients in parallel.
        
        Args:
            accounts: List of Account objects to connect
            logger: Logger instance for status messages
            task_id: Optional task ID for account locking. When provided,
                     each client will attempt to acquire a lock on its account.
                     
        Returns:
            List of connected Client objects, or None if no clients connected
        """
        if logger:
            logger.info(f"Connecting clients for {len(accounts)} accounts...")

        clients = [Client(account) for account in accounts]
        
        # Connect all clients in parallel, passing task_id for locking
        await asyncio.gather(*(client.connect(task_id=task_id) for client in clients))

        if logger:
            logger.info(f"Connected clients for {len(clients)} accounts.")

        return clients if clients else None
    
    @classmethod
    async def disconnect_clients(cls, clients: list["Client"], logger, task_id: int = None):
        """
        Disconnect multiple clients in parallel.
        
        Args:
            clients: List of Client objects to disconnect
            logger: Logger instance for status messages
            task_id: Optional task ID. If provided and clients failed to disconnect,
                     will forcefully release all locks for this task as cleanup.
                     
        Returns:
            None to indicate all clients are disconnected
        """
        if not clients:
            if logger:
                logger.info("No clients to disconnect.")
            return None
            
        if logger:
            logger.info(f"Disconnecting {len(clients)} clients...")

        await asyncio.gather(*(client.disconnect() for client in clients))

        # Cleanup: ensure all locks for this task are released
        # This handles edge cases where disconnect might have failed silently
        if task_id is not None:
            lock_manager = get_account_lock_manager()
            released = await lock_manager.release_all_for_task(task_id)
            if released > 0 and logger:
                logger.debug(f"Released {released} remaining locks for task {task_id}")

        if logger:
            logger.info(f"Disconnected {len(clients)} clients.")

        return None  # Return None to indicate all clients are disconnected

# Additional utility methods

    def _extract_identifier_from_link(self, link: str):
        """
        Extract username or chat_id from a Telegram message link.
        Used when we need to fetch an entity from a link.
        
        Args:
            link: Telegram message link
            
        Returns:
            str or int: Username (for public channels) or chat_id (for /c/ links)
        """
        try:
            link = link.strip()
            if '://' not in link:
                link = 'https://' + link
            
            parsed = urlparse(unquote(link))
            path = parsed.path.lstrip('/')
            segments = [seg for seg in path.split('/') if seg != '']
            
            if not segments or len(segments) < 2:
                raise ValueError(f"Link format not recognized: {link}")
            
            # /c/<raw>/<msg> format - return chat_id
            if segments[0] == 'c':
                if len(segments) < 3:
                    raise ValueError(f"Invalid /c/ link: {link}")
                raw = segments[1]
                if not raw.isdigit():
                    raise ValueError(f"Non-numeric in /c/ link: {link}")
                return int(f"-100{raw}")
            
            # /s/<username>/<msg> or /<username>/<msg> format - return username
            if segments[0] == 's':
                if len(segments) < 3:
                    raise ValueError(f"Invalid /s/ link: {link}")
                username = segments[1]
            else:
                username = segments[0]
            
            return username.lstrip('@')
            
        except Exception as e:
            self.logger.warning(f"Error extracting identifier from '{link}': {e}")
            raise
    
    async def get_message_ids(self, link: str):
        """
        Extract (chat_id, message_id, entity) from a Telegram link of types:
        - https://t.me/c/<raw>/<msg>
        - https://t.me/<username>/<msg>
        - https://t.me/s/<username>/<msg>
        - with or without @, with query params
        
        Returns:
            tuple: (chat_id, message_id, entity) where entity is None for /c/ links
                   and the cached entity object for username-based links
        """
        try:
            link = link.strip()
            if '://' not in link:
                link = 'https://' + link
            
            # First, try to find a stored Post in DB with the same link. If it exists and
            # is already validated, use its chat_id/message_id and skip network resolution.
            try:
                from main_logic.database import get_db
                db = get_db()

                try:
                    post_obj = await db.get_post_by_link(link)
                    if post_obj and getattr(post_obj, 'is_validated', False):
                        # Ensure both ids exist and are integers
                        if post_obj.chat_id is not None and post_obj.message_id is not None:
                            try:
                                chat_id_db = int(post_obj.chat_id)
                                message_id_db = int(post_obj.message_id)
                                self.logger.debug(f"Found validated post in DB for link {link}: chat_id={chat_id_db}, message_id={message_id_db}")
                                return chat_id_db, message_id_db, None  # No entity for DB cached posts
                            except Exception:
                                self.logger.debug(f"DB post for link {link} had non-integer ids, falling back to resolution")
                except Exception as _db_err:
                    # Do not fail on DB errors; fall back to Telethon resolution
                    self.logger.debug(f"DB lookup by message_link failed for '{link}': {_db_err}")
            except Exception:
                pass
            
            # Parse link to extract message_id
            parsed = urlparse(unquote(link))
            path = parsed.path.lstrip('/')
            segments = [seg for seg in path.split('/') if seg != '']
            if not segments or len(segments) < 2:
                raise ValueError(f"Link format not recognized: {link}")

            # Extract message_id from the last segment
            # For /c/<raw>/<msg>, /s/<username>/<msg>, /<username>/<msg>
            msg = segments[-1]
            if not msg.isdigit():
                raise ValueError(f"Message part is not numeric: {link}")
            message_id = int(msg)
            
            # Use _extract_identifier_from_link to get the identifier (username or chat_id)
            # This handles all link formats: /c/, /s/, and direct username
            identifier = self._extract_identifier_from_link(link)
            
            # If identifier is an int (from /c/ links), return immediately without entity
            if isinstance(identifier, int):
                # /c/ link - identifier is already the chat_id
                return identifier, message_id, None
            
            # Identifier is a username - fetch entity with caching and rate limiting
            await self.ensure_connected()
            
            try:
                entity = await self.get_entity_cached(identifier)
            except (errors.AuthKeyUnregisteredError, errors.AuthKeyInvalidError, errors.SessionRevokedError, errors.UserDeactivatedError, errors.UserDeactivatedBanError) as auth_error:
                # Session is invalid/expired/revoked or account is banned - re-raise for proper handling upstream
                self.logger.error(f"Session invalid/expired or account deactivated while resolving '{identifier}': {auth_error}")
                raise
            except Exception as e1:
                # Try several fallbacks: full URL with scheme, http, www variant, and @username.
                last_exc = e1
                tried = []
                candidates = [
                    f"https://{parsed.netloc}/{identifier}",
                    f"http://{parsed.netloc}/{identifier}",
                    f"{parsed.netloc}/{identifier}",
                    f"@{identifier}",
                ]
                entity = None
                for candidate in candidates:
                    tried.append(candidate)
                    try:
                        entity = await self.get_entity_cached(candidate)
                        break
                    except (errors.AuthKeyUnregisteredError, errors.AuthKeyInvalidError, errors.SessionRevokedError, errors.UserDeactivatedError, errors.UserDeactivatedBanError) as auth_error:
                        # Re-raise auth related errors immediately so they can be handled upstream
                        self.logger.error(f"Session invalid/expired or account deactivated while resolving '{identifier}' using '{candidate}': {auth_error}")
                        raise
                    except Exception as e2:
                        last_exc = e2
                        self.logger.debug(f"get_entity failed for candidate '{candidate}': {e2}")

                if entity is None:
                    self.logger.error(f"Failed to resolve username '{identifier}' from link {link}. Tried: {tried}. Errors: {e1}, last: {last_exc}")
                    raise ValueError(f"Cannot resolve username '{identifier}' from link {link}")

            chat_id = normalize_chat_id(entity.id)
            return chat_id, message_id, entity  # Return entity to avoid redundant get_entity call

        except (errors.AuthKeyUnregisteredError, errors.AuthKeyInvalidError, errors.UserDeactivatedError, errors.UserDeactivatedBanError):
            # Re-raise auth errors without wrapping them
            raise
        except Exception as e:
            self.logger.warning(f"Error extracting IDs from '{link}': {e}")
            raise

    async def get_message_content(self, chat_id=None, message_id=None, message_link=None) -> str | None:
        """
        Retrieve the content of a single message by chat and message_id.
        """
        try:
            await self.ensure_connected()
            if message_link and not (message_id and chat_id):
                entity, message = await self._get_message_ids(message_link)
                return message.message if message else None
            else:
                if not chat_id or not message_id:
                    raise ValueError("Either message_link or both chat_id and message_id must be provided.")

            # Use cached get_entity with rate limiting
            entity = await self.get_entity_cached(chat_id)
            # Rate limit message fetching
            await rate_limiter.wait_if_needed('get_messages')
            message = await self.client.get_messages(entity, ids=message_id)
            return message.message if message else None
        except Exception as e:
            self.logger.warning(f"Error retrieving message content: {e}")
            raise

    async def update_account_id_from_telegram(self):
        """Fetch account id from Telegram and update the account record in accounts file."""
        try:
            await self.ensure_connected()
            me = await self.client.get_me()
            account_id = me.id if hasattr(me, 'id') else None
            if account_id:
                from main_logic.database import get_db  # Avoid circular import if any
                db = get_db()
                await db.update_account(self.phone_number, {'account_id': account_id})
                self.logger.info(f"Updated account_id for {self.phone_number} to {account_id}")
                self.account.account_id = account_id
                self.account_id = account_id
            else:
                self.logger.warning("Could not fetch account_id from Telegram.")
        except Exception as e:
            self.logger.error(f"Error updating account_id from Telegram: {e}")
            raise

    async def fetch_and_update_subscribed_channels(self):
        """
        Fetch all channels the account is subscribed to from Telegram,
        update the account's subscribed_to field in database,
        and upsert channel data to the channels collection.
        
        This method minimizes API calls by:
        - Using a single GetDialogsRequest to fetch all channels
        - Batch processing channel data
        - Only fetching full channel details for channels not in DB
        
        Returns:
            List of chat_ids that were added/updated
        """
        from main_logic.database import get_db
        from main_logic.channel import Channel
        from datetime import datetime, timezone
        
        db = get_db()
        
        try:
            await self.ensure_connected()
            self.logger.info(f"Fetching subscribed channels for {self.phone_number}")
            
            # Get all dialogs (chats/channels) with a single API call
            # This returns channels, groups, and private chats
            dialogs = await self.client.get_dialogs()
            
            # Filter for channels only (not groups or private chats)
            # Channel types: Channel (broadcast) and Megagroup (discussion-enabled channels)
            channel_dialogs = [
                d for d in dialogs 
                if hasattr(d.entity, 'broadcast') or 
                   (hasattr(d.entity, 'megagroup') and d.entity.megagroup)
            ]
            
            self.logger.info(f"Found {len(channel_dialogs)} subscribed channels")
            
            if not channel_dialogs:
                self.logger.info("No channels found - account not subscribed to any channels")
                # Update account with empty list
                await db.update_account(self.phone_number, {'subscribed_to': []})
                self.account.subscribed_to = []
                return []
            
            chat_ids = []
            channels_to_upsert = []
            
            for dialog in channel_dialogs:
                entity = dialog.entity
                chat_id = normalize_chat_id(entity.id)
                chat_ids.append(chat_id)
                
                # Extract channel data from the entity we already have
                # No additional API calls needed!
                channel_data = {
                    'chat_id': chat_id,
                    'is_private': not getattr(entity, 'username', None),  # No username = private
                    'channel_name': getattr(entity, 'title', None),
                    'has_enabled_reactions': getattr(entity, 'reactions_enabled', True),
                    'tags': []  # Will be managed manually by user
                }
                
                # Get channel hash for private channels (access_hash)
                if hasattr(entity, 'access_hash') and entity.access_hash:
                    channel_data['channel_hash'] = str(entity.access_hash)
                else:
                    channel_data['channel_hash'] = ""
                
                # Check if channel has linked discussion group
                # Only fetch if we don't already have this channel in DB
                existing_channel = await db.get_channel(chat_id)
                
                if existing_channel:
                    # Channel exists - only update basic fields that might have changed
                    update_data = {
                        'channel_name': channel_data['channel_name'],
                        'is_private': channel_data['is_private'],
                        'has_enabled_reactions': channel_data['has_enabled_reactions']
                    }
                    await db.update_channel(chat_id, update_data)
                    self.logger.debug(f"Updated existing channel: {channel_data['channel_name']} ({chat_id})")
                else:
                    # New channel - try to get discussion group info
                    # This is the only additional API call we make, and only for new channels
                    try:
                        full_channel = await self.client(functions.channels.GetFullChannelRequest(
                            channel=entity
                        ))
                        
                        # Check for linked discussion group
                        if hasattr(full_channel.full_chat, 'linked_chat_id'):
                            channel_data['discussion_chat_id'] = full_channel.full_chat.linked_chat_id
                        else:
                            channel_data['discussion_chat_id'] = None
                        
                        # Check reaction settings more accurately
                        if hasattr(full_channel.full_chat, 'available_reactions'):
                            reactions = full_channel.full_chat.available_reactions
                            if reactions is None:
                                channel_data['has_enabled_reactions'] = False
                            elif hasattr(reactions, 'reactions'):
                                channel_data['has_enabled_reactions'] = len(reactions.reactions) > 0
                        
                        # Check if reactions are only for subscribers
                        if hasattr(full_channel.full_chat, 'reactions_limit'):
                            # If there's a limit, it might be subscriber-only
                            # This is a heuristic - Telegram doesn't expose this directly
                            channel_data['reactions_only_for_subscribers'] = False
                        else:
                            channel_data['reactions_only_for_subscribers'] = False
                            
                    except Exception as e:
                        self.logger.warning(f"Could not fetch full channel info for {chat_id}: {e}")
                        # Use defaults if full channel fetch fails
                        channel_data['discussion_chat_id'] = None
                        channel_data['reactions_only_for_subscribers'] = False
                    
                    # Add timestamps
                    channel_data['created_at'] = datetime.now(timezone.utc)
                    channel_data['updated_at'] = datetime.now(timezone.utc)
                    
                    channels_to_upsert.append(channel_data)
            
            # Batch insert new channels
            for channel_data in channels_to_upsert:
                try:
                    await db.add_channel(channel_data)
                    self.logger.debug(f"Added new channel: {channel_data['channel_name']} ({channel_data['chat_id']})")
                except ValueError as e:
                    # Channel already exists (race condition) - update instead
                    self.logger.debug(f"Channel {channel_data['chat_id']} already exists, updating: {e}")
                    update_data = {k: v for k, v in channel_data.items() if k not in ['chat_id', 'created_at']}
                    await db.update_channel(channel_data['chat_id'], update_data)
            
            # Update account's subscribed_to field
            await db.update_account(self.phone_number, {'subscribed_to': chat_ids})
            self.account.subscribed_to = chat_ids
            
            self.logger.info(
                f"Successfully updated subscriptions for {self.phone_number}: "
                f"{len(chat_ids)} channels, {len(channels_to_upsert)} new channels added"
            )
            
            return chat_ids
            
        except Exception as e:
            self.logger.error(f"Error fetching subscribed channels: {e}")
            raise


# Basic actions

    async def _react(self, message, target_chat, channel: Channel = None):
        """
        React to a message with an emoji from the active palette.
        
        Args:
            message: Telethon message object
            target_chat: Target chat entity
            channel: Optional Channel object with metadata
        
        Raises:
            ValueError: If no valid emojis are available after filtering
        """
        await self.ensure_connected()
        
        # Convert entity to InputPeer to avoid "Could not find input entity" errors
        input_peer = await self.client.get_input_entity(target_chat)
        
        # Check subscription status and warn if not subscribed
        chat_id = normalize_chat_id(target_chat.id if hasattr(target_chat, 'id') else target_chat)
        is_subscribed = await self._check_subscription(chat_id)
        
        if not is_subscribed:
            self.logger.warning(
                f"⚠️  DANGER: Account {self.phone_number} is NOT subscribed to channel {chat_id}. "
                f"Reacting to posts from unsubscribed channels significantly increases ban risk. "
                f"Telegram may flag this as spam behavior."
            )
        
        await self.client(GetMessagesViewsRequest(
            peer=input_peer,
            id=[message.id],
            increment=True
        ))

        # CRITICAL: ALWAYS add reading time delay to prevent spam detection
        # Even at humanisation_level 0, we need basic delays
        humanisation_level = config.get('delays', {}).get('humanisation_level', 1)
        if humanisation_level >= 1:
            # Full humanization: use message content from message object (already fetched)
            msg_content = message.message if hasattr(message, 'message') else None
            if msg_content:
                reading_time = estimate_reading_time(msg_content)
                self.logger.debug(f"Estimated reading time: {reading_time} seconds")
                await asyncio.sleep(reading_time)
            else:
                # Message content empty - use fallback delay
                fallback_delay = random.uniform(2, 5)
                self.logger.debug(f"Message content empty, using fallback delay: {fallback_delay:.2f}s")
                await asyncio.sleep(fallback_delay)
        else:
            # Minimal humanization: just add a random delay to prevent instant reactions
            minimal_delay = random.uniform(1.5, 4)
            self.logger.debug(f"Minimal humanization delay: {minimal_delay:.2f}s")
            await asyncio.sleep(minimal_delay)

        # Check for active emoji palette
        if not self.active_emoji_palette:
            error_msg = "No emoji palette configured for this client. Palette must be set before reacting."
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        # Get allowed reactions from message (already fetched - use it directly!)
        allowed_reactions = None
        try:
            # Use the message object we already have instead of fetching again
            if hasattr(message, 'reactions') and message.reactions:
                # Check available_reactions if it exists (Telegram's list of allowed emojis)
                if hasattr(message.reactions, 'available_reactions'):
                    available_reactions_list = []
                    for reaction in message.reactions.available_reactions:
                        if hasattr(reaction, 'emoticon'):
                            available_reactions_list.append(reaction.emoticon)
                    
                    if available_reactions_list:
                        self.logger.debug(f"Message has restricted reactions: {available_reactions_list}")
                        # If available_reactions exists, it means only these are allowed
                        allowed_reactions = available_reactions_list
            
            if not allowed_reactions:
                self.logger.debug("Message has no reaction restrictions - will try palette emojis")
                
        except Exception as e:
            self.logger.warning(f"Could not fetch message reactions metadata: {e}. Will try palette emojis.")
        
        # Filter palette based on allowed reactions (only if explicitly restricted)
        if allowed_reactions:
            # Filter to only emojis that are in the allowed list
            filtered_palette = [emoji for emoji in self.active_emoji_palette if emoji in allowed_reactions]
            
            if not filtered_palette:
                # None of our palette emojis are in the allowed reactions
                error_msg = f"None of the palette emojis {self.active_emoji_palette} are in allowed reactions {allowed_reactions}"
                self.logger.error(error_msg)
                raise ValueError(error_msg)
            
            self.logger.info(f"Filtered palette from {len(self.active_emoji_palette)} to {len(filtered_palette)} emojis based on allowed reactions")
        else:
            # No explicit restrictions - use full palette and rely on try-catch
            filtered_palette = self.active_emoji_palette.copy()
            self.logger.debug(f"Using full palette ({len(filtered_palette)} emojis) - will try until one works")
        
        if not filtered_palette:
            error_msg = f"No valid emojis available after filtering. Palette: {self.active_emoji_palette}, Allowed: {allowed_reactions}"
            self.logger.error(error_msg)
            raise ValueError(error_msg)
        
        # Simulate human-like delay before reacting (additional unpredictability)
        min_delay = config.get('delays', {}).get('min_delay_before_reaction', 1)
        max_delay = config.get('delays', {}).get('max_delay_before_reaction', 3)
        pre_reaction_delay = random.uniform(min_delay, max_delay)
        self.logger.debug(f"Pre-reaction delay: {pre_reaction_delay:.2f}s")
        await asyncio.sleep(pre_reaction_delay)
        
        # Try to send reaction
        if self.palette_ordered:
            # Ordered mode: try emojis in sequence until one succeeds
            last_error = None
            for idx, emoticon in enumerate(filtered_palette, 1):
                try:
                    self.logger.debug(f"Attempting emoji (ordered, rank {idx}/{len(filtered_palette)}): {emoticon}")
                    # Rate limit reaction sending
                    await rate_limiter.wait_if_needed('send_reaction')
                    await self.client(SendReactionRequest(
                        peer=input_peer,
                        msg_id=message.id,
                        reaction=[types.ReactionEmoji(emoticon=emoticon)],
                        add_to_recent=True
                    ))
                    self.logger.info(f"Successfully reacted with {emoticon} (rank {idx}/{len(filtered_palette)})")
                    return  # Success - exit method
                except errors.ReactionInvalidError as e:
                    # This emoji is not allowed, try next one
                    self.logger.warning(f"Emoji {emoticon} not allowed (rank {idx}/{len(filtered_palette)}): {e}")
                    last_error = e
                    if idx < len(filtered_palette):
                        continue  # Try next emoji
                    else:
                        # No more emojis to try
                        error_msg = f"All {len(filtered_palette)} emojis failed. Last error: {last_error}"
                        self.logger.error(error_msg)
                        raise ValueError(error_msg)
                except Exception as e:
                    # Other error - don't retry, raise immediately
                    error_msg = f"Failed to send reaction {emoticon} to message {message.id}: {e}"
                    self.logger.error(error_msg)
                    raise RuntimeError(error_msg)
        else:
            # Random mode: try random emojis until one succeeds or all fail
            # Shuffle palette to try in random order
            shuffled_palette = filtered_palette.copy()
            random.shuffle(shuffled_palette)
            
            last_error = None
            for idx, emoticon in enumerate(shuffled_palette, 1):
                try:
                    self.logger.debug(f"Attempting emoji (random, attempt {idx}/{len(shuffled_palette)}): {emoticon}")
                    # Rate limit reaction sending
                    await rate_limiter.wait_if_needed('send_reaction')
                    await self.client(SendReactionRequest(
                        peer=input_peer,
                        msg_id=message.id,
                        reaction=[types.ReactionEmoji(emoticon=emoticon)],
                        add_to_recent=True
                    ))
                    self.logger.info(f"Successfully reacted with {emoticon} (attempt {idx}/{len(shuffled_palette)})")
                    return  # Success - exit method
                except errors.ReactionInvalidError as e:
                    # This emoji is not allowed, try another random one
                    self.logger.warning(f"Emoji {emoticon} not allowed (attempt {idx}/{len(shuffled_palette)}): {e}")
                    last_error = e
                    if idx < len(shuffled_palette):
                        continue  # Try next random emoji
                    else:
                        # No more emojis to try
                        error_msg = f"All {len(shuffled_palette)} emojis failed. Last error: {last_error}"
                        self.logger.error(error_msg)
                        raise ValueError(error_msg)
                except Exception as e:
                    # Other error - don't retry, raise immediately
                    error_msg = f"Failed to send reaction {emoticon} to message {message.id}: {e}"
                    self.logger.error(error_msg)
                    raise RuntimeError(error_msg)

    async def _comment(self, message, target_chat, content, channel: Channel = None):
        await self.ensure_connected()
        
        # Convert entity to InputPeer to avoid "Could not find input entity" errors
        input_peer = await self.client.get_input_entity(target_chat)
        
        # Check subscription requirements for commenting
        chat_id = normalize_chat_id(target_chat.id if hasattr(target_chat, 'id') else target_chat)
        is_subscribed_to_channel = await self._check_subscription(chat_id)
        
        # If not subscribed to channel
        if not is_subscribed_to_channel:
            # Check if channel is private
            if channel and channel.is_private:
                error_msg = (
                    f"Cannot comment on private channel {chat_id}: "
                    f"account {self.phone_number} is not subscribed to this channel. "
                    f"Private channels require subscription to comment."
                )
                self.logger.error(error_msg)
                raise ValueError(error_msg)
            
            # Channel is public - check discussion group subscription
            if channel and channel.has_discussion_group:
                discussion_chat_id = channel.discussion_chat_id
                is_subscribed_to_discussion = await self._check_subscription(discussion_chat_id)
                
                if not is_subscribed_to_discussion:
                    error_msg = (
                        f"Cannot comment on channel {chat_id}: "
                        f"account {self.phone_number} is not subscribed to the discussion group (chat_id: {discussion_chat_id}). "
                        f"You must subscribe to the discussion group to comment on posts from unsubscribed channels."
                    )
                    self.logger.error(error_msg)
                    raise ValueError(error_msg)
                
                self.logger.info(
                    f"Account {self.phone_number} is not subscribed to channel {chat_id}, "
                    f"but is subscribed to discussion group {discussion_chat_id}. Proceeding with comment."
                )
            else:
                # No discussion group info - warn but proceed
                self.logger.warning(
                    f"Account {self.phone_number} is not subscribed to channel {chat_id}. "
                    f"No discussion group info available. Attempting to comment anyway."
                )

        await self.client(GetMessagesViewsRequest(
            peer=input_peer,
            id=[message.id],
            increment=True
        ))

        if config.get('delays', {}).get('humanisation_level', 1) >= 1:  # If humanisation level is 1 it should consider reading time
            msg_content = await self.get_message_content(chat_id=target_chat.id if hasattr(target_chat, 'id') else target_chat, message_id=message.id)
            reading_time = estimate_reading_time(msg_content)
            self.logger.info(f"Estimated reading time: {reading_time} seconds")
            await asyncio.sleep(reading_time)

        discussion = await self.client(functions.messages.GetDiscussionMessageRequest(
            peer=target_chat,
            msg_id=message.id
        ))
        self.logger.debug(f"Discussion found: {discussion.messages[0].id}")
        
        # Use the discussion message ID, not the original channel message ID
        discussion_message_id = discussion.messages[0].id
        discussion_chat = discussion.chats[0]
        
        # Text typing speed should be added to simulate properly

        await asyncio.sleep(random.uniform(0.5, 2))  # Prevent spam if everything is broken

        # Rate limit message sending
        await rate_limiter.wait_if_needed('send_message')
        await self.client.send_message(
            entity=discussion_chat,
            message=content,
            reply_to=discussion_message_id  # Use discussion message ID, not original message.id
        )

    async def _undo_reaction(self, message, target_chat):
        await self.ensure_connected()
        
        # Convert entity to InputPeer to avoid "Could not find input entity" errors
        input_peer = await self.client.get_input_entity(target_chat)
        
        await self.client(GetMessagesViewsRequest(
            peer=input_peer,
            id=[message.id],
            increment=True
        ))
        
        await asyncio.sleep(random.uniform(0.5, 1))  # Prevent spam if everything is broken
        
        # Rate limit reaction removal
        await rate_limiter.wait_if_needed('send_reaction')
        await self.client(SendReactionRequest(
            peer=input_peer,
            msg_id=message.id,
            reaction=[],  # Empty list removes reaction
            add_to_recent=False
        ))

    async def _undo_comment(self, message, target_chat):
        """
        Deletes all user comments on given post.
        """
        await self.ensure_connected()

        # Convert entity to InputPeer to avoid "Could not find input entity" errors
        input_peer = await self.client.get_input_entity(target_chat)

        await self.client(GetMessagesViewsRequest(
            peer=input_peer,
            id=[message.id],
            increment=True
        ))

        await asyncio.sleep(random.uniform(0.5, 1))  # Prevent spam if everything is broken
        discussion = await self.client(functions.messages.GetDiscussionMessageRequest(
            peer=input_peer,
            msg_id=message.id
        ))
        discussion_chat = discussion.chats[0]
        # Find comments by this user on this discussion
        async for msg in self.client.iter_messages(discussion_chat, reply_to=discussion.messages[0].id, from_user='me'):
            await msg.delete()

# Actions

    async def undo_reaction(self, message_link:str=None):
        retries = config.get('delays', {}).get('action_retries', 3)
        delay = config.get('delays', {}).get('action_retry_delay', 3)
        attempt = 0
        while attempt < retries:
            try:
                chat_id, message_id, entity = await self.get_message_ids(message_link)
                # Use entity from get_message_ids if available, otherwise fetch it
                if entity is None:
                    # Extract username/identifier from link and use that to fetch entity
                    identifier = self._extract_identifier_from_link(message_link)
                    entity = await self.get_entity_cached(identifier) # Possible duplicate call
                
                # Get or fetch channel data (for consistency, though not strictly needed for undo)
                channel = await self._get_or_fetch_channel_data(chat_id, entity=entity)
                
                # Rate limit message fetching
                await rate_limiter.wait_if_needed('get_messages')
                message = await self.client.get_messages(entity, ids=message_id)
                await self._undo_reaction(message, entity)
                self.logger.info("Reaction removed successfully")
                return
            except Exception as e:
                attempt += 1
                self.logger.warning(f"Undo reaction failed (attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    await asyncio.sleep(delay)
                else:
                    raise

    async def undo_comment(self, message_link:str=None):
        retries = config.get('delays', {}).get('action_retries', 3)
        delay = config.get('delays', {}).get('action_retry_delay', 3)
        attempt = 0
        while attempt < retries:
            try:
                chat_id, message_id, entity = await self.get_message_ids(message_link)
                # Use entity from get_message_ids if available, otherwise fetch it
                if entity is None:
                    # Extract username/identifier from link and use that to fetch entity
                    identifier = self._extract_identifier_from_link(message_link)
                    entity = await self.get_entity_cached(identifier)
                
                # Get or fetch channel data (for consistency)
                channel = await self._get_or_fetch_channel_data(chat_id, entity=entity)
                
                # Rate limit message fetching
                await rate_limiter.wait_if_needed('get_messages')
                message = await self.client.get_messages(entity, ids=message_id)
                await self._undo_comment(message, entity)
                self.logger.info(f"Comment {message} deleted successfully!")
                return
            except Exception as e:
                attempt += 1
                self.logger.warning(f"undo_comment failed (attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    await asyncio.sleep(delay)
                else:
                    raise

    async def react(self, message_link:str):
        """
        React to a message by its ID in a specific chat.
        
        Args:
            message_link: Telegram message link
        """
        retries = config.get('delays', {}).get('action_retries', 1)
        delay = config.get('delays', {}).get('action_retry_delay', 3)
        attempt = 0
        while attempt < retries:
            try:
                chat_id, message_id, entity = await self.get_message_ids(message_link)
                # Use entity from get_message_ids if available, otherwise fetch it
                if entity is None:
                    # Extract username/identifier from link and use that to fetch entity
                    # (using raw chat_id doesn't work for channels without access_hash)
                    identifier = self._extract_identifier_from_link(message_link)
                    entity = await self.get_entity_cached(identifier)  # Possible duplicate call
                
                # Get or fetch channel data (minimizes API calls by reusing entity)
                channel = await self._get_or_fetch_channel_data(chat_id, entity=entity)
                
                # Rate limit message fetching
                await rate_limiter.wait_if_needed('get_messages')
                message = await self.client.get_messages(entity, ids=message_id)                
                await self._react(message, entity, channel=channel)                
                self.logger.info("Reaction added successfully")
                return
            except Exception as e:
                attempt += 1
                self.logger.warning(f"React failed (attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    await asyncio.sleep(delay)
                else:
                    raise
    
    async def comment(self, content, message_id:int=None, chat_id:str=None, message_link:str=None):
        """Comment on a message by its ID in a specific chat."""
        retries = config.get('delays', {}).get('action_retries', 3)
        delay = config.get('delays', {}).get('action_retry_delay', 3)
        attempt = 0
        while attempt < retries:
            try:
                chat_id, message_id, entity = await self.get_message_ids(message_link)
                # Use entity from get_message_ids if available, otherwise fetch it
                if entity is None:
                    # Extract username/identifier from link and use that to fetch entity
                    identifier = self._extract_identifier_from_link(message_link)
                    entity = await self.get_entity_cached(identifier)
                
                # Get or fetch channel data (minimizes API calls by reusing entity)
                channel = await self._get_or_fetch_channel_data(chat_id, entity=entity)
                
                # Rate limit message fetching
                await rate_limiter.wait_if_needed('get_messages')
                message = await self.client.get_messages(entity, ids=message_id)
                await self._comment(message=message, target_chat=entity, content=content, channel=channel)
                self.logger.info("Comment added successfully!")
                return
            except Exception as e:
                attempt += 1
                self.logger.warning(f"Comment failed (attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    await asyncio.sleep(delay)
                else:
                    raise

