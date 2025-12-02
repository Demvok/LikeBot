"""
Test improved error handling for non-existent and invalid usernames.
"""
import pytest
from telethon import errors


class TestUsernameErrorHandling:
    """Test that username errors are properly caught and reported."""
    
    @pytest.mark.asyncio
    async def test_username_not_occupied_error_message(self, monkeypatch):
        """Verify clear error message when username doesn't exist."""
        from main_logic.client_mixins.entity_resolution import EntityResolutionMixin
        
        # Mock client
        class MockClient(EntityResolutionMixin):
            def __init__(self):
                self.phone_number = "+1234567890"
                self.telegram_cache = None
                from utils.logger import setup_logger
                self.logger = setup_logger("test_client", "main.log")
            
            async def ensure_connected(self):
                pass
            
            async def get_entity_cached(self, identifier):
                # Simulate Telethon error for non-existent username
                raise errors.UsernameNotOccupiedError(
                    request=None,
                    message=f"No user has \"{identifier}\" as username"
                )
        
        client = MockClient()
        
        # Test that error is caught and re-raised with helpful message
        with pytest.raises(ValueError) as exc_info:
            await client.get_message_ids("https://t.me/nonexistent_channel/123")
        
        error_msg = str(exc_info.value)
        assert "does not exist" in error_msg
        assert "deleted, changed its username, or the link is incorrect" in error_msg
        assert "nonexistent_channel" in error_msg
    
    @pytest.mark.asyncio
    async def test_username_invalid_error_message(self, monkeypatch):
        """Verify clear error message when username format is invalid."""
        from main_logic.client_mixins.entity_resolution import EntityResolutionMixin
        
        # Mock client
        class MockClient(EntityResolutionMixin):
            def __init__(self):
                self.phone_number = "+1234567890"
                self.telegram_cache = None
                from utils.logger import setup_logger
                self.logger = setup_logger("test_client", "main.log")
            
            async def ensure_connected(self):
                pass
            
            async def get_entity_cached(self, identifier):
                # Simulate Telethon error for invalid username format
                raise errors.UsernameInvalidError(
                    request=None,
                    message=f"The username is not valid"
                )
        
        client = MockClient()
        
        # Test that error is caught and re-raised with helpful message
        with pytest.raises(ValueError) as exc_info:
            await client.get_message_ids("https://t.me/invalid@#$channel/123")
        
        error_msg = str(exc_info.value)
        assert "invalid format" in error_msg
        assert "Check the link for typos" in error_msg
    
    @pytest.mark.asyncio
    async def test_fallback_candidates_skip_username_errors(self, monkeypatch):
        """Verify that fallback candidates skip username errors and try all options."""
        from main_logic.client_mixins.entity_resolution import EntityResolutionMixin
        
        call_count = [0]
        attempted_identifiers = []
        
        # Mock client
        class MockClient(EntityResolutionMixin):
            def __init__(self):
                self.phone_number = "+1234567890"
                self.telegram_cache = None
                from utils.logger import setup_logger
                self.logger = setup_logger("test_client", "main.log")
            
            async def ensure_connected(self):
                pass
            
            async def get_entity_cached(self, identifier):
                attempted_identifiers.append(identifier)
                call_count[0] += 1
                
                # All attempts fail with UsernameNotOccupiedError
                raise errors.UsernameNotOccupiedError(
                    request=None,
                    message=f"No user has \"{identifier}\" as username"
                )
        
        client = MockClient()
        
        # Test that all candidates are tried before final error
        with pytest.raises(ValueError) as exc_info:
            await client.get_message_ids("https://t.me/test_channel/123")
        
        # Should try initial identifier + fallback candidates
        # Initial: "test_channel"
        # Fallbacks: "https://t.me/test_channel", "http://t.me/test_channel", 
        #            "t.me/test_channel", "@test_channel"
        assert call_count[0] == 5, f"Expected 5 attempts, got {call_count[0]}"
        assert "test_channel" in attempted_identifiers
        assert "@test_channel" in attempted_identifiers
        
        # Final error should mention username doesn't exist
        error_msg = str(exc_info.value)
        assert "does not exist" in error_msg
        assert "test_channel" in error_msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
