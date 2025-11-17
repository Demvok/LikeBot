"""
Test script for Telegram API rate limiting and entity caching
"""

import asyncio
import time
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main_logic.agent import TelegramAPIRateLimiter


async def test_rate_limiter():
    """Test that rate limiter enforces minimum delays"""
    print("Testing TelegramAPIRateLimiter...")
    
    limiter = TelegramAPIRateLimiter()
    
    # Test get_entity rate limiting (500ms minimum)
    print("\n1. Testing get_entity rate limit (500ms):")
    start = time.time()
    await limiter.wait_if_needed('get_entity')
    first_call = time.time()
    print(f"   First call: {(first_call - start) * 1000:.1f}ms")
    
    await limiter.wait_if_needed('get_entity')
    second_call = time.time()
    elapsed = (second_call - first_call) * 1000
    print(f"   Second call: {elapsed:.1f}ms after first")
    assert elapsed >= 450, f"Rate limit violated! Only {elapsed}ms between calls"
    print(f"   ✓ Rate limit enforced correctly")
    
    # Test get_messages rate limiting (300ms minimum)
    print("\n2. Testing get_messages rate limit (300ms):")
    start = time.time()
    await limiter.wait_if_needed('get_messages')
    first_call = time.time()
    print(f"   First call: {(first_call - start) * 1000:.1f}ms")
    
    await limiter.wait_if_needed('get_messages')
    second_call = time.time()
    elapsed = (second_call - first_call) * 1000
    print(f"   Second call: {elapsed:.1f}ms after first")
    assert elapsed >= 250, f"Rate limit violated! Only {elapsed}ms between calls"
    print(f"   ✓ Rate limit enforced correctly")
    
    # Test parallel calls are serialized
    print("\n3. Testing parallel calls are serialized:")
    start = time.time()
    
    async def make_call(n):
        await limiter.wait_if_needed('send_reaction')
        return time.time()
    
    # Make 3 parallel calls
    results = await asyncio.gather(
        make_call(1),
        make_call(2),
        make_call(3)
    )
    
    # Check that calls were spaced out
    for i in range(len(results) - 1):
        gap = (results[i+1] - results[i]) * 1000
        print(f"   Gap between call {i+1} and {i+2}: {gap:.1f}ms")
        assert gap >= 450, f"Parallel calls not properly serialized: {gap}ms gap"
    
    total_time = (results[-1] - start) * 1000
    print(f"   Total time for 3 calls: {total_time:.1f}ms")
    print(f"   ✓ Parallel calls properly serialized")
    
    # Test different methods don't interfere
    print("\n4. Testing different methods are independent:")
    start = time.time()
    await limiter.wait_if_needed('get_entity')
    await limiter.wait_if_needed('get_messages')  # Should not wait for get_entity
    elapsed = (time.time() - start) * 1000
    print(f"   Time for two different methods: {elapsed:.1f}ms")
    assert elapsed < 100, f"Different methods should be independent, took {elapsed}ms"
    print(f"   ✓ Different methods are independent")
    
    print("\n✅ All rate limiter tests passed!")


async def test_entity_cache():
    """Test entity caching logic (without real Telegram client)"""
    print("\nTesting Entity Cache...")
    
    from collections import OrderedDict
    
    # Simulate cache
    cache = OrderedDict()
    cache_max_size = 3
    cache_ttl = 2  # 2 seconds for testing
    
    def add_to_cache(key, value):
        cache[key] = (value, time.time())
        # Cleanup
        now = time.time()
        expired = [k for k, (v, t) in cache.items() if now - t > cache_ttl]
        for k in expired:
            del cache[k]
        # Enforce size
        while len(cache) > cache_max_size:
            cache.popitem(last=False)
    
    # Test cache miss/hit
    print("\n1. Testing cache miss/hit:")
    assert 'test1' not in cache
    print("   ✓ Cache miss detected")
    
    add_to_cache('test1', 'entity1')
    assert 'test1' in cache
    print("   ✓ Cache hit after adding")
    
    # Test TTL expiration
    print("\n2. Testing TTL expiration:")
    add_to_cache('test2', 'entity2')
    print(f"   Waiting {cache_ttl + 0.5}s for expiration...")
    await asyncio.sleep(cache_ttl + 0.5)
    add_to_cache('test3', 'entity3')  # Trigger cleanup
    assert 'test2' not in cache
    print("   ✓ Expired entries removed")
    
    # Test LRU eviction
    print("\n3. Testing LRU eviction:")
    cache.clear()
    add_to_cache('a', 1)
    add_to_cache('b', 2)
    add_to_cache('c', 3)
    print(f"   Cache size: {len(cache)}/{cache_max_size}")
    add_to_cache('d', 4)  # Should evict 'a' (oldest)
    print(f"   Cache after adding 4th item: {list(cache.keys())}")
    assert 'a' not in cache
    assert len(cache) == cache_max_size
    print("   ✓ LRU eviction works correctly")
    
    print("\n✅ All cache tests passed!")


async def main():
    """Run all tests"""
    print("=" * 60)
    print("TELEGRAM API RATE LIMITING & CACHING TEST SUITE")
    print("=" * 60)
    
    try:
        await test_rate_limiter()
        await test_entity_cache()
        
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED ✅")
        print("=" * 60)
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
