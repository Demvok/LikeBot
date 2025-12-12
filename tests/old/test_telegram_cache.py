"""
Test suite for Telegram cache system.

Tests cover:
- Basic cache hits/misses and expiration
- Per-account cache isolation
- Concurrent in-flight request de-duplication
- Thread safety with concurrent different keys
- LRU eviction policy
- Error propagation to waiters
- Integration with mock Telegram client
"""

import pytest
import asyncio
from auxilary_logic.telegram_cache import (
    TelegramCache,
    CacheEntry,
    InFlightRequest,
    TelegramCacheScope,
)

# Test account identifiers
ACCOUNT_1 = "+1234567890"
ACCOUNT_2 = "+0987654321"


@pytest.mark.asyncio
async def test_cache_hit():
    """Test basic cache hit scenario."""
    cache = TelegramCache(task_id=1)
    
    call_count = 0
    async def fetch_func():
        nonlocal call_count
        call_count += 1
        return {"id": 123, "name": "Test"}
    
    # First call - cache miss
    result1 = await cache.get("entity", ACCOUNT_1, 123, fetch_func)
    assert result1 == {"id": 123, "name": "Test"}
    assert call_count == 1
    
    # Second call - cache hit
    result2 = await cache.get("entity", ACCOUNT_1, 123, fetch_func)
    assert result2 == {"id": 123, "name": "Test"}
    assert call_count == 1  # Should not increase
    
    stats = cache.get_stats()
    assert stats['hits'] == 1
    assert stats['misses'] == 1
    assert stats['hit_rate_percent'] == 50.0


@pytest.mark.asyncio
async def test_cache_expiration():
    """Test that expired entries trigger refetch."""
    cache = TelegramCache(task_id=1)
    
    call_count = 0
    async def fetch_func():
        nonlocal call_count
        call_count += 1
        return f"value_{call_count}"
    
    # First call
    result1 = await cache.get("entity", ACCOUNT_1, 123, fetch_func, ttl=0.1)  # 100ms TTL
    assert result1 == "value_1"
    
    # Wait for expiration
    await asyncio.sleep(0.15)
    
    # Second call - should refetch
    result2 = await cache.get("entity", ACCOUNT_1, 123, fetch_func, ttl=0.1)
    assert result2 == "value_2"
    assert call_count == 2


@pytest.mark.asyncio
async def test_in_flight_deduplication():
    """Test that concurrent requests for same key only trigger one fetch."""
    cache = TelegramCache(task_id=1)
    
    call_count = 0
    async def slow_fetch():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.1)  # Simulate slow API call
        return {"id": 123}
    
    # Launch 10 concurrent requests for same entity
    tasks = [cache.get("entity", ACCOUNT_1, 123, slow_fetch) for _ in range(10)]
    results = await asyncio.gather(*tasks)
    
    # All should return same result
    assert all(r == {"id": 123} for r in results)
    
    # Only ONE fetch should have occurred
    assert call_count == 1
    
    stats = cache.get_stats()
    assert stats['dedup_saves'] == 9  # 9 requests saved from duplicate fetch


@pytest.mark.asyncio
async def test_cross_account_isolation():
    """Accounts should maintain isolated cache entries for identical keys."""
    cache = TelegramCache(task_id=1)

    call_count = 0

    def fetch_factory(account):
        async def fetch():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return {"id": 555, "account": account}
        return fetch

    results = await asyncio.gather(
        cache.get("entity", ACCOUNT_1, 555, fetch_factory(ACCOUNT_1)),
        cache.get("entity", ACCOUNT_2, 555, fetch_factory(ACCOUNT_2)),
    )

    assert results[0]["account"] == ACCOUNT_1
    assert results[1]["account"] == ACCOUNT_2
    assert call_count == 2
    assert cache.get_stats()['dedup_saves'] == 0


@pytest.mark.asyncio
async def test_thread_safety_concurrent_different_keys():
    """Test that concurrent requests for different keys work correctly."""
    cache = TelegramCache(task_id=1)
    
    call_counts = {}
    
    async def fetch_factory(key):
        async def fetch():
            call_counts[key] = call_counts.get(key, 0) + 1
            await asyncio.sleep(0.05)
            return {"id": key}
        return fetch
    
    # Launch concurrent requests for 20 different keys
    tasks = []
    for i in range(20):
        tasks.append(cache.get("entity", ACCOUNT_1, i, await fetch_factory(i)))
    
    results = await asyncio.gather(*tasks)
    
    # All should succeed
    assert len(results) == 20
    
    # Each key should have been fetched exactly once
    assert all(count == 1 for count in call_counts.values())


