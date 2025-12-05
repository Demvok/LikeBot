"""
Account locking manager for task coordination.

Provides thread-safe account locking to prevent concurrent access
from multiple tasks. Ensures account integrity during task execution.
"""

import asyncio
from datetime import datetime, timezone
from utils.logger import setup_logger


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
        await lock_manager.acquire(phone_number, task_id)
        
        # Release lock
        await lock_manager.release(phone_number, task_id)
        
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
