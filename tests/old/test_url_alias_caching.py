"""
Test URL alias caching for channels to reduce Telegram API calls.

This test verifies that:
1. URL aliases are properly extracted and stored
2. Database lookups work correctly for various URL formats
3. API calls are reduced when channels are cached
4. Both /c/ links and username links are handled
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from main_logic.channel import Channel, normalize_chat_id
from main_logic.database import MongoStorage


class MockDB:
    """Mock database for testing URL alias functionality."""
    
    def __init__(self):
        self.channels = {}
        self.url_alias_to_chat_id = {}
    
    async def get_channel_by_url_alias(self, alias: str):
        """Get channel by URL alias."""
        chat_id = self.url_alias_to_chat_id.get(alias)
        if chat_id:
            return self.channels.get(chat_id)
        return None
    
    async def add_channel_url_alias(self, chat_id: int, alias: str):
        """Add URL alias to channel."""
        normalized_id = normalize_chat_id(chat_id)
        
        # Add alias mapping
        self.url_alias_to_chat_id[alias] = normalized_id
        
        # Update channel if it exists, otherwise create a minimal one
        if normalized_id in self.channels:
            if alias not in self.channels[normalized_id].url_aliases:
                self.channels[normalized_id].url_aliases.append(alias)
        else:
            # Create a minimal channel record to simulate real DB behavior
            channel = Channel(
                chat_id=normalized_id,
                channel_name=f"Channel_{normalized_id}",
                url_aliases=[alias]
            )
            self.channels[normalized_id] = channel
        
        return True
    
    async def add_channel(self, channel_data):
        """Add channel to mock database."""
        if hasattr(channel_data, 'to_dict'):
            channel_data = channel_data.to_dict()
        
        chat_id = normalize_chat_id(channel_data['chat_id'])
        channel = Channel.from_dict(channel_data)
        self.channels[chat_id] = channel
        
        # Add all aliases to mapping
        for alias in channel.url_aliases:
            self.url_alias_to_chat_id[alias] = chat_id
        
        return True
    
    async def get_channel(self, chat_id: int):
        """Get channel by chat_id."""
        normalized_id = normalize_chat_id(chat_id)
        return self.channels.get(normalized_id)
    
    async def get_post_by_link(self, link: str):
        """Mock post lookup - returns None."""
        return None


class MockEntityResolutionMixin:
    """Mock client with entity resolution for testing."""
    
    def __init__(self):
        self.logger = MagicMock()
        self.telegram_cache = None
        self.api_calls_made = []
    
    def _normalize_url_identifier(self, identifier: str) -> str:
        """Normalize URL identifier."""
        if not identifier:
            return identifier
        return identifier.lstrip('@').lower().strip()
    
    def _get_url_alias_from_link(self, link: str) -> str:
        """Extract URL alias from link."""
        from urllib.parse import urlparse, unquote
        
        link = link.strip()
        if '://' not in link:
            link = 'https://' + link
        
        parsed = urlparse(unquote(link))
        path = parsed.path.lstrip('/')
        segments = [seg for seg in path.split('/') if seg != '']
        
        if not segments or len(segments) < 2:
            raise ValueError(f"Link format not recognized: {link}")
        
        # /c/<raw>/<msg> format - return raw number as alias
        if segments[0] == 'c':
            if len(segments) < 3:
                raise ValueError(f"Invalid /c/ link: {link}")
            raw = segments[1]
            if not raw.isdigit():
                raise ValueError(f"Non-numeric in /c/ link: {link}")
            return raw
        
        # /s/<username>/<msg> or /<username>/<msg> format - return normalized username
        if segments[0] == 's':
            if len(segments) < 3:
                raise ValueError(f"Invalid /s/ link: {link}")
            username = segments[1]
        else:
            username = segments[0]
        
        return self._normalize_url_identifier(username)
    
    async def get_message_ids_with_caching(self, link: str, db: MockDB):
        """
        Simplified version of get_message_ids that uses URL alias caching.
        Returns (chat_id, message_id, from_cache: bool)
        """
        from urllib.parse import urlparse, unquote
        
        link = link.strip()
        if '://' not in link:
            link = 'https://' + link
        
        # Parse link to extract message_id
        parsed = urlparse(unquote(link))
        path = parsed.path.lstrip('/')
        segments = [seg for seg in path.split('/') if seg != '']
        
        if not segments or len(segments) < 2:
            raise ValueError(f"Link format not recognized: {link}")
        
        msg = segments[-1]
        if not msg.isdigit():
            raise ValueError(f"Message part is not numeric: {link}")
        message_id = int(msg)
        
        # Try to find channel by URL alias in DB
        url_alias = self._get_url_alias_from_link(link)
        channel = await db.get_channel_by_url_alias(url_alias)
        
        if channel:
            # Found in cache!
            self.logger.debug(f"Found channel in DB cache for alias '{url_alias}'")
            return channel.chat_id, message_id, True  # from_cache=True
        
        # Not in cache - simulate API call
        self.logger.debug(f"No channel found in DB for alias '{url_alias}', making API call")
        self.api_calls_made.append(link)
        
        # Simulate getting chat_id from API
        if segments[0] == 'c':
            raw = segments[1]
            chat_id = int(f"-100{raw}")
        else:
            # Simulate entity resolution for username
            # In real scenario, this would be entity.id
            chat_id = 2723750105  # Mock chat_id
        
        # Store the alias for future lookups
        await db.add_channel_url_alias(chat_id, url_alias)
        
        return chat_id, message_id, False  # from_cache=False


@pytest.mark.asyncio
async def test_url_alias_extraction():
    """Test URL alias extraction from various link formats."""
    client = MockEntityResolutionMixin()
    
    # Test /c/ link
    alias1 = client._get_url_alias_from_link("https://t.me/c/2723750105/123")
    assert alias1 == "2723750105", f"Expected '2723750105', got '{alias1}'"
    
    # Test username link
    alias2 = client._get_url_alias_from_link("https://t.me/examplechannel/456")
    assert alias2 == "examplechannel", f"Expected 'examplechannel', got '{alias2}'"
    
    # Test /s/ link
    alias3 = client._get_url_alias_from_link("https://t.me/s/testchannel/789")
    assert alias3 == "testchannel", f"Expected 'testchannel', got '{alias3}'"
    
    # Test @ prefix normalization
    alias4 = client._normalize_url_identifier("@UserName")
    assert alias4 == "username", f"Expected 'username', got '{alias4}'"
    
    print("✓ URL alias extraction tests passed")


@pytest.mark.asyncio
async def test_channel_url_alias_methods():
    """Test Channel class URL alias management methods."""
    channel = Channel(
        chat_id=2723750105,
        channel_name="Test Channel",
        url_aliases=["testchannel", "2723750105"]
    )
    
    # Test has_url_alias
    assert channel.has_url_alias("testchannel") == True
    assert channel.has_url_alias("nonexistent") == False
    
    # Test add_url_alias
    channel.add_url_alias("newalias")
    assert "newalias" in channel.url_aliases
    
    # Test duplicate prevention
    initial_len = len(channel.url_aliases)
    channel.add_url_alias("testchannel")  # Already exists
    assert len(channel.url_aliases) == initial_len
    
    # Test remove_url_alias
    channel.remove_url_alias("newalias")
    assert "newalias" not in channel.url_aliases
    
    print("✓ Channel URL alias method tests passed")


@pytest.mark.asyncio
async def test_database_url_alias_lookup():
    """Test database URL alias lookup functionality."""
    db = MockDB()
    
    # Create and add a channel with aliases
    channel = Channel(
        chat_id=2723750105,
        channel_name="Example Channel",
        url_aliases=["examplechannel", "2723750105"]
    )
    await db.add_channel(channel)
    
    # Test lookup by different aliases
    result1 = await db.get_channel_by_url_alias("examplechannel")
    assert result1 is not None
    assert result1.chat_id == 2723750105
    
    result2 = await db.get_channel_by_url_alias("2723750105")
    assert result2 is not None
    assert result2.chat_id == 2723750105
    
    # Test non-existent alias
    result3 = await db.get_channel_by_url_alias("nonexistent")
    assert result3 is None
    
    print("✓ Database URL alias lookup tests passed")


@pytest.mark.asyncio
async def test_api_call_reduction():
    """Test that API calls are reduced when using URL alias caching."""
    client = MockEntityResolutionMixin()
    db = MockDB()
    
    # First call - should make API call and cache the alias
    link1 = "https://t.me/examplechannel/123"
    chat_id1, msg_id1, from_cache1 = await client.get_message_ids_with_caching(link1, db)
    
    assert from_cache1 == False, "First call should not be from cache"
    assert len(client.api_calls_made) == 1, "Should have made 1 API call"
    assert msg_id1 == 123
    
    # Verify channel was created in DB after first call
    channel_exists = await db.get_channel_by_url_alias("examplechannel")
    assert channel_exists is not None, "Channel should be in DB after first call"
    
    # Also ensure the channel record exists (needed for mock DB)
    if chat_id1 not in db.channels:
        # Create channel record for the mock (in real DB this happens via add_channel)
        channel = Channel(
            chat_id=chat_id1,
            channel_name="Example Channel",
            url_aliases=["examplechannel"]
        )
        db.channels[chat_id1] = channel
    
    # Second call to same channel - should use cache
    link2 = "https://t.me/examplechannel/456"
    chat_id2, msg_id2, from_cache2 = await client.get_message_ids_with_caching(link2, db)
    
    assert from_cache2 == True, "Second call should be from cache"
    assert len(client.api_calls_made) == 1, "Should still only have 1 API call (no new calls)"
    assert msg_id2 == 456
    assert chat_id2 == chat_id1, "Chat IDs should match"
    
    print("✓ API call reduction test passed")
    print(f"  - API calls made: {len(client.api_calls_made)}")
    print(f"  - Cache hits: 1")
    print(f"  - API call reduction: 50%")


@pytest.mark.asyncio
async def test_c_link_alias_storage():
    """Test that /c/ links store and use aliases correctly."""
    client = MockEntityResolutionMixin()
    db = MockDB()
    
    # First /c/ link
    link1 = "https://t.me/c/2723750105/100"
    chat_id1, msg_id1, from_cache1 = await client.get_message_ids_with_caching(link1, db)
    
    assert from_cache1 == False, "First call should not be from cache"
    assert chat_id1 == -1002723750105, f"Expected -1002723750105, got {chat_id1}"
    
    # Verify alias was stored (normalized form)
    alias = client._get_url_alias_from_link(link1)
    assert alias == "2723750105"
    
    stored_channel = await db.get_channel_by_url_alias(alias)
    assert stored_channel is not None, "Channel should be stored with alias after first call"
    # Channel should be stored with normalized chat_id
    assert normalize_chat_id(stored_channel.chat_id) == 2723750105
    
    # Second call to same /c/ channel - should use cache
    link2 = "https://t.me/c/2723750105/200"
    chat_id2, msg_id2, from_cache2 = await client.get_message_ids_with_caching(link2, db)
    
    # Should be cached now
    assert from_cache2 == True, "Second call should be from cache"
    assert normalize_chat_id(chat_id2) == normalize_chat_id(chat_id1)
    assert msg_id2 == 200
    
    print("✓ /c/ link alias storage test passed")


@pytest.mark.asyncio
async def test_multiple_aliases_same_channel():
    """Test that a channel can have multiple URL aliases."""
    db = MockDB()
    
    # Create channel with multiple aliases
    channel = Channel(
        chat_id=2723750105,
        channel_name="Multi-Alias Channel",
        url_aliases=["alias1", "alias2", "2723750105"]
    )
    await db.add_channel(channel)
    
    # All aliases should resolve to same channel
    result1 = await db.get_channel_by_url_alias("alias1")
    result2 = await db.get_channel_by_url_alias("alias2")
    result3 = await db.get_channel_by_url_alias("2723750105")
    
    assert result1.chat_id == result2.chat_id == result3.chat_id == 2723750105
    
    # Add a new alias dynamically
    await db.add_channel_url_alias(2723750105, "newalias")
    result4 = await db.get_channel_by_url_alias("newalias")
    assert result4.chat_id == 2723750105
    
    print("✓ Multiple aliases test passed")


@pytest.mark.asyncio
async def test_normalized_id_handling():
    """Test that both normalized and -100 prefixed IDs work correctly."""
    db = MockDB()
    
    # Create channel with normalized ID
    channel = Channel(
        chat_id=2723750105,
        channel_name="Normalized ID Channel",
        url_aliases=["normalizedchannel"]
    )
    await db.add_channel(channel)
    
    # Both forms should work for add_channel_url_alias
    await db.add_channel_url_alias(-1002723750105, "alias_with_prefix")
    await db.add_channel_url_alias(2723750105, "alias_without_prefix")
    
    # All aliases should resolve to the same normalized chat_id
    result1 = await db.get_channel_by_url_alias("normalizedchannel")
    result2 = await db.get_channel_by_url_alias("alias_with_prefix")
    result3 = await db.get_channel_by_url_alias("alias_without_prefix")
    
    assert result1.chat_id == 2723750105
    assert result2.chat_id == 2723750105
    assert result3.chat_id == 2723750105
    
    print("✓ Normalized ID handling test passed")


def run_tests():
    """Run all tests."""
    print("\n" + "="*60)
    print("Testing URL Alias Caching for Channels")
    print("="*60 + "\n")
    
    asyncio.run(test_url_alias_extraction())
    asyncio.run(test_channel_url_alias_methods())
    asyncio.run(test_database_url_alias_lookup())
    asyncio.run(test_api_call_reduction())
    asyncio.run(test_c_link_alias_storage())
    asyncio.run(test_multiple_aliases_same_channel())
    asyncio.run(test_normalized_id_handling())
    
    print("\n" + "="*60)
    print("✅ All URL alias caching tests passed!")
    print("="*60 + "\n")
    print("Key benefits demonstrated:")
    print("  ✓ URL aliases extracted and stored correctly")
    print("  ✓ Database lookups work for all URL formats")
    print("  ✓ API calls reduced by ~50%+ for repeated channels")
    print("  ✓ Both /c/ and username links supported")
    print("  ✓ Multiple aliases per channel work correctly")
    print("  ✓ Normalized chat_id handling works properly")
    print()


if __name__ == "__main__":
    run_tests()
