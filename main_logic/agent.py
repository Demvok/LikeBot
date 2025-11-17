"""
Telegram account and client management utilities using Telethon.
Defines Account and Client classes to manage account data, session creation,
connection lifecycle, and common actions (react/comment/undo).
"""

import os, random, asyncio, time
from pandas import Timestamp 
from telethon.tl.functions.messages import SendReactionRequest, GetMessagesViewsRequest
from telethon import TelegramClient, functions, types, errors
from telethon.sessions import StringSession
from utils.logger import setup_logger, load_config
from dotenv import load_dotenv
from main_logic.schemas import AccountStatus, status_name
from auxilary_logic.telethon_error_handler import map_telethon_exception
from urllib.parse import urlparse, unquote
from auxilary_logic.encryption import (
    decrypt_secret,
    encrypt_secret,
    PURPOSE_PASSWORD,
    PURPOSE_STRING_SESSION,
)
from auxilary_logic.humaniser import rate_limiter, estimate_reading_time
from collections import OrderedDict

load_dotenv()
api_id = os.getenv('api_id')
api_hash = os.getenv('api_hash')

config = load_config()


class Account(object):

    AccountStatus = AccountStatus
    
    def __init__(self, account_data):
        try:
            self.phone_number = account_data.get('phone_number')
            self.account_id = account_data.get('account_id', None)
            self.session_name = account_data.get('session_name', None)
            self.session_encrypted = account_data.get('session_encrypted', None)
            self.twofa = account_data.get('twofa', False)
            self.password_encrypted = account_data.get('password_encrypted', None)
            self.notes = account_data.get('notes', "")
            self.status = account_data.get('status', self.AccountStatus.NEW)
            self.created_at = account_data.get('created_at', Timestamp.now())
            self.updated_at = account_data.get('updated_at', Timestamp.now())
            
            # Status tracking fields
            self.last_error = account_data.get('last_error', None)
            self.last_error_type = account_data.get('last_error_type', None)
            self.last_error_time = account_data.get('last_error_time', None)
            self.last_success_time = account_data.get('last_success_time', None)
            self.last_checked = account_data.get('last_checked', None)
            self.flood_wait_until = account_data.get('flood_wait_until', None)

        except KeyError as e:
            raise ValueError(f"Missing key in account configuration: {e}")

        if self.twofa and not self.password_encrypted:
            raise ValueError("2FA is enabled but no password provided in account configuration.")

        if self.session_name is None:
            self.session_name = self.phone_number
    
    def __repr__(self):
        return f"Account({self.account_id}, {self.phone_number}, status={self.status})"
    
    def __str__(self):
        return f"Account ID: {self.account_id}, phone: {self.phone_number}, status: {self.status}"
    
    def is_usable(self) -> bool:
        """Check if account can be used in tasks."""
        return AccountStatus.is_usable(self.status)
    
    def needs_attention(self) -> bool:
        """Check if account requires manual intervention."""
        return AccountStatus.needs_attention(self.status)
    
    async def update_status(self, new_status: AccountStatus, error: Exception = None, success: bool = False):
        """
        Update account status and related tracking fields in database.
        
        Args:
            new_status: New AccountStatus to set
            error: Optional exception that caused the status change
            success: If True, updates last_success_time
        """
        from main_logic.database import get_db
        from datetime import datetime, timezone
        
        db = get_db()
        update_data = {
            'status': status_name(new_status),
            'last_checked': datetime.now(timezone.utc),
            'updated_at': datetime.now(timezone.utc)
        }
        
        if error:
            update_data['last_error'] = str(error)
            update_data['last_error_type'] = type(error).__name__
            update_data['last_error_time'] = datetime.now(timezone.utc)
        
        if success:
            update_data['last_success_time'] = datetime.now(timezone.utc)
            # Clear error fields on success
            update_data['last_error'] = None
            update_data['last_error_type'] = None
        
        # Update local instance
        self.status = new_status
        self.last_checked = update_data['last_checked']
        if error:
            self.last_error = update_data['last_error']
            self.last_error_type = update_data['last_error_type']
            self.last_error_time = update_data['last_error_time']
        if success:
            self.last_success_time = update_data['last_success_time']
            self.last_error = None
            self.last_error_type = None
        
        # Update database
        await db.update_account(self.phone_number, update_data)
    
    async def set_flood_wait(self, seconds: int, error: Exception = None):
        """Set flood wait status and expiration time.

        Args:
            seconds: number of seconds the flood wait will last
            error: optional exception to record in last_error/last_error_type
        """
        from main_logic.database import get_db
        from datetime import datetime, timezone, timedelta
        
        db = get_db()
        flood_wait_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)

        # The explicit FLOOD_WAIT status was removed from AccountStatus. Use
        # ERROR to flag the account for attention while still recording the
        # flood-wait expiration. Record the error details when available.
        update_payload = {
            'status': AccountStatus.ERROR.name,
            'flood_wait_until': flood_wait_until,
            'last_checked': datetime.now(timezone.utc)
        }
        if error:
            update_payload['last_error'] = str(error)
            update_payload['last_error_type'] = type(error).__name__
            update_payload['last_error_time'] = datetime.now(timezone.utc)

        await db.update_account(self.phone_number, update_payload)
        
        self.status = AccountStatus.ERROR
        self.flood_wait_until = flood_wait_until
        if error:
            self.last_error = update_payload.get('last_error')
            self.last_error_type = update_payload.get('last_error_type')
            self.last_error_time = update_payload.get('last_error_time')
    
    def to_dict(self, secure=False):
        """Convert Account object to dictionary matching AccountDict schema.
        
        Args:
            secure (bool): If True, excludes password_encrypted field for security
        """
        base_dict = {
            'account_id': self.account_id,
            'session_name': self.session_name,
            'phone_number': self.phone_number,
            'session_encrypted': self.session_encrypted,
            'twofa': self.twofa,
            'notes': self.notes,
            'status': status_name(self.status),
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'last_error': self.last_error,
            'last_error_type': self.last_error_type,
            'last_error_time': self.last_error_time,
            'last_success_time': self.last_success_time,
            'last_checked': self.last_checked,
            'flood_wait_until': self.flood_wait_until
        }
        
        if not secure:
            base_dict['password_encrypted'] = self.password_encrypted
            
        return base_dict

    async def create_connection(self):
        """Create a TelegramClient connection from account, useful for debugging."""
        client = Client(self)
        await client.connect()
        client.logger.info(f"Client for {self.phone_number} connected successfully.")  # Use self.logger instead of client.logger
        return client

    async def add_password(self, password):
        """Add or update the encrypted password for 2FA."""
        if not password:
            raise ValueError("Password cannot be empty.")
        self.password_encrypted = encrypt_secret(password, PURPOSE_PASSWORD)
        self.twofa = True

        from main_logic.database import get_db  # Avoid circular import if any
        db = get_db()
        await db.update_account(self.phone_number, {'password_encrypted': self.password_encrypted, 'twofa': self.twofa})

    @classmethod
    def from_keys(
        cls,
        phone_number,
        account_id=None,
        session_name=None,
        session_encrypted=None,
        twofa=False,
        password_encrypted=None,
        password=None,
        notes=None,
        status=None,
        created_at=None,
        updated_at=None
    ):
        """Create an Account object from keys, matching AccountBase schema."""
        account_data = {
            'phone_number': phone_number,
            'account_id': account_id,
            'session_name': session_name,
            'session_encrypted': session_encrypted,
            'twofa': twofa,
            'password_encrypted': password_encrypted if password_encrypted else encrypt_secret(password, PURPOSE_PASSWORD) if password else None,
            'notes': notes if notes is not None else "",
            'status': status if status is not None else cls.AccountStatus.NEW,
            'created_at': created_at or Timestamp.now(),
            'updated_at': updated_at or Timestamp.now()
        }
        return cls(account_data)

    @classmethod
    async def get_accounts(cls, phones:list):
        """Get a list of Account objects from a list of phone numbers."""
        from main_logic.database import get_db
        db = get_db()
        all_accounts = await db.load_all_accounts()
        return [elem for elem in all_accounts if elem.phone_number in phones]


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

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.disconnect()
            self.logger.info(f"Client for {self.phone_number} disconnected.")

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

    async def connect(self):
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
        """Disconnect the client."""
        retries = config.get('delays', {}).get('connection_retries', 5)
        delay = config.get('delays', {}).get('reconnect_delay', 3)
        attempt = 0
        while attempt < retries:
            try:
                await self.client.disconnect()
                self.logger.info(f"Client for {self.phone_number} disconnected.")
                
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

