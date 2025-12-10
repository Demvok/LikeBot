"""
Test that task-scoped cache works correctly with multiple accounts.
Verifies that the cache isolation works when cache is injected by Task._run().
"""
import pytest
import asyncio
from auxilary_logic.telegram_cache import TelegramCache


@pytest.mark.asyncio
async def test_task_scope_cache_with_multiple_accounts():
    """
    Test the actual task usage pattern:
    1. Task creates ONE shared cache instance
    2. Task injects it into ALL clients
    3. Each client uses get_entity_cached() which calls cache.get_entity(id, self)
    4. Cache uses client.phone_number to isolate entries per account
    """
    
    # Mock the Telegram client
    class MockTelegramClient:
        def __init__(self, account_name):
            self.account_name = account_name
        
        async def get_entity(self, identifier):
            # Simulate that different accounts have different entity data
            return {
                "id": identifier,
                "account": self.account_name,
                "username": f"user_{identifier}_{self.account_name}"
            }
    
    # Mock Client class (simplified version)
    class MockClient:
        def __init__(self, phone_number, account_name):
            self.phone_number = phone_number
            self.client = MockTelegramClient(account_name)
            self.telegram_cache = None  # Will be injected by "Task"
        
        async def get_entity_cached(self, identifier):
            """This mimics the real get_entity_cached implementation."""
            if self.telegram_cache is None:
                raise RuntimeError("telegram_cache not initialized")
            return await self.telegram_cache.get_entity(identifier, self)
    
    # Simulate Task._run() pattern
    print("\n" + "="*70)
    print("SIMULATING TASK EXECUTION WITH SHARED CACHE")
    print("="*70)
    
    # 1. Task creates ONE cache instance
    task_cache = TelegramCache(task_id=1)
    print("\n1. Task created shared cache instance")
    
    # 2. Task creates multiple clients
    client_alice = MockClient("+1234567890", "Alice")
    client_bob = MockClient("+0987654321", "Bob")
    client_charlie = MockClient("+1111111111", "Charlie")
    print("2. Task created 3 clients (Alice, Bob, Charlie)")
    
    # 3. Task injects cache into all clients (this is what task.py does)
    for client in [client_alice, client_bob, client_charlie]:
        client.telegram_cache = task_cache
    print("3. Task injected shared cache into all clients")
    
    # 4. All clients request the same entity ID (123)
    print("\n4. All clients request entity 123:")
    
    entity_alice = await client_alice.get_entity_cached(123)
    print(f"   Alice got: {entity_alice}")
    
    entity_bob = await client_bob.get_entity_cached(123)
    print(f"   Bob got: {entity_bob}")
    
    entity_charlie = await client_charlie.get_entity_cached(123)
    print(f"   Charlie got: {entity_charlie}")
    
    # 5. Verify each got their own account-specific entity
    print("\n5. Verification:")
    assert entity_alice["account"] == "Alice", "Alice should get her own entity"
    assert entity_bob["account"] == "Bob", "Bob should get his own entity"
    assert entity_charlie["account"] == "Charlie", "Charlie should get his own entity"
    print("   ✓ Each account got its own entity (not shared)")
    
    # 6. Verify cache has 3 separate entries
    assert len(task_cache._cache) == 3, "Cache should have 3 entries (one per account)"
    print(f"   ✓ Cache has {len(task_cache._cache)} separate entries")
    
    # 7. Second request by Alice should hit cache
    print("\n6. Alice requests entity 123 again:")
    entity_alice_2 = await client_alice.get_entity_cached(123)
    assert entity_alice == entity_alice_2, "Should be same entity from cache"
    print("   ✓ Got same entity from cache (hit)")
    
    # 8. Check stats
    stats = task_cache.get_stats()
    print(f"\n7. Cache stats: hits={stats['hits']}, misses={stats['misses']}, hit_rate={stats['hit_rate_percent']}%")
    assert stats['hits'] == 1, "Should have 1 cache hit (Alice's 2nd request)"
    assert stats['misses'] == 3, "Should have 3 cache misses (first request per account)"
    
    # 9. Show cache keys
    print("\n8. Cache keys (per-account isolation):")
    for key in task_cache._cache.keys():
        cache_type, phone, entity_id = key
        print(f"   ({cache_type}, {phone}, {entity_id})")
    
    print("\n" + "="*70)
    print("✓ TASK-SCOPED CACHE WITH PER-ACCOUNT ISOLATION WORKS CORRECTLY!")
    print("="*70)


@pytest.mark.asyncio
async def test_cache_prevents_cross_account_contamination():
    """Verify that Account A cannot accidentally get Account B's cached entity."""
    
    class MockTelegramClient:
        def __init__(self, secret_data):
            self.secret_data = secret_data
        
        async def get_entity(self, identifier):
            # Each account has different secret data
            return {"id": identifier, "secret": self.secret_data}
    
    class MockClient:
        def __init__(self, phone, secret):
            self.phone_number = phone
            self.client = MockTelegramClient(secret)
            self.telegram_cache = None
        
        async def get_entity_cached(self, identifier):
            if self.telegram_cache is None:
                raise RuntimeError("telegram_cache not initialized")
            return await self.telegram_cache.get_entity(identifier, self)
    
    # Setup
    cache = TelegramCache(task_id=999)
    
    client_a = MockClient("+1111111111", "SECRET_A")
    client_b = MockClient("+2222222222", "SECRET_B")
    
    client_a.telegram_cache = cache
    client_b.telegram_cache = cache
    
    # Both request entity 999
    entity_a = await client_a.get_entity_cached(999)
    entity_b = await client_b.get_entity_cached(999)
    
    # Critical assertion: they must have different secrets
    assert entity_a["secret"] == "SECRET_A", "Client A must get its own secret"
    assert entity_b["secret"] == "SECRET_B", "Client B must get its own secret"
    assert entity_a["secret"] != entity_b["secret"], "Secrets must not leak across accounts"
    
    print("\n✓ Cache correctly prevents cross-account data contamination")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
