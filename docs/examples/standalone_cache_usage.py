"""
Example: Using Client with standalone cache for debugging/testing

This demonstrates how to use Client.init_standalone_cache() to enable
caching when working with Client outside of a Task execution context.
"""
import asyncio
from main_logic.database import get_db
from main_logic.agent import Client


async def debug_example():
    """Example of using standalone cache for debugging."""
    
    print("\n" + "="*70)
    print("STANDALONE CACHE DEBUGGING EXAMPLE")
    print("="*70 + "\n")
    
    # Load an account from database
    db = get_db()
    accounts = await db.load_all_accounts()
    
    if not accounts:
        print("❌ No accounts found. Please add an account first.")
        return
    
    account = accounts[0]
    print(f"✓ Using account: {account.phone_number}\n")
    
    # Create client
    client = Client(account)
    
    # IMPORTANT: Initialize standalone cache for debugging
    print("Initializing standalone cache...")
    client.init_standalone_cache(max_size=200)
    print("✓ Standalone cache initialized\n")
    
    # Connect to Telegram
    print("Connecting to Telegram...")
    await client.connect()
    print("✓ Connected\n")
    
    try:
        # Now you can use cached methods!
        print("Testing cached entity fetches...")
        
        # First fetch - will hit API
        chat_id = 777000  # Telegram service notifications
        entity1 = await client.get_entity_cached(chat_id)
        print(f"✓ First fetch (API call): {getattr(entity1, 'title', 'N/A')}")
        
        # Second fetch - should hit cache
        entity2 = await client.get_entity_cached(chat_id)
        print(f"✓ Second fetch (cached): {getattr(entity2, 'title', 'N/A')}")
        
        # Check cache stats
        stats = client.telegram_cache.get_stats()
        print(f"\nCache statistics:")
        print(f"  Hits: {stats['hits']}")
        print(f"  Misses: {stats['misses']}")
        print(f"  Hit rate: {stats['hit_rate_percent']}%")
        print(f"  Cache size: {stats['cache_size']}")
        
    finally:
        # Cleanup
        await client.disconnect()
        print("\n✓ Disconnected")
    
    print("\n" + "="*70)
    print("EXAMPLE COMPLETE")
    print("="*70)


async def comparison_without_cache():
    """Show what happens WITHOUT standalone cache."""
    
    print("\n" + "="*70)
    print("COMPARISON: WITHOUT STANDALONE CACHE")
    print("="*70 + "\n")
    
    db = get_db()
    accounts = await db.load_all_accounts()
    
    if not accounts:
        print("❌ No accounts found")
        return
    
    account = accounts[0]
    client = Client(account)
    
    # Don't initialize cache!
    await client.connect()
    
    try:
        # Try to use cached method - will fail
        print("Attempting to use get_entity_cached without cache...")
        entity = await client.get_entity_cached(777000)
    except RuntimeError as e:
        print(f"✓ Expected error caught: {e}\n")
        print("Solution: Either use client.init_standalone_cache()")
        print("          or call client.client.get_entity() directly")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    print("\n🔧 Standalone Cache Usage Examples\n")
    
    # Run example with cache
    asyncio.run(debug_example())
    
    # Run comparison without cache
    asyncio.run(comparison_without_cache())