@pytest.mark.asyncio
async def test_lru_eviction():
    """Test that cache evicts oldest entries when max_size exceeded."""
    cache = TelegramCache(task_id=1, max_size=5)
    
    # Fill cache to max
    for i in range(5):
        async def fetch(val=i):
            return {"id": val}
        await cache.get("entity", ACCOUNT_1, i, fetch)
    
    assert len(cache._cache) == 5
    
    # Add one more - should evict oldest (key 0)
    async def fetch_new():
        return {"id": 5}
    await cache.get("entity", ACCOUNT_1, 5, fetch_new)
    
    assert len(cache._cache) == 5
    assert cache.get_stats()['evictions'] == 1
    
    # Key 0 should be evicted
    cache_keys = [k[1] for k in cache._cache.keys()]
    assert "0" not in cache_keys
    assert "5" in cache_keys


@pytest.mark.asyncio
async def test_fetch_error_propagation():
    """Test that fetch errors propagate to all waiters."""
    cache = TelegramCache(task_id=1)
    
    async def failing_fetch():
        await asyncio.sleep(0.05)
        raise ValueError("API Error")
    
    # Launch concurrent requests that will all fail
    tasks = [cache.get("entity", ACCOUNT_1, 123, failing_fetch) for _ in range(5)]
    
    # All should raise the same exception
    with pytest.raises(ValueError, match="API Error"):
        await asyncio.gather(*tasks)
    
    # No entry should be cached
    assert len(cache._cache) == 0


@pytest.mark.asyncio
async def test_get_entity_integration():
    """Test get_entity() convenience method with mock client."""
    cache = TelegramCache(task_id=1)
    
    # Mock client
    class MockTelegramClient:
        async def get_entity(self, identifier):
            return {"id": identifier, "name": f"User{identifier}"}
    
    class MockClient:
        def __init__(self, phone):
            self.client = MockTelegramClient()
            self.phone_number = phone
    
    mock_client = MockClient(ACCOUNT_1)
    
    # First call
    entity1 = await cache.get_entity(123, mock_client)
    assert entity1 == {"id": 123, "name": "User123"}
    
    # Second call - should hit cache
    entity2 = await cache.get_entity(123, mock_client)
    assert entity2 == entity1
    
    stats = cache.get_stats()
    assert stats['hits'] == 1
    assert stats['misses'] == 1


@pytest.mark.asyncio
async def test_key_normalization():
    """Test that cache key normalization works correctly."""
    cache = TelegramCache(task_id=1)
    
    # Test username normalization
    assert cache._normalize_key("entity", "@username") == ("entity", "username")
    assert cache._normalize_key("entity", "USERNAME") == ("entity", "username")
    assert cache._normalize_key("entity", "@UsErNaMe") == ("entity", "username")
    
    # Test integer keys
    assert cache._normalize_key("entity", 12345) == ("entity", "12345")
    
    # Test tuple keys (for composite keys like message)
    assert cache._normalize_key("message", (12345, 678)) == ("message", "12345:678")


@pytest.mark.asyncio
async def test_cache_invalidation():
    """Test manual cache invalidation."""
    cache = TelegramCache(task_id=1)
    
    async def fetch():
        return {"data": "test"}
    
    # Add entry to cache
    await cache.get("entity", ACCOUNT_1, 123, fetch)
    assert len(cache._cache) == 1
    
    # Invalidate it
    result = await cache.invalidate("entity", ACCOUNT_1, 123)
    assert result is True
    assert len(cache._cache) == 0
    
    # Try to invalidate non-existent entry
    result = await cache.invalidate("entity", ACCOUNT_1, 999)
    assert result is False


@pytest.mark.asyncio
async def test_cache_clear():
    """Test clearing entire cache."""
    cache = TelegramCache(task_id=1)
    
    # Add multiple entries
    for i in range(10):
        async def fetch(val=i):
            return {"id": val}
        await cache.get("entity", ACCOUNT_1, i, fetch)
    
    assert len(cache._cache) == 10
    
    # Clear cache
    await cache.clear()
    assert len(cache._cache) == 0
    assert len(cache._in_flight) == 0


@pytest.mark.asyncio
async def test_different_cache_types():
    """Test that different cache types are stored separately."""
    cache = TelegramCache(task_id=1)
    
    async def fetch_entity():
        return {"type": "entity", "id": 123}
    
    async def fetch_message():
        return {"type": "message", "id": 123}
    
    # Store same key in different cache types
    entity = await cache.get(TelegramCache.ENTITY, ACCOUNT_1, 123, fetch_entity)
    message = await cache.get(TelegramCache.MESSAGE, ACCOUNT_1, 123, fetch_message)
    
    assert entity["type"] == "entity"
    assert message["type"] == "message"
    assert len(cache._cache) == 2


