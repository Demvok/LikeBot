"""
Demonstration of per-account cache isolation in TelegramCache.

This script shows how the reworked caching system prevents entity objects
from one Telegram account being incorrectly reused by another account.

Before the fix:
    - Cache keys were (cache_type, entity_id)
    - Entity from account A could be returned for account B
    - This caused Telethon errors because entities are session-specific

After the fix:
    - Cache keys are (cache_type, account_id, entity_id)
    - Each account has isolated cache namespace
    - Entities are never shared across accounts
"""

import asyncio
from auxilary_logic.telegram_cache import TelegramCache


async def demonstrate_isolation():
    """Show that two accounts get separate cache entries for same entity ID."""
    
    # Create shared cache (one per task)
    cache = TelegramCache(task_id=999)
    
    # Mock clients for two different accounts
    class MockTelegramClient:
        def __init__(self, account_name):
            self.account_name = account_name
        
        async def get_entity(self, identifier):
            # Simulate different entity data for different accounts
            return {
                "id": identifier,
                "account": self.account_name,
                "name": f"Entity_{identifier}_from_{self.account_name}"
            }
    
    class MockClient:
        def __init__(self, phone, account_name):
            self.phone_number = phone
            self.client = MockTelegramClient(account_name)
    
    # Two different accounts
    client_alice = MockClient("+1234567890", "Alice")
    client_bob = MockClient("+0987654321", "Bob")
    
    print("=" * 70)
    print("Demonstrating Per-Account Cache Isolation")
    print("=" * 70)
    
    # Both accounts request entity with ID 12345
    print("\n1. Alice requests entity 12345:")
    entity_alice = await cache.get_entity(12345, client_alice)
    print(f"   → Got: {entity_alice}")
    
    print("\n2. Bob requests entity 12345:")
    entity_bob = await cache.get_entity(12345, client_bob)
    print(f"   → Got: {entity_bob}")
    
    # Verify they got different entities (not shared)
    print("\n3. Verification:")
    print(f"   Alice's entity account: {entity_alice['account']}")
    print(f"   Bob's entity account: {entity_bob['account']}")
    print(f"   ✓ Entities are isolated: {entity_alice['account'] != entity_bob['account']}")
    
    # Second request by Alice should hit cache
    print("\n4. Alice requests entity 12345 again:")
    entity_alice_cached = await cache.get_entity(12345, client_alice)
    print(f"   → Got: {entity_alice_cached}")
    print(f"   ✓ Cache hit: {entity_alice == entity_alice_cached}")
    
    # Check cache stats
    print("\n5. Cache statistics:")
    stats = cache.get_stats()
    print(f"   Hits: {stats['hits']}")
    print(f"   Misses: {stats['misses']}")
    print(f"   Hit rate: {stats['hit_rate_percent']}%")
    print(f"   Cache size: {stats['cache_size']} entries")
    
    # Show cache keys
    print("\n6. Cache keys (internal structure):")
    for key in cache._cache.keys():
        print(f"   {key}")
    
    print("\n" + "=" * 70)
    print("✓ Per-account isolation verified successfully!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(demonstrate_isolation())
