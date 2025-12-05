"""
Test that cache properly resolves entities before fetching messages.

This test validates the fix for the bug where subsequent accounts failed
because get_message() was using bare chat_id instead of resolving entity first.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from auxilary_logic.telegram_cache import TelegramCache


class TestCacheEntityResolution:
    """Test entity resolution in cache operations."""
    
    @pytest.mark.asyncio
    async def test_get_message_resolves_entity_first(self):
        """Test that get_message resolves entity before fetching message."""
        cache = TelegramCache(task_id=1)
        
        # Mock client
        mock_client = MagicMock()
        mock_client.client = AsyncMock()
        
        # Mock entity and message
        mock_entity = MagicMock()
        mock_entity.id = 1197363285
        mock_message = MagicMock()
        mock_message.id = 181633
        mock_message.message = "Test message"
        
        # Setup mock responses
        mock_client.client.get_entity = AsyncMock(return_value=mock_entity)
        mock_client.client.get_messages = AsyncMock(return_value=mock_message)
        
        # Call get_message with chat_id
        chat_id = 1197363285
        message_id = 181633
        
        with patch('auxilary_logic.telegram_cache.rate_limiter') as mock_rate_limiter:
            mock_rate_limiter.wait_if_needed = AsyncMock()
            
            result = await cache.get_message(chat_id, message_id, mock_client)
        
        # Verify entity was resolved first
        mock_client.client.get_entity.assert_called_once_with(chat_id)
        
        # Verify get_messages was called with entity, not bare chat_id
        mock_client.client.get_messages.assert_called_once_with(mock_entity, ids=message_id)
        
        # Verify result
        assert result == mock_message
    
    @pytest.mark.asyncio
    async def test_get_message_uses_entity_cache(self):
        """Test that get_message reuses cached entities."""
        cache = TelegramCache(task_id=1)
        
        # Mock client
        mock_client = MagicMock()
        mock_client.client = AsyncMock()
        
        # Mock entity and message
        mock_entity = MagicMock()
        mock_entity.id = 1197363285
        mock_message1 = MagicMock()
        mock_message1.id = 181633
        mock_message2 = MagicMock()
        mock_message2.id = 181634
        
        # Setup mock responses
        mock_client.client.get_entity = AsyncMock(return_value=mock_entity)
        mock_client.client.get_messages = AsyncMock(side_effect=[mock_message1, mock_message2])
        
        chat_id = 1197363285
        
        with patch('auxilary_logic.telegram_cache.rate_limiter') as mock_rate_limiter:
            mock_rate_limiter.wait_if_needed = AsyncMock()
            
            # Fetch two different messages from same chat
            result1 = await cache.get_message(chat_id, 181633, mock_client)
            result2 = await cache.get_message(chat_id, 181634, mock_client)
        
        # Verify entity was only fetched once (second call uses cache)
        assert mock_client.client.get_entity.call_count == 1
        
        # Verify both messages were fetched with the entity
        assert mock_client.client.get_messages.call_count == 2
        
        # Verify cache stats show entity cache hit
        stats = cache.get_stats()
        assert stats['hits'] > 0  # Second entity fetch was a cache hit
    
    @pytest.mark.asyncio
    async def test_get_full_channel_resolves_entity_first(self):
        """Test that get_full_channel resolves entity before fetching full channel."""
        cache = TelegramCache(task_id=1)
        
        # Mock client
        mock_client = MagicMock()
        mock_client.client = AsyncMock()
        
        # Mock entity and full channel
        mock_entity = MagicMock()
        mock_entity.id = 1197363285
        mock_full_channel = MagicMock()
        
        # Setup mock responses
        mock_client.client.get_entity = AsyncMock(return_value=mock_entity)
        mock_client.client.return_value = mock_full_channel  # For GetFullChannelRequest
        
        channel_id = 1197363285
        
        with patch('auxilary_logic.telegram_cache.rate_limiter') as mock_rate_limiter:
            mock_rate_limiter.wait_if_needed = AsyncMock()
            
            result = await cache.get_full_channel(channel_id, mock_client)
        
        # Verify entity was resolved first
        mock_client.client.get_entity.assert_called_once_with(channel_id)
        
        # Verify GetFullChannelRequest was called with entity
        # (mock_client.client() is the call to the request)
        assert mock_client.client.call_count == 1
        
        # Verify result
        assert result == mock_full_channel


def run_tests():
    """Run all cache entity resolution tests."""
    print("Testing cache entity resolution...")
    
    # Test 1: get_message resolves entity first
    print("\n1. Testing get_message resolves entity first:")
    test = TestCacheEntityResolution()
    asyncio.run(test.test_get_message_resolves_entity_first())
    print("   ✓ get_message resolves entity before fetching")
    
    # Test 2: get_message uses entity cache
    print("\n2. Testing get_message uses entity cache:")
    asyncio.run(test.test_get_message_uses_entity_cache())
    print("   ✓ Entity cache reused for multiple messages")
    
    # Test 3: get_full_channel resolves entity first
    print("\n3. Testing get_full_channel resolves entity first:")
    asyncio.run(test.test_get_full_channel_resolves_entity_first())
    print("   ✓ get_full_channel resolves entity before fetching")
    
    print("\n✓ All cache entity resolution tests passed!")


if __name__ == "__main__":
    run_tests()