@pytest.mark.asyncio
async def test_rate_limiter_integration():
    """Test that rate limiting is applied on cache misses."""
    import time
    cache = TelegramCache(task_id=1)
    
    call_times = []
    
    async def fetch():
        call_times.append(time.time())
        return {"id": 123}
    
    # First call - will apply rate limiting
    await cache.get("entity", ACCOUNT_1, 1, fetch, rate_limit_method='get_entity')
    
    # Different key - will apply rate limiting again
    await cache.get("entity", ACCOUNT_1, 2, fetch, rate_limit_method='get_entity')
    
    # Ensure rate limiting caused a delay (at least 2.5 seconds based on config)
    if len(call_times) >= 2:
        delay = call_times[1] - call_times[0]
        assert delay >= 2.5  # config.yaml has rate_limit_get_entity: 3


@pytest.mark.asyncio
async def test_concurrent_mixed_operations():
    """Test concurrent mix of hits, misses, and in-flight requests."""
    cache = TelegramCache(task_id=1)
    
    call_counts = {}
    
    async def fetch_factory(key):
        async def fetch():
            call_counts[key] = call_counts.get(key, 0) + 1
            await asyncio.sleep(0.05)
            return {"id": key}
        return fetch
    
    # Pre-populate some entries
    for i in range(5):
        await cache.get("entity", ACCOUNT_1, i, await fetch_factory(i))
    
    # Launch mixed operations:
    # - Some will hit cache (0-4)
    # - Some will miss (5-9)
    # - Some duplicates will wait in-flight
    tasks = []
    for i in range(20):
        key = i % 10  # Create duplicates
        tasks.append(cache.get("entity", ACCOUNT_1, key, await fetch_factory(key)))
    
    results = await asyncio.gather(*tasks)
    
    # All should succeed
    assert len(results) == 20
    
    # Keys 0-4 should have been called once (during pre-population)
    # Keys 5-9 should have been called once (during concurrent ops)
    for key in range(10):
        assert call_counts[key] == 1
    
    # Cache should have 10 entries
    assert len(cache._cache) == 10


@pytest.mark.asyncio
async def test_cache_stats_accuracy():
    """Test that cache statistics are accurate."""
    cache = TelegramCache(task_id=1)
    
    async def fetch(val):
        return {"id": val}
    
    # Perform various operations
    await cache.get("entity", ACCOUNT_1, 1, lambda: fetch(1))  # Miss
    await cache.get("entity", ACCOUNT_1, 1, lambda: fetch(1))  # Hit
    await cache.get("entity", ACCOUNT_1, 1, lambda: fetch(1))  # Hit
    await cache.get("entity", ACCOUNT_1, 2, lambda: fetch(2))  # Miss
    
    stats = cache.get_stats()
    assert stats['hits'] == 2
    assert stats['misses'] == 2
    assert stats['total_requests'] == 4
    assert stats['hit_rate_percent'] == 50.0
    assert stats['cache_size'] == 2


@pytest.mark.asyncio
async def test_cross_account_cache_entries_are_isolated():
    """Identical entity lookups for different accounts must not be shared."""
    cache = TelegramCache(task_id=1)

    call_count = 0

    async def fetch_shared():
        nonlocal call_count
        call_count += 1
        return {"id": 123, "value": "shared"}

    result1 = await cache.get("entity", ACCOUNT_1, 123, fetch_shared)
    result2 = await cache.get("entity", ACCOUNT_2, 123, fetch_shared)

    assert call_count == 2  # Each account fetched independently
    assert result1 == result2
    assert len(cache._cache) == 2


@pytest.mark.asyncio
async def test_per_account_entry_limit_enforced():
    """Ensure per-account limits evict only entries for that account."""

    cache = TelegramCache(task_id=1, per_account_max_entries=2)

    async def fetch(value):
        return {"id": value}

    # Insert three keys for the same account -> first should be evicted
    for key in range(3):
        await cache.get("entity", ACCOUNT_1, key, lambda val=key: fetch(val))

    assert cache._account_entry_counts[ACCOUNT_1] == 2
    remaining_keys = {
        int(k[1])
        for k, entry in cache._cache.items()
        if entry.owner_account == ACCOUNT_1
    }
    assert remaining_keys == {1, 2}

    # Insert entry for another account - should not evict ACCOUNT_1 entries
    await cache.get("entity", ACCOUNT_2, 999, lambda: fetch(999))
    assert cache._account_entry_counts[ACCOUNT_2] == 1


@pytest.mark.asyncio
async def test_background_cleanup_removes_expired_entries():
    """Process-scoped cache should clean expired entries via background loop."""

    cache = TelegramCache(
        task_id=None,
        scope=TelegramCacheScope.PROCESS,
        enable_background_cleanup=True,
        cleanup_interval=0,
    )

    async def fetch():
        return {"id": 1}

    await cache.get("entity", ACCOUNT_1, 1, fetch, ttl=0.05)
    assert len(cache._cache) == 1

    await asyncio.sleep(0.1)
    await asyncio.sleep(0)  # allow cleanup loop to run
    assert len(cache._cache) == 0

    await cache.shutdown()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
