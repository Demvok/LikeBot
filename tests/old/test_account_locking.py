"""
Tests for the AccountLockManager account locking mechanism.

This module tests:
- Account lock acquisition and release
- Lock conflict detection
- Task-based lock cleanup
- Singleton behavior
"""
import asyncio
import pytest
from main_logic.agent import (
    AccountLockManager, 
    AccountLockError, 
    get_account_lock_manager
)


@pytest.fixture
def lock_manager():
    """Get a fresh lock manager for testing (clears all locks before each test)."""
    lm = get_account_lock_manager()
    # Clear any existing locks synchronously by resetting internal state
    lm._locks.clear()
    return lm


class TestAccountLockManager:
    """Tests for AccountLockManager functionality."""
    
    @pytest.mark.asyncio
    async def test_acquire_and_release(self, lock_manager):
        """Test basic lock acquire and release."""
        phone = "+1234567890"
        task_id = 1
        
        # Acquire lock
        result = await lock_manager.acquire(phone, task_id)
        assert result is True
        assert lock_manager.is_locked(phone) is True
        
        # Get lock info
        info = lock_manager.get_lock_info(phone)
        assert info is not None
        assert info['task_id'] == task_id
        
        # Release lock
        released = await lock_manager.release(phone, task_id)
        assert released is True
        assert lock_manager.is_locked(phone) is False
    
    @pytest.mark.asyncio
    async def test_conflict_detection(self, lock_manager):
        """Test that acquiring a lock held by another task raises AccountLockError."""
        phone = "+1234567890"
        task1 = 1
        task2 = 2
        
        # Task 1 acquires lock
        await lock_manager.acquire(phone, task1)
        
        # Task 2 tries to acquire - should raise
        with pytest.raises(AccountLockError) as exc_info:
            await lock_manager.acquire(phone, task2)
        
        assert exc_info.value.phone_number == phone
        assert exc_info.value.locked_by_task_id == task1
        
        # Cleanup
        await lock_manager.release(phone, task1)
    
    @pytest.mark.asyncio
    async def test_same_task_reacquire(self, lock_manager):
        """Test that the same task can re-acquire its own lock."""
        phone = "+1234567890"
        task_id = 1
        
        # Acquire twice with same task_id
        await lock_manager.acquire(phone, task_id)
        result = await lock_manager.acquire(phone, task_id)  # Should not raise
        
        assert result is True
        assert lock_manager.is_locked(phone) is True
        
        # Cleanup
        await lock_manager.release(phone, task_id)
    
    @pytest.mark.asyncio
    async def test_force_acquire(self, lock_manager):
        """Test force acquire overrides existing lock."""
        phone = "+1234567890"
        task1 = 1
        task2 = 2
        
        # Task 1 acquires lock
        await lock_manager.acquire(phone, task1)
        
        # Task 2 force acquires
        result = await lock_manager.acquire(phone, task2, force=True)
        assert result is True
        
        # Lock should now be held by task 2
        info = lock_manager.get_lock_info(phone)
        assert info['task_id'] == task2
        
        # Cleanup
        await lock_manager.release(phone, task2)
    
    @pytest.mark.asyncio
    async def test_release_wrong_task(self, lock_manager):
        """Test that releasing with wrong task_id doesn't release the lock."""
        phone = "+1234567890"
        task1 = 1
        task2 = 2
        
        # Task 1 acquires lock
        await lock_manager.acquire(phone, task1)
        
        # Task 2 tries to release - should fail
        released = await lock_manager.release(phone, task2)
        assert released is False
        
        # Lock should still be held
        assert lock_manager.is_locked(phone) is True
        
        # Cleanup with correct task_id
        await lock_manager.release(phone, task1)
    
    @pytest.mark.asyncio
    async def test_release_all_for_task(self, lock_manager):
        """Test releasing all locks for a specific task."""
        phones = ["+1234567890", "+0987654321", "+1111111111"]
        task1 = 1
        task2 = 2
        
        # Task 1 acquires 2 locks
        await lock_manager.acquire(phones[0], task1)
        await lock_manager.acquire(phones[1], task1)
        
        # Task 2 acquires 1 lock
        await lock_manager.acquire(phones[2], task2)
        
        # Release all for task 1
        released_count = await lock_manager.release_all_for_task(task1)
        assert released_count == 2
        
        # Task 1's locks should be released
        assert lock_manager.is_locked(phones[0]) is False
        assert lock_manager.is_locked(phones[1]) is False
        
        # Task 2's lock should still exist
        assert lock_manager.is_locked(phones[2]) is True
        
        # Cleanup
        await lock_manager.release(phones[2], task2)
    
    @pytest.mark.asyncio
    async def test_get_all_locks(self, lock_manager):
        """Test getting all current locks."""
        phones = ["+1234567890", "+0987654321"]
        task_id = 1
        
        # Acquire multiple locks
        await lock_manager.acquire(phones[0], task_id)
        await lock_manager.acquire(phones[1], task_id)
        
        all_locks = lock_manager.get_all_locks()
        
        assert len(all_locks) == 2
        assert phones[0] in all_locks
        assert phones[1] in all_locks
        
        # Cleanup
        await lock_manager.release_all_for_task(task_id)
    
    @pytest.mark.asyncio
    async def test_clear_all(self, lock_manager):
        """Test clearing all locks."""
        phones = ["+1234567890", "+0987654321"]
        
        await lock_manager.acquire(phones[0], 1)
        await lock_manager.acquire(phones[1], 2)
        
        cleared = await lock_manager.clear_all()
        assert cleared == 2
        
        assert lock_manager.is_locked(phones[0]) is False
        assert lock_manager.is_locked(phones[1]) is False
    
    def test_singleton(self):
        """Test that get_account_lock_manager returns singleton."""
        lm1 = get_account_lock_manager()
        lm2 = get_account_lock_manager()
        
        assert lm1 is lm2


class TestAccountLockError:
    """Tests for AccountLockError exception."""
    
    def test_error_attributes(self):
        """Test AccountLockError has correct attributes."""
        error = AccountLockError("+1234567890", 42, "Custom message")
        
        assert error.phone_number == "+1234567890"
        assert error.locked_by_task_id == 42
        assert str(error) == "Custom message"
    
    def test_default_message(self):
        """Test AccountLockError generates default message."""
        error = AccountLockError("+1234567890", 42)
        
        assert "+1234567890" in str(error)
        assert "42" in str(error)
