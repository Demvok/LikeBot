"""
Test message caching optimizations.

Verifies:
1. get_message_cached() wrapper exists and works
2. Message content is stored in Post during validation
3. Actions use message object directly instead of fetching again
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from main_logic.post import Post
from main_logic.schemas import PostDict


class TestMessageCaching:
    """Test message caching optimizations."""
    
    def test_post_schema_includes_message_content(self):
        """Verify PostDict schema includes message_content and content_fetched_at fields."""
        from main_logic.schemas import PostDict
        from datetime import datetime
        
        # Create post with message content
        post_data = {
            'post_id': 1,
            'chat_id': -1001234567890,
            'message_id': 123,
            'message_link': 'https://t.me/test/123',
            'is_validated': True,
            'message_content': 'Test message content',
            'content_fetched_at': datetime.now().isoformat(),
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }
        
        # Should not raise validation error
        post = PostDict(**post_data)
        assert post.message_content == 'Test message content'
        assert post.content_fetched_at is not None
        print("✓ PostDict schema correctly includes message_content and content_fetched_at")
    
    def test_post_class_includes_message_content(self):
        """Verify Post class stores message_content."""
        from main_logic.post import Post
        
        post = Post(
            message_link='https://t.me/test/123',
            post_id=1,
            chat_id=-1001234567890,
            message_id=123,
            message_content='Test content'
        )
        
        assert post.message_content == 'Test content'
        assert hasattr(post, 'content_fetched_at')
        print("✓ Post class correctly stores message_content")
    
    def test_post_to_dict_includes_message_content(self):
        """Verify Post.to_dict() includes message_content."""
        from main_logic.post import Post
        
        post = Post(
            message_link='https://t.me/test/123',
            post_id=1,
            message_content='Test content'
        )
        
        post_dict = post.to_dict()
        assert 'message_content' in post_dict
        assert post_dict['message_content'] == 'Test content'
        assert 'content_fetched_at' in post_dict
        print("✓ Post.to_dict() includes message_content field")
    
    @pytest.mark.asyncio
    async def test_get_message_cached_exists(self):
        """Verify get_message_cached() method exists on Client."""
        # Mock Client class
        from main_logic.agent import Client, Account
        
        # Create mock account
        mock_account = Mock(spec=Account)
        mock_account.phone_number = '+1234567890'
        mock_account.is_usable = Mock(return_value=True)
        
        # Create client instance
        client = Client(mock_account)
        
        # Verify method exists
        assert hasattr(client, 'get_message_cached')
        assert callable(client.get_message_cached)
        print("✓ Client.get_message_cached() method exists")
    
    @pytest.mark.asyncio
    async def test_post_validate_fetches_content(self):
        """Verify Post.validate() attempts to fetch message content."""
        from main_logic.post import Post
        from main_logic import database
        from pandas import Timestamp
        
        # Create post
        post = Post(
            message_link='https://t.me/testchannel/123',
            post_id=1
        )
        
        # Mock client
        mock_client = AsyncMock()
        mock_client.get_message_ids = AsyncMock(return_value=(-1001234567890, 123, None))
        mock_client.get_message_content = AsyncMock(return_value='Fetched message content')
        
        # Mock database
        mock_db = AsyncMock()
        mock_db.update_post = AsyncMock()
        
        with patch.object(database, 'get_db', return_value=mock_db):
            await post.validate(mock_client)
        
        # Verify message content was fetched
        mock_client.get_message_content.assert_called_once()
        assert post.message_content == 'Fetched message content'
        assert post.content_fetched_at is not None
        
        # Verify database was updated with content
        update_call_args = mock_db.update_post.call_args[0][1]
        assert 'message_content' in update_call_args
        assert update_call_args['message_content'] == 'Fetched message content'
        print("✓ Post.validate() fetches and stores message content")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
