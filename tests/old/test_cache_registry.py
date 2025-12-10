import pytest

from auxilary_logic.cache_registry import TelegramCacheRegistry
from auxilary_logic.telegram_cache import TelegramCacheScope


@pytest.mark.asyncio
async def test_registry_task_scope_creates_fresh_caches(monkeypatch):
    monkeypatch.setattr(
        "auxilary_logic.cache_registry.load_config",
        lambda: {"cache": {"scope": "task", "max_size": 10}},
    )

    registry = TelegramCacheRegistry()

    cache_a = registry.get_cache(task_id=1)
    cache_b = registry.get_cache(task_id=2)

    assert cache_a is not cache_b
    assert cache_a.scope == TelegramCacheScope.TASK
    assert cache_b.scope == TelegramCacheScope.TASK

    await registry.release_cache(cache_a)
    await registry.release_cache(cache_b)


@pytest.mark.asyncio
async def test_registry_process_scope_reuses_cache(monkeypatch):
    monkeypatch.setattr(
        "auxilary_logic.cache_registry.load_config",
        lambda: {
            "cache": {
                "scope": "process",
                "max_size": 5,
                "process": {"max_size": 8, "cleanup_interval": 0},
                "per_account": {"max_entries": 3},
            }
        },
    )

    registry = TelegramCacheRegistry()
    cache_a = registry.get_cache(task_id=1)
    cache_b = registry.get_cache(task_id=2)

    assert cache_a is cache_b
    assert cache_a.scope == TelegramCacheScope.PROCESS
    assert registry.warm_start() is False

    async def fetch():
        return {"id": 1}

    await cache_a.get("entity", "+1000", 1, fetch)
    assert registry.warm_start() is True

    # release_cache should be a no-op for process scope
    await registry.release_cache(cache_a)
    await registry.shutdown()
