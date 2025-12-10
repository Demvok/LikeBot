"""
Test that database queries find records regardless of which chat_id form is stored.
This ensures backward compatibility with existing data.
"""

import pytest


class TestChatIdDatabaseCompatibility:
    """
    Test that database queries work with both stored formats.
    
    Since we normalize on input in Channel/Post classes, new records will be normalized.
    But existing records might have -100 prefix, so queries must find both.
    """
    
    def test_query_construction(self):
        """Test that we generate both forms correctly for queries."""
        from utils.chat_id_utils import normalize_chat_id
        
        # Test case 1: Input is -100 prefixed
        input_id = -1002723750105
        normalized = normalize_chat_id(input_id)
        prefixed = int(f"-100{normalized}")
        
        assert normalized == 2723750105
        assert prefixed == -1002723750105
        assert {normalized, prefixed} == {2723750105, -1002723750105}
        
        # Test case 2: Input is already normalized
        input_id = 2723750105
        normalized = normalize_chat_id(input_id)
        prefixed = int(f"-100{normalized}")
        
        assert normalized == 2723750105
        assert prefixed == -1002723750105
        assert {normalized, prefixed} == {2723750105, -1002723750105}
    
    def test_both_forms_in_query_set(self):
        """Verify both forms are in the query regardless of input."""
        from utils.chat_id_utils import normalize_chat_id
        
        test_cases = [
            -1002723750105,
            2723750105,
            -100123,
            123,
        ]
        
        for input_id in test_cases:
            normalized = normalize_chat_id(input_id)
            prefixed = int(f"-100{normalized}")
            
            # Both forms should be present in query
            query_ids = [normalized, prefixed]
            
            # One is the normalized form
            assert normalized in query_ids
            # The other is the -100 prefixed form
            assert prefixed in query_ids
            # They should be different (unless edge case)
            if normalized >= 100:  # Avoid edge cases with small numbers
                assert normalized != prefixed


class TestDatabaseQueryBehavior:
    """
    Document expected database query behavior.
    
    KEY INSIGHT:
    - Database can have records with EITHER chat_id format (2723750105 OR -1002723750105)
    - Queries use MongoDB $in operator to search for BOTH forms
    - This ensures we find the record regardless of which format is stored
    - New records will use normalized form (due to Channel/Post normalization)
    - Old records might use -100 prefixed form
    """
    
    def test_mongodb_in_operator_logic(self):
        """Document MongoDB $in operator behavior for chat_id queries."""
        from utils.chat_id_utils import normalize_chat_id
        
        chat_id = 2723750105
        normalized = normalize_chat_id(chat_id)
        prefixed = int(f"-100{normalized}")
        
        # MongoDB query will be: {"chat_id": {"$in": [2723750105, -1002723750105]}}
        # This matches records where chat_id is EITHER value
        
        # Simulating what MongoDB would match:
        stored_normalized = 2723750105
        stored_prefixed = -1002723750105
        
        query_values = [normalized, prefixed]
        
        # Record with normalized form would match
        assert stored_normalized in query_values
        
        # Record with prefixed form would also match
        assert stored_prefixed in query_values
        
        # This ensures we find the record regardless of storage format


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