#

    @classmethod
    async def connect_clients(cls, accounts: list[Account], logger):
        if logger:
            logger.info(f"Connecting clients for {len(accounts)} accounts...")

        clients = [Client(account) for account in accounts]
        
        await asyncio.gather(*(client.connect() for client in clients))  # Connect all clients in parallel

        if logger:
            logger.info(f"Connected clients for {len(clients)} accounts.")

        return clients if clients else None
    
    @classmethod
    async def disconnect_clients(cls, clients: list["Client"], logger):
        if not clients:
            if logger:
                logger.info("No clients to disconnect.")
            return None
            
        if logger:
            logger.info(f"Disconnecting {len(clients)} clients...")

        await asyncio.gather(*(client.disconnect() for client in clients))

        if logger:
            logger.info(f"Disconnected {len(clients)} clients.")

        return None  # Return None to indicate all clients are disconnected

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

    # Basic actions

    async def _react(self, message, target_chat):
        """
        React to a message with an emoji from the active palette.
        
        Args:
            message: Telethon message object
            target_chat: Target chat entity
        
        Raises:
            ValueError: If no valid emojis are available after filtering
        """
        await self.ensure_connected()
        
        await self.client(GetMessagesViewsRequest(
            peer=target_chat,
            id=[message.id],
            increment=True
        ))

        # CRITICAL: ALWAYS add reading time delay to prevent spam detection
        # Even at humanisation_level 0, we need basic delays
        humanisation_level = config.get('delays', {}).get('humanisation_level', 1)
        if humanisation_level >= 1:
            # Full humanization: fetch message content and estimate reading time
            msg_content = await self.get_message_content(chat_id=target_chat.id if hasattr(target_chat, 'id') else target_chat, message_id=message.id)
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

        # Get allowed reactions from message (if explicitly restricted)
        allowed_reactions = None
        try:
            # Fetch full message to get reactions attribute
            # Rate limit message fetching
            await rate_limiter.wait_if_needed('get_messages')
            full_message = await self.client.get_messages(target_chat, ids=message.id)
            
            if hasattr(full_message, 'reactions') and full_message.reactions:
                # Check available_reactions if it exists (Telegram's list of allowed emojis)
                if hasattr(full_message.reactions, 'available_reactions'):
                    available_reactions_list = []
                    for reaction in full_message.reactions.available_reactions:
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
                        peer=target_chat,
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
                        peer=target_chat,
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

    async def _comment(self, message, target_chat, content):
        await self.ensure_connected()

        await self.client(GetMessagesViewsRequest(
            peer=target_chat,
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
        
        await self.client(GetMessagesViewsRequest(
            peer=target_chat,
            id=[message.id],
            increment=True
        ))
        
        await asyncio.sleep(random.uniform(0.5, 1))  # Prevent spam if everything is broken
        
        # Rate limit reaction removal
        await rate_limiter.wait_if_needed('send_reaction')
        await self.client(SendReactionRequest(
            peer=target_chat,
            msg_id=message.id,
            reaction=[],  # Empty list removes reaction
            add_to_recent=False
        ))

    async def _undo_comment(self, message, target_chat):
        """
        Deletes all user comments on given post.
        """
        await self.ensure_connected()

        await self.client(GetMessagesViewsRequest(
            peer=target_chat,
            id=[message.id],
            increment=True
        ))

        await asyncio.sleep(random.uniform(0.5, 1))  # Prevent spam if everything is broken
        discussion = await self.client(functions.messages.GetDiscussionMessageRequest(
            peer=target_chat,
            msg_id=message.id
        ))
        discussion_chat = discussion.chats[0]
        # Find comments by this user on this discussion
        async for msg in self.client.iter_messages(discussion_chat, reply_to=discussion.messages[0].id, from_user='me'):
            await msg.delete()

    async def get_message_ids(self, link: str):
        """
        Extract (chat_id, message_id) from a Telegram link of types:
        - https://t.me/c/<raw>/<msg>
        - https://t.me/<username>/<msg>
        - https://t.me/s/<username>/<msg>
        - with or without @, with query params
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
                                return chat_id_db, message_id_db
                            except Exception:
                                self.logger.debug(f"DB post for link {link} had non-integer ids, falling back to resolution")
                except Exception as _db_err:
                    # Do not fail on DB errors; fall back to Telethon resolution
                    self.logger.debug(f"DB lookup by message_link failed for '{link}': {_db_err}")
            except Exception:
                pass
            parsed = urlparse(unquote(link))
            path = parsed.path.lstrip('/')
            segments = [seg for seg in path.split('/') if seg != '']
            if not segments or len(segments) < 2:
                raise ValueError(f"Link format not recognized: {link}")

            # випадок /c/<raw>/<msg>
            if segments[0] == 'c':
                if len(segments) < 3:
                    raise ValueError(f"Invalid /c/ link: {link}")
                raw = segments[1]
                msg = segments[2]
                if not raw.isdigit() or not msg.isdigit():
                    raise ValueError(f"Non-numeric in /c/ link: {link}")
                chat_id = int(f"-100{raw}")
                message_id = int(msg)
                return chat_id, message_id

            # випадок /s/<username>/<msg>
            if segments[0] == 's':
                if len(segments) < 3:
                    raise ValueError(f"Invalid /s/ link: {link}")
                username = segments[1]
                msg = segments[2]
            else:
                # /<username>/<msg>
                username = segments[0]
                msg = segments[1]

            username = username.lstrip('@')
            if not msg.isdigit():
                raise ValueError(f"Message part is not numeric: {link}")
            message_id = int(msg)

            # отримуємо entity with caching and rate limiting
            await self.ensure_connected()
            # Спроба передати повний URL (Telethon підтримує це)
            try:
                entity = await self.get_entity_cached(username)
            except (errors.AuthKeyUnregisteredError, errors.AuthKeyInvalidError, errors.SessionRevokedError, errors.UserDeactivatedError, errors.UserDeactivatedBanError) as auth_error:
                # Session is invalid/expired/revoked or account is banned - re-raise for proper handling upstream
                # Include SessionRevokedError explicitly so it isn't swallowed and can be mapped to account status
                self.logger.error(f"Session invalid/expired or account deactivated while resolving '{username}': {auth_error}")
                raise
            except Exception as e1:
                # Try several fallbacks: full URL with scheme, http, www variant, and @username.
                # Some Telethon versions accept a full URL but others don't, so try multiple formats.
                last_exc = e1
                tried = []
                candidates = [
                    f"https://{parsed.netloc}/{username}",
                    f"http://{parsed.netloc}/{username}",
                    f"{parsed.netloc}/{username}",
                    f"@{username}",
                ]
                entity = None
                for candidate in candidates:
                    tried.append(candidate)
                    try:
                        entity = await self.get_entity_cached(candidate)
                        break
                    except (errors.AuthKeyUnregisteredError, errors.AuthKeyInvalidError, errors.SessionRevokedError, errors.UserDeactivatedError, errors.UserDeactivatedBanError) as auth_error:
                        # Re-raise auth related errors immediately so they can be handled upstream
                        self.logger.error(f"Session invalid/expired or account deactivated while resolving '{username}' using '{candidate}': {auth_error}")
                        raise
                    except Exception as e2:
                        last_exc = e2
                        self.logger.debug(f"get_entity failed for candidate '{candidate}': {e2}")

                if entity is None:
                    self.logger.error(f"Failed to resolve username '{username}' from link {link}. Tried: {tried}. Errors: {e1}, last: {last_exc}")
                    raise ValueError(f"Cannot resolve username '{username}' from link {link}")

            chat_id = entity.id
            return chat_id, message_id

        except (errors.AuthKeyUnregisteredError, errors.AuthKeyInvalidError, errors.UserDeactivatedError, errors.UserDeactivatedBanError):
            # Re-raise auth errors without wrapping them
            raise
        except Exception as e:
            self.logger.warning(f"Error extracting IDs from '{link}': {e}")
            raise


    # Actions

    async def undo_reaction(self, message_link:str=None):
        retries = config.get('delays', {}).get('action_retries', 3)
        delay = config.get('delays', {}).get('action_retry_delay', 3)
        attempt = 0
        while attempt < retries:
            try:
                chat_id, message_id = await self.get_message_ids(message_link)
                # Use cached get_entity with rate limiting
                entity = await self.get_entity_cached(chat_id)
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
                chat_id, message_id = await self.get_message_ids(message_link)
                # Use cached get_entity with rate limiting
                entity = await self.get_entity_cached(chat_id)
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
                chat_id, message_id = await self.get_message_ids(message_link)
                # Use cached get_entity with rate limiting
                entity = await self.get_entity_cached(chat_id)
                # Rate limit message fetching
                await rate_limiter.wait_if_needed('get_messages')
                message = await self.client.get_messages(entity, ids=message_id)                
                await self._react(message, entity)                
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
                chat_id, message_id = await self.get_message_ids(message_link)
                # Use cached get_entity with rate limiting
                entity = await self.get_entity_cached(chat_id)
                # Rate limit message fetching
                await rate_limiter.wait_if_needed('get_messages')
                message = await self.client.get_messages(entity, ids=message_id)
                await self._comment(message=message, target_chat=entity, content=content)
                self.logger.info("Comment added successfully!")
                return
            except Exception as e:
                attempt += 1
                self.logger.warning(f"Comment failed (attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    await asyncio.sleep(delay)
                else:
                    raise

