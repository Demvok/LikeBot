"""
Account locking mixin for Telegram client.

Handles account lock acquisition and release for task coordination.
"""

from auxilary_logic.account_locking import get_account_lock_manager, AccountLockError


class LockingMixin:
    """Handles account locking for task coordination."""
    
    async def _acquire_lock(self, task_id: int) -> bool:
        """
        Attempt to acquire lock on this account for a task.
        
        Args:
            task_id: Task ID requesting the lock
        
        Returns:
            True if lock acquired, False if already locked by another task
        """
        if task_id is None:
            return True  # No locking needed
        
        self._task_id = task_id
        lock_manager = get_account_lock_manager()
        
        try:
            await lock_manager.acquire(self.phone_number, task_id)
            self._is_locked = True
            self.logger.debug(f"Acquired lock on account {self.phone_number} for task {task_id}")
            return True
        except AccountLockError as e:
            # Log warning but don't fail - allow task to proceed with caution
            self.logger.warning(
                f"⚠️ ACCOUNT LOCK CONFLICT: {self.phone_number} is already in use by task {e.locked_by_task_id}. "
                f"Proceeding anyway, but this may cause issues. Consider pausing the other task first."
            )
            self._is_locked = False
            return False
    
    async def _release_lock(self):
        """Release account lock if held."""
        if not self._is_locked:
            return
        
        lock_manager = get_account_lock_manager()
        released = await lock_manager.release(self.phone_number, self._task_id)
        if released:
            self.logger.debug(f"Released lock on account {self.phone_number} for task {self._task_id}")
        self._is_locked = False
