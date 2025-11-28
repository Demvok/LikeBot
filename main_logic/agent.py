"""
Telegram client management utilities using Telethon.

Refactored with mixin-based architecture for better maintainability and testability.
Client class now composes functionality from focused mixins in client_mixins/.

The Account class has been moved to main_logic/account.py but is re-exported
here for backwards compatibility.
"""

import asyncio
from utils.logger import setup_logger
from main_logic.account import Account
from auxilary_logic.humaniser import TelegramAPIRateLimiter
from auxilary_logic.account_locking import AccountLockManager, AccountLockError, get_account_lock_manager

# Import all mixins
from main_logic.client_mixins import (
    ConnectionMixin,
    EntityResolutionMixin,
    ChannelDataMixin,
    ActionsMixin,
    CacheIntegrationMixin,
)

# Explicit exports for `from main_logic.agent import *`
__all__ = ['Account', 'Client', 'TelegramAPIRateLimiter', 'AccountLockManager', 'AccountLockError', 'get_account_lock_manager']


class Client(
    ConnectionMixin,
    EntityResolutionMixin,
    ChannelDataMixin,
    ActionsMixin,
    CacheIntegrationMixin
):
    """
    Telegram client with full functionality composed from mixins.
    
    Mixins (in MRO order):
    1. ConnectionMixin: Connection lifecycle (composes SessionMixin, ProxyMixin, LockingMixin)
    2. EntityResolutionMixin: Username/link/ID resolution
    3. ChannelDataMixin: Channel metadata + subscription checking
    4. ActionsMixin: Reactions, comments, undo operations
    5. CacheIntegrationMixin: Standalone cache for debugging
    
    The ClientBase logic (initialization, properties) remains here for simplicity.
    """

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
        
        # Task-scoped cache (injected by Task)
        self.telegram_cache = None  # Will be set by Task._run()
        
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
        return self.client and self.client.is_connected()

    # Class methods for mass operations
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

        clients = [cls(account) for account in accounts]
        
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
