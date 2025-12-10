"""Central registry for task/process scoped Telegram cache instances."""

from __future__ import annotations

import asyncio
import atexit
from threading import Lock
from typing import Optional

from utils.logger import load_config, setup_logger
from auxilary_logic.telegram_cache import TelegramCache, TelegramCacheScope

__all__ = [
    "TelegramCacheRegistry",
    "get_cache_registry",
    "shutdown_cache_registry",
]


class TelegramCacheRegistry:
    """Manages lifecycle of TelegramCache instances for different scopes."""

    def __init__(self) -> None:
        cache_config = load_config().get("cache", {})
        scope_value = cache_config.get("scope", TelegramCacheScope.TASK.value)
        self.scope = TelegramCacheScope(scope_value)

        process_cfg = cache_config.get("process", {})
        per_account_cfg = cache_config.get("per_account", {})

        self._task_max_size = cache_config.get("max_size", 500)
        self._process_max_size = process_cfg.get("max_size", self._task_max_size)
        self._cleanup_interval = process_cfg.get("cleanup_interval", 60)
        self._per_account_limit = per_account_cfg.get("max_entries")

        self._logger = setup_logger("TelegramCacheRegistry", "main.log")
        self._lock = Lock()
        self._process_cache: Optional[TelegramCache] = None

    # ------------------------------------------------------------------
    # Cache accessors
    # ------------------------------------------------------------------

    def get_cache(self, task_id: int | None) -> TelegramCache:
        """Return cache instance according to configured scope."""

        with self._lock:
            if self.scope == TelegramCacheScope.PROCESS:
                if self._process_cache is None:
                    self._process_cache = TelegramCache(
                        task_id=None,
                        max_size=self._process_max_size,
                        scope=self.scope,
                        per_account_max_entries=self._per_account_limit,
                        enable_background_cleanup=True,
                        cleanup_interval=self._cleanup_interval,
                    )
                    self._logger.info(
                        "Created process-scoped Telegram cache (max_size=%s, per_account=%s)",
                        self._process_max_size,
                        self._per_account_limit,
                    )
                return self._process_cache

        # Task scope returns a fresh cache every call
        return TelegramCache(
            task_id=task_id,
            max_size=self._task_max_size,
            scope=TelegramCacheScope.TASK,
            per_account_max_entries=self._per_account_limit,
            enable_background_cleanup=False,
            cleanup_interval=self._cleanup_interval,
        )

    async def release_cache(self, cache: TelegramCache | None) -> None:
        """Release cache back to registry (clears when scope=TASK)."""

        if cache is None:
            return
        if self.scope == TelegramCacheScope.TASK:
            await cache.shutdown(clear_cache=True)

    async def shutdown(self) -> None:
        """Shutdown any long-lived caches (used at process exit)."""

        with self._lock:
            cache = self._process_cache
            self._process_cache = None

        if cache is not None:
            await cache.shutdown(clear_cache=True)
            self._logger.info("Process-scoped cache shut down")

    def warm_start(self) -> bool:
        """Whether the shared cache already contains entries."""

        cache = None
        with self._lock:
            cache = self._process_cache
        return bool(cache and cache.is_warm())


_registry: TelegramCacheRegistry | None = None


def get_cache_registry() -> TelegramCacheRegistry:
    global _registry
    if _registry is None:
        _registry = TelegramCacheRegistry()
    return _registry


def _sync_shutdown() -> None:
    global _registry
    if _registry is None:
        return
    registry, _registry = _registry, None
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(registry.shutdown())
            return
        loop.run_until_complete(registry.shutdown())
    except RuntimeError:
        asyncio.run(registry.shutdown())


def shutdown_cache_registry() -> None:
    """Public helper to synchronously tear down the registry."""

    _sync_shutdown()


atexit.register(_sync_shutdown)
