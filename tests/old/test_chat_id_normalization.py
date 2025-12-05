"""
Test suite for chat ID normalization functionality.

Tests that chat IDs with -100 prefix are correctly normalized to their base form,
ensuring that -1002723750105 and 2723750105 are treated as identical.
"""

import pytest
from main_logic.channel import normalize_chat_id
from main_logic.channel import Channel
from main_logic.post import Post


class TestChatIdNormalization:
    """Test the normalize_chat_id utility function."""
    
    def test_normalize_with_negative_100_prefix(self):
        """Test normalization of chat_id with -100 prefix."""
        assert normalize_chat_id(-1002723750105) == 2723750105
        assert normalize_chat_id(-100123456) == 123456
        assert normalize_chat_id(-1001) == 1
    
    def test_normalize_without_prefix(self):
        """Test that chat_id without -100 prefix remains unchanged."""
        assert normalize_chat_id(2723750105) == 2723750105
        assert normalize_chat_id(123456) == 123456
        assert normalize_chat_id(1) == 1
    
    def test_normalize_other_negative_numbers(self):
        """Test normalization of negative numbers without -100 prefix."""
        # Should return absolute value for other negative numbers
        assert normalize_chat_id(-123) == 123
        assert normalize_chat_id(-999) == 999
    
    def test_normalize_none(self):
        """Test that None is handled correctly."""
        assert normalize_chat_id(None) is None
    
    def test_normalize_zero(self):
        """Test edge case of zero."""
        assert normalize_chat_id(0) == 0


class TestChannelNormalization:
    """Test that Channel class normalizes chat_id correctly."""
    
    def test_channel_init_with_negative_100_prefix(self):
        """Test Channel initialization normalizes chat_id with -100 prefix."""
        channel = Channel(chat_id=-1002723750105, channel_name="Test Channel")
        assert channel.chat_id == 2723750105
    
    def test_channel_init_without_prefix(self):
        """Test Channel initialization with already normalized chat_id."""
        channel = Channel(chat_id=2723750105, channel_name="Test Channel")
        assert channel.chat_id == 2723750105
    
    def test_channel_discussion_chat_id_normalization(self):
        """Test that discussion_chat_id is also normalized."""
        channel = Channel(
            chat_id=2723750105,
            discussion_chat_id=-1009876543,
            channel_name="Test Channel"
        )
        assert channel.chat_id == 2723750105
        assert channel.discussion_chat_id == 9876543
    
    def test_channel_from_dict_normalization(self):
        """Test that from_dict normalizes chat_id."""
        data = {
            'chat_id': -1002723750105,
            'channel_name': 'Test Channel',
            'is_private': False
        }
        channel = Channel.from_dict(data)
        assert channel.chat_id == 2723750105
    
    def test_channel_to_dict_preserves_normalized_id(self):
        """Test that to_dict returns normalized chat_id."""
        channel = Channel(chat_id=-1002723750105, channel_name="Test")
        data = channel.to_dict()
        assert data['chat_id'] == 2723750105


class TestPostNormalization:
    """Test that Post class normalizes chat_id correctly."""
    
    def test_post_init_with_negative_100_prefix(self):
        """Test Post initialization normalizes chat_id with -100 prefix."""
        post = Post(
            message_link="https://t.me/testchannel/123",
            chat_id=-1002723750105,
            message_id=123
        )
        assert post.chat_id == 2723750105
    
    def test_post_init_without_prefix(self):
        """Test Post initialization with already normalized chat_id."""
        post = Post(
            message_link="https://t.me/testchannel/123",
            chat_id=2723750105,
            message_id=123
        )
        assert post.chat_id == 2723750105
    
    def test_post_init_with_none_chat_id(self):
        """Test Post initialization with None chat_id."""
        post = Post(message_link="https://t.me/testchannel/123", chat_id=None)
        assert post.chat_id is None
    
    def test_post_to_dict_preserves_normalized_id(self):
        """Test that to_dict returns normalized chat_id."""
        post = Post(
            message_link="https://t.me/testchannel/123",
            chat_id=-1002723750105,
            message_id=123
        )
        data = post.to_dict()
        assert data['chat_id'] == 2723750105
    
    def test_post_from_keys_normalization(self):
        """Test that from_keys normalizes chat_id."""
        post = Post.from_keys(
            message_link="https://t.me/testchannel/123",
            chat_id=-1002723750105,
            message_id=123
        )
        assert post.chat_id == 2723750105


class TestChatIdEquivalence:
    """Test that different representations of same chat_id are treated as equal."""
    
    def test_channel_equivalence(self):
        """Test that channels with -100 prefix and without are equivalent."""
        channel1 = Channel(chat_id=-1002723750105, channel_name="Test")
        channel2 = Channel(chat_id=2723750105, channel_name="Test")
        assert channel1.chat_id == channel2.chat_id
    
    def test_post_equivalence(self):
        """Test that posts with -100 prefix and without are equivalent."""
        post1 = Post(
            message_link="https://t.me/test/123",
            chat_id=-1002723750105
        )
        post2 = Post(
            message_link="https://t.me/test/123",
            chat_id=2723750105
        )
        assert post1.chat_id == post2.chat_id
    
    def test_multiple_representations_normalized_to_same(self):
        """Test various representations normalize to same value."""
        values = [
            -1002723750105,
            2723750105,
        ]
        normalized = [normalize_chat_id(v) for v in values]
        assert len(set(normalized)) == 1  # All should be the same
        assert normalized[0] == 2723750105
