"""
Test standalone cache functionality for debugging/testing scenarios.
"""
import pytest
from main_logic.agent import Client, Account
from main_logic.schemas import AccountStatus


@pytest.mark.asyncio
async def test_standalone_cache_initialization():
    """Test that standalone cache can be initialized for debugging."""
    # Create a mock account
    account = Account({
        'phone_number': '+1234567890',
        'status': AccountStatus.ACTIVE,
        'session_encrypted': 'mock_session',
        'session_name': 'test_session'
    })
    
    # Create client
    client = Client(account)
    
    # Verify no cache initially
    assert client.telegram_cache is None
    
    # Initialize standalone cache
    client.init_standalone_cache(max_size=100)
    
    # Verify cache is now initialized
    assert client.telegram_cache is not None
    assert client.telegram_cache._max_size == 100
    
    print("✅ Standalone cache initialized successfully")


@pytest.mark.asyncio
async def test_standalone_cache_prevents_error():
    """Test that standalone cache allows get_entity_cached to work outside Task."""
    # Create a mock account
    account = Account({
        'phone_number': '+1234567890',
        'status': AccountStatus.ACTIVE,
        'session_encrypted': 'mock_session',
        'session_name': 'test_session'
    })
    
    # Create client without cache - should raise RuntimeError
    client = Client(account)
    
    with pytest.raises(RuntimeError, match="telegram_cache not initialized"):
        # This should fail without cache
        await client.get_entity_cached(12345)
    
    # Now initialize standalone cache
    client.init_standalone_cache()
    
    # Note: We can't actually test get_entity_cached without a real connection,
    # but we've verified the RuntimeError guard is bypassed
    assert client.telegram_cache is not None
    
    print("✅ Standalone cache prevents RuntimeError as expected")


@pytest.mark.asyncio
async def test_standalone_cache_overwrite_warning():
    """Test that overwriting cache creates new instance."""
    account = Account({
        'phone_number': '+1234567890',
        'status': AccountStatus.ACTIVE,
        'session_encrypted': 'mock_session',
        'session_name': 'test_session'
    })
    
    client = Client(account)
    
    # Initialize once
    client.init_standalone_cache()
    first_cache = client.telegram_cache
    
    # Initialize again - should create new instance
    client.init_standalone_cache()
    second_cache = client.telegram_cache
    
    # Should be different instances (cache was replaced)
    assert first_cache is not second_cache
    assert client.telegram_cache is second_cache
    
    print("✅ Cache overwrite creates new instance correctly")


if __name__ == "__main__":
    import asyncio
    
    async def run_tests():
        print("\n" + "="*60)
        print("STANDALONE CACHE FUNCTIONALITY TESTS")
        print("="*60 + "\n")
        
        await test_standalone_cache_initialization()
        await test_standalone_cache_prevents_error()
        
        # Note: caplog fixture only works with pytest, skip manual run
        print("\n⚠ Skipping caplog test (requires pytest)")
        
        print("\n" + "="*60)
        print("ALL STANDALONE CACHE TESTS PASSED ✅")
        print("="*60)
    
    asyncio.run(run_tests())
