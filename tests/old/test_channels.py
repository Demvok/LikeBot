"""
Test suite for Channel domain class and database operations.
"""

import pytest
from datetime import datetime
from main_logic.channel import Channel
from main_logic.schemas import (
    ChannelBase, ChannelCreate, ChannelUpdate, 
    ChannelResponse, ChannelDict
)


class TestChannelClass:
    """Test Channel domain class."""
    
    def test_channel_creation(self):
        """Test creating a Channel object."""
        channel = Channel(
            chat_id=-1001234567890,
            channel_name="Test Channel",
            is_private=False
        )
        
        # chat_id should be normalized (without -100 prefix)
        assert channel.chat_id == 1234567890
        assert channel.channel_name == "Test Channel"
        assert channel.is_private == False
        assert channel.has_enabled_reactions == True
        assert channel.reactions_only_for_subscribers == False
        assert channel.tags == []
        assert channel.channel_hash == ""
    
    def test_channel_with_all_fields(self):
        """Test creating a Channel with all fields."""
        channel = Channel(
            chat_id=-1001234567890,
            is_private=True,
            channel_hash="abc123",
            has_enabled_reactions=True,
            reactions_only_for_subscribers=True,
            discussion_chat_id=-1009876543210,
            channel_name="Private Channel",
            tags=["premium", "news"]
        )
        
        assert channel.is_private == True
        assert channel.channel_hash == "abc123"
        assert channel.reactions_only_for_subscribers == True
        # discussion_chat_id should be normalized (without -100 prefix)
        assert channel.discussion_chat_id == 9876543210
        assert "premium" in channel.tags
        assert "news" in channel.tags
    
    def test_channel_to_dict(self):
        """Test converting Channel to dictionary."""
        channel = Channel(
            chat_id=-1001234567890,
            channel_name="Test Channel",
            tags=["test", "example"]
        )
        
        data = channel.to_dict()
        
        # chat_id should be normalized in output
        assert data['chat_id'] == 1234567890
        assert data['channel_name'] == "Test Channel"
        assert data['tags'] == ["test", "example"]
        assert 'created_at' in data
        assert 'updated_at' in data
    
    def test_channel_from_dict(self):
        """Test creating Channel from dictionary."""
        data = {
            'chat_id': -1001234567890,
            'channel_name': "Test Channel",
            'is_private': True,
            'tags': ["test"]
        }
        
        channel = Channel.from_dict(data)
        
        # chat_id should be normalized
        assert channel.chat_id == 1234567890
        assert channel.channel_name == "Test Channel"
        assert channel.is_private == True
        assert channel.tags == ["test"]
    
    def test_channel_from_keys(self):
        """Test creating Channel from keys."""
        channel = Channel.from_keys(
            chat_id=-1001234567890,
            channel_name="Test Channel",
            tags=["test"]
        )
        
        # chat_id should be normalized
        assert channel.chat_id == 1234567890
        assert channel.channel_name == "Test Channel"
        assert channel.tags == ["test"]
    
    def test_channel_tag_operations(self):
        """Test tag add/remove/has operations."""
        channel = Channel(chat_id=-1001234567890)
        
        # Add tags
        channel.add_tag("news")
        assert channel.has_tag("news")
        assert len(channel.tags) == 1
        
        channel.add_tag("tech")
        assert len(channel.tags) == 2
        
        # Duplicate tag should not be added
        channel.add_tag("news")
        assert len(channel.tags) == 2
        
        # Remove tag
        channel.remove_tag("news")
        assert not channel.has_tag("news")
        assert len(channel.tags) == 1
    
    def test_channel_update(self):
        """Test updating channel attributes."""
        channel = Channel(
            chat_id=-1001234567890,
            channel_name="Old Name"
        )
        
        channel.update(
            channel_name="New Name",
            tags=["updated"]
        )
        
        assert channel.channel_name == "New Name"
        assert channel.tags == ["updated"]
    
    def test_channel_properties(self):
        """Test channel property methods."""
        # Test can_react
        channel = Channel(chat_id=-1001234567890, has_enabled_reactions=True)
        assert channel.can_react == True
        
        channel.has_enabled_reactions = False
        assert channel.can_react == False
        
        # Test requires_subscription_for_reactions
        channel.reactions_only_for_subscribers = True
        assert channel.requires_subscription_for_reactions == True
        
        # Test has_discussion_group
        channel.discussion_chat_id = -1009876543210
        assert channel.has_discussion_group == True
        # discussion_chat_id should be normalized when set directly
        # (though normally it should be set via __init__ or update)
        
        channel.discussion_chat_id = None
        assert channel.has_discussion_group == False
    
    def test_channel_repr(self):
        """Test string representation."""
        channel = Channel(
            chat_id=-1001234567890,
            channel_name="Test Channel",
            is_private=True
        )
        
        repr_str = repr(channel)
        # chat_id should be normalized in repr
        assert "1234567890" in repr_str
        assert "Test Channel" in repr_str
        assert "private" in repr_str


class TestChannelSchemas:
    """Test Channel Pydantic schemas."""
    
    def test_channel_base_schema(self):
        """Test ChannelBase schema."""
        data = ChannelBase(
            chat_id=-1001234567890,
            channel_name="Test Channel"
        )
        
        assert data.chat_id == -1001234567890
        assert data.channel_name == "Test Channel"
        assert data.is_private == False
        assert data.has_enabled_reactions == True
    
    def test_channel_create_schema(self):
        """Test ChannelCreate schema."""
        data = ChannelCreate(
            chat_id=-1001234567890,
            channel_name="Test Channel",
            tags=["test", "example"]
        )
        
        assert data.chat_id == -1001234567890
        assert data.tags == ["test", "example"]
    
    def test_channel_update_schema(self):
        """Test ChannelUpdate schema."""
        data = ChannelUpdate(
            channel_name="Updated Name",
            tags=["updated"]
        )
        
        assert data.channel_name == "Updated Name"
        assert data.tags == ["updated"]
    
    def test_channel_response_schema(self):
        """Test ChannelResponse schema."""
        data = ChannelResponse(
            chat_id=-1001234567890,
            channel_name="Test Channel"
        )
        
        assert data.chat_id == -1001234567890
        assert hasattr(data, 'created_at')
        assert hasattr(data, 'updated_at')
    
    def test_channel_dict_schema(self):
        """Test ChannelDict schema."""
        data = ChannelDict(
            chat_id=-1001234567890,
            is_private=False,
            channel_hash="",
            has_enabled_reactions=True,
            reactions_only_for_subscribers=False,
            discussion_chat_id=None,
            channel_name="Test Channel",
            tags=["test"],
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        )
        
        assert data.chat_id == -1001234567890
        assert data.tags == ["test"]
    
    def test_tags_validation(self):
        """Test tags validation removes empty strings."""
        # ChannelBase validation
        data = ChannelBase(
            chat_id=-1001234567890,
            tags=["valid", "", "  ", "another"]
        )
        
        # Should filter out empty and whitespace-only tags
        assert "" not in data.tags
        assert "  " not in data.tags
        assert "valid" in data.tags
        assert "another" in data.tags


# Database CRUD tests would require async setup
class TestChannelDatabase:
    """Test Channel database operations (requires async setup)."""
    
    # These tests would need async test framework setup
    # and MongoDB connection, which are typically run separately
    
    def test_placeholder(self):
        """Placeholder for async database tests."""
        # Actual database tests would be in a separate async test suite
        # or integration tests that set up the database connection
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
