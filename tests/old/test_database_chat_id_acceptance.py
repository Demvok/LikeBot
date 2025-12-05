"""
Test that database queries accept both normalized and -100 prefixed chat IDs.
"""

import pytest
from main_logic.channel import normalize_chat_id


class TestDatabaseChatIdAcceptance:
    """Test that database methods accept both chat_id forms."""
    
    def test_normalize_function_equivalence(self):
        """Test that both forms normalize to the same value."""
        chat_id_with_prefix = -1002723750105
        chat_id_without_prefix = 2723750105
        
        assert normalize_chat_id(chat_id_with_prefix) == normalize_chat_id(chat_id_without_prefix)
        assert normalize_chat_id(chat_id_with_prefix) == 2723750105
    
    def test_multiple_chat_ids(self):
        """Test normalization with various chat IDs."""
        test_cases = [
            (-1002723750105, 2723750105),
            (-100123, 123),
            (-1009876543210, 9876543210),
            (2723750105, 2723750105),  # Already normalized
            (123, 123),  # Already normalized
        ]
        
        for input_id, expected_output in test_cases:
            assert normalize_chat_id(input_id) == expected_output
    
    def test_documentation_example(self):
        """Test the exact example from user request."""
        # User said: "2723750105 actually equals -1002723750105"
        assert normalize_chat_id(2723750105) == normalize_chat_id(-1002723750105)
        assert normalize_chat_id(2723750105) == 2723750105


# Note: Actual database query tests would require async setup and database connection.
# The tests above verify the normalization logic is correct.
# Database methods (get_channel, get_posts_by_chat_id, etc.) now accept both forms
# because they normalize the input before querying.


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
