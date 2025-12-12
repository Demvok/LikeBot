"""Telegram cache primitives with optional task/process scope management."""

from __future__ import annotations

import asyncio
import time
from collections import Counter, OrderedDict
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Callable, Dict, Tuple, TYPE_CHECKING

from utils.logger import setup_logger, load_config
from auxilary_logic.humaniser import rate_limiter

# Forward references to avoid circular imports
if TYPE_CHECKING:
    from main_logic.agent import Client

__all__ = [
    "CacheEntry",
    "InFlightRequest",
    "TelegramCache",
    "TelegramCacheScope",
]


class TelegramCacheScope(str, Enum):
    """Runtime scope for cache lifetime management."""

    TASK = "task"
    PROCESS = "process"

@dataclass
class CacheEntry:
    """Single cache entry with metadata."""
    value: Any
    timestamp: float
    ttl: float | None
    cache_type: str
    key: str
    owner_account: str | None = None
    
    def is_expired(self) -> bool:
        """Check if entry has expired based on TTL."""
        if self.ttl is None or self.ttl <= 0:
            return False
        return time.time() - self.timestamp > self.ttl


@dataclass
class InFlightRequest:
    """Tracks ongoing API requests to prevent duplicate calls."""
    future: asyncio.Future
    started_at: float
    waiters: int = 0


class TelegramCache:
    """
    Task- or process-scoped cache for Telegram API objects with per-account isolation.
    """
    
    # Cache type constants
    ENTITY = "entity"
    MESSAGE = "message"
    FULL_CHANNEL = "full_channel"
    DISCUSSION = "discussion"
    INPUT_PEER = "input_peer"
    
    def __init__(
        self,
        task_id: int | None = None,
        max_size: int | None = None,
        *,
        scope: TelegramCacheScope = TelegramCacheScope.TASK,
        per_account_max_entries: int | None = None,
        enable_background_cleanup: bool = False,
        cleanup_interval: int = 60,
    ):
        """Initialize cache instance for a specific scope."""

        self.scope = scope
        self.task_id = task_id
        scope_suffix = scope.value if scope else "task"
        if scope == TelegramCacheScope.TASK:
            label = f"cache_{scope_suffix}_{task_id if task_id else None}"
        else:
            label = "cache"
        self.logger = setup_logger(label, "main.log")

        config = load_config()
        cache_config = config.get('cache', {})

        # Cache storage: {(cache_type, normalized_key, account_id): CacheEntry}
        self._cache: OrderedDict[Tuple[str, str, str], CacheEntry] = OrderedDict()
        self._account_entry_counts: Counter[str] = Counter()
        self._in_flight: Dict[Tuple[str, str, str], InFlightRequest] = {}
        self._lock = asyncio.Lock()

        self._max_size = max_size or cache_config.get('max_size', 500)
        self._enable_dedup = cache_config.get('enable_in_flight_dedup', True)
        self._per_account_max_entries = per_account_max_entries
        self._cleanup_interval = cleanup_interval
        self._background_cleanup_enabled = enable_background_cleanup and scope == TelegramCacheScope.PROCESS
        self._cleanup_task: asyncio.Task | None = None
        self._shutdown = False

        self._ttls = {
            self.ENTITY: cache_config.get('entity_ttl', 300),
            self.MESSAGE: cache_config.get('message_ttl', 60),
            self.FULL_CHANNEL: cache_config.get('full_channel_ttl', 600),
            self.DISCUSSION: cache_config.get('discussion_ttl', 300),
            self.INPUT_PEER: cache_config.get('input_peer_ttl', 300),
        }
        self._refresh_ttl_on_hit = cache_config.get('refresh_ttl_on_hit', True)

        self._stats = {
            'hits': 0,
            'misses': 0,
            'dedup_saves': 0,
            'evictions': 0,
            'expired': 0,
        }

        if self._background_cleanup_enabled:
            self._start_cleanup_task()

        self.logger.info(
            "TelegramCache initialized scope=%s max_size=%s per_account_max=%s dedup=%s",
            self.scope.value,
            self._max_size,
            self._per_account_max_entries,
            self._enable_dedup,
        )

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def _start_cleanup_task(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.logger.warning("Cleanup task not started: no running event loop")
            return

        if self._cleanup_task and not self._cleanup_task.done():
            return

        self._cleanup_task = loop.create_task(self._cleanup_loop())
        self.logger.debug("Started cache cleanup task (interval=%ss)", self._cleanup_interval)

    async def _cleanup_loop(self) -> None:
        try:
            while not self._shutdown:
                await asyncio.sleep(self._cleanup_interval)
                removed = await self._remove_expired_entries()
                if removed:
                    self.logger.debug("Cleanup removed %s expired entries", removed)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.warning("Cleanup loop error: %s", exc)

    async def shutdown(self, *, clear_cache: bool = True) -> None:
        """Stop cleanup tasks and optionally clear entries."""

        self._shutdown = True
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None

        if clear_cache:
            await self.clear()

    def _consume_future_result(self, future: asyncio.Future) -> None:
        """Ensure future result/exception is retrieved to avoid unhandled warnings."""
        if future.cancelled():
            return
        try:
            future.result()
        except Exception as exc:
            # Exception is already propagated elsewhere; keep logs at debug level to avoid spam
            try:
                self.logger.debug(f"In-flight future exception consumed: {exc}")
            except Exception:
                pass
    
    def _normalize_key(self, cache_type: str, key: Any) -> Tuple[str, str]:
        """
        Normalize cache key to consistent format.
        
        Args:
            cache_type: Cache type constant
            key: Raw key (int, str, tuple, etc.)
        
        Returns:
            Tuple of (cache_type, normalized_key_string)
            
        Examples:
            _normalize_key(ENTITY, 12345) -> ("entity", "12345")
            _normalize_key(ENTITY, "@username") -> ("entity", "username")
            _normalize_key(MESSAGE, (12345, 678)) -> ("message", "12345:678")
        """
        if isinstance(key, int):
            normalized = str(key)
        elif isinstance(key, str):
            # Remove @ prefix for usernames
            normalized = key.lstrip('@').lower()
        elif isinstance(key, tuple):
            # For composite keys like (chat_id, message_id)
            normalized = ':'.join(str(k) for k in key)
        else:
            normalized = str(key)
        
        return (cache_type, normalized)

    def _build_cache_key(self, cache_type: str, key: Any, account_id: str | None) -> Tuple[str, str, str]:
        """Build the storage key that enforces per-account isolation."""
        cache_type_norm, normalized = self._normalize_key(cache_type, key)
        owner = account_id or ""
        return (cache_type_norm, normalized, owner)

    def _resolve_ttl(self, cache_type: str, override_ttl: float | None) -> float | None:
        """Return TTL override when provided, else fall back to configured defaults."""
        if override_ttl is not None:
            return override_ttl
        return self._ttls.get(cache_type, 300)

    def _track_addition(self, account_id: str) -> None:
        if account_id:
            self._account_entry_counts[account_id] += 1

    def _track_removal_for_entry(self, entry: CacheEntry | None) -> None:
        if entry is None:
            return
        account_id = entry.owner_account
        if not account_id:
            return
        current = self._account_entry_counts.get(account_id)
        if not current:
            return
        if current <= 1:
            self._account_entry_counts.pop(account_id, None)
        else:
            self._account_entry_counts[account_id] = current - 1

    def _after_entry_removed(self, cache_key: Tuple[str, str, str], entry: CacheEntry | None, *, expired: bool = False, evicted: bool = False) -> None:
        self._track_removal_for_entry(entry)
        if expired:
            self._stats['expired'] += 1
        if evicted:
            self._stats['evictions'] += 1

    def _register_hit_locked(self, cache_key: Tuple[str, str, str], entry: CacheEntry) -> None:
        """Track hit stats and optionally extend TTL/LRU ordering for active entries."""
        if self._refresh_ttl_on_hit:
            entry.timestamp = time.time()
        self._cache.move_to_end(cache_key, last=True)
        self._stats['hits'] += 1

    def _evict_oldest_entry(self) -> None:
        if not self._cache:
            return
        evicted_key, entry = self._cache.popitem(last=False)
        self._after_entry_removed(evicted_key, entry, evicted=True)
        self.logger.debug("Cache EVICT (global LRU): %s", evicted_key)

    def _evict_oldest_for_account(self, account_id: str) -> bool:
        for key, entry in list(self._cache.items()):
            if entry.owner_account == account_id:
                removed = self._cache.pop(key, None)
                self._after_entry_removed(key, removed, evicted=True)
                self.logger.debug("Cache EVICT (per-account %s): %s", account_id, key)
                return True
        return False

    def _ensure_account_capacity(self, account_id: str) -> None:
        if self._per_account_max_entries is None:
            return
        while self._account_entry_counts.get(account_id, 0) >= self._per_account_max_entries:
            if not self._evict_oldest_for_account(account_id):
                break

    async def _remove_expired_entries(self) -> int:
        async with self._lock:
            expired_keys = [key for key, entry in self._cache.items() if entry.is_expired()]
            for key in expired_keys:
                entry = self._cache.pop(key, None)
                self._after_entry_removed(key, entry, expired=True)
            return len(expired_keys)

    def is_warm(self) -> bool:
        """Return True if cache already contains entries."""

        return len(self._cache) > 0
    
    async def get(
        self, 
        cache_type: str, 
        account_id: str,
        key: Any, 
        fetch_func: Callable, 
        ttl: Optional[float] = None,
        rate_limit_method: Optional[str] = None
    ) -> Any:
        """
        Get cached value or fetch if expired/missing.
        Handles in-flight request de-duplication automatically.
        
        Args:
            cache_type: Type of cached object (use class constants)
            account_id: Account identifier (phone_number) for cache isolation
            key: Cache key (will be normalized)
            fetch_func: Async function to call on cache miss (no args)
            ttl: Optional TTL override (uses default for cache_type if None)
            rate_limit_method: Optional rate limiter method name ('get_entity', etc.)
        
        Returns:
            Cached or freshly fetched value
            
        Raises:
            Exception: Re-raises exceptions from fetch_func
            
        Example:
            entity = await cache.get(
                cache_type=TelegramCache.ENTITY,
                account_id=client.phone_number,
                key=chat_id,
                fetch_func=lambda: client.get_entity(chat_id),
                rate_limit_method='get_entity'
            )
        """
        cache_key = self._build_cache_key(cache_type, key, account_id)
        ttl = self._resolve_ttl(cache_type, ttl)
        
        # Fast path: Check cache without awaiting fetch
        entry = self._cache.get(cache_key)
        if entry:
            async with self._lock:
                locked_entry = self._cache.get(cache_key)
                if locked_entry is not None:
                    if locked_entry.is_expired():
                        expired_entry = self._cache.pop(cache_key, None)
                        self._after_entry_removed(cache_key, expired_entry, expired=True)
                    else:
                        self._register_hit_locked(cache_key, locked_entry)
                        self.logger.debug("Cache HIT: %s:%s (account=%s)", cache_type, key, account_id)
                        return locked_entry.value
        
        # Slow path: Need to fetch (acquire lock for coordination)
        async with self._lock:
            # Double-check after acquiring lock (another worker may have fetched)
            entry = self._cache.get(cache_key)
            if entry:
                if entry.is_expired():
                    expired_entry = self._cache.pop(cache_key, None)
                    self._after_entry_removed(cache_key, expired_entry, expired=True)
                else:
                    self._register_hit_locked(cache_key, entry)
                    self.logger.debug("Cache HIT (after lock): %s:%s (account=%s)", cache_type, key, account_id)
                    return entry.value
            
            # Check if request already in-flight
            if self._enable_dedup and cache_key in self._in_flight:
                in_flight = self._in_flight[cache_key]
                in_flight.waiters += 1
                self._stats['dedup_saves'] += 1
                self.logger.debug(
                    "In-flight WAIT: %s:%s (account=%s, waiters=%s)",
                    cache_type,
                    key,
                    account_id,
                    in_flight.waiters,
                )
                
                # Release lock and wait for in-flight request to complete
                # CRITICAL: Must release lock or we deadlock the fetcher
                future = in_flight.future
            else:
                # We're the first - create in-flight tracker
                future = asyncio.Future()
                future.add_done_callback(self._consume_future_result)
                self._in_flight[cache_key] = InFlightRequest(
                    future=future,
                    started_at=time.time(),
                    waiters=0
                )
                self.logger.debug("Cache MISS: %s:%s (account=%s, fetching)", cache_type, key, account_id)
                self._stats['misses'] += 1
                future = None  # Signal that we need to fetch
        if future is not None:
            try:
                result = await future
                return result
            except Exception as e:
                self.logger.warning("In-flight request failed: %s:%s (account=%s): %s", cache_type, key, account_id, e)
                raise
        
        # We're responsible for fetching
        try:
            # Apply rate limiting BEFORE API call (outside lock)
            if rate_limit_method:
                await rate_limiter.wait_if_needed(rate_limit_method)
            
            # Call fetch function
            value = await fetch_func()
            
            # Store in cache and complete in-flight future
            async with self._lock:
                # Create cache entry
                entry = CacheEntry(
                    value=value,
                    timestamp=time.time(),
                    ttl=ttl,
                    cache_type=cache_type,
                    key=str(key),
                    owner_account=account_id
                )
                
                existing_entry = self._cache.pop(cache_key, None)
                if existing_entry is not None:
                    self._track_removal_for_entry(existing_entry)
                needs_capacity = existing_entry is None or existing_entry.owner_account != account_id
                if needs_capacity:
                    self._ensure_account_capacity(account_id)
                self._cache[cache_key] = entry
                self._track_addition(account_id)

                while len(self._cache) > self._max_size:
                    self._evict_oldest_entry()
                
                # Complete in-flight future
                if cache_key in self._in_flight:
                    in_flight = self._in_flight[cache_key]
                    in_flight.future.set_result(value)
                    del self._in_flight[cache_key]
                    if in_flight.waiters > 0:
                        self.logger.debug(
                            "In-flight COMPLETE: %s:%s (waiters=%s)",
                            cache_type,
                            key,
                            in_flight.waiters,
                        )
            
            return value
            
        except Exception as e:
            # Propagate exception to all waiters
            async with self._lock:
                if cache_key in self._in_flight:
                    in_flight = self._in_flight[cache_key]
                    if not in_flight.future.done():
                        in_flight.future.set_exception(e)
                    del self._in_flight[cache_key]
            
            self.logger.error("Fetch failed: %s:%s (account=%s): %s", cache_type, key, account_id, e)
            raise
    
    async def get_entity(self, identifier: Any, client: 'Client') -> Any:
        """
        Get entity with automatic rate limiting.
        Drop-in replacement for client.get_entity_cached().
        
        Args:
            identifier: Entity identifier (chat_id, username, etc.)
            client: Telethon client instance to use for fetching
        
        Returns:
            Telethon entity object
        """
        return await self.get(
            cache_type=self.ENTITY,
            account_id=client.phone_number,
            key=identifier,
            fetch_func=lambda: client.client.get_entity(identifier),
            rate_limit_method='get_entity'
        )
    
    async def get_message(self, chat_id: int, message_id: int, client: 'Client') -> Any:
        """
        Get message with caching.
        
        Args:
            chat_id: Chat/channel ID (int, str, entity or InputPeer)
            message_id: Message ID
            client: Telethon client instance
        
        Returns:
            Telethon message object
        """

        async def _resolve_entity(identifier: Any) -> Any:
            if identifier is None:
                raise ValueError("chat_id is required to fetch messages")
            if hasattr(identifier, 'id') or hasattr(identifier, 'channel_id'):
                return identifier
            return await client.get_entity_cached(identifier)

        async def _warm_input_peer(entity: Any) -> None:
            try:
                await self.get_input_peer(entity, client)
            except Exception as exc:  # pragma: no cover - defensive logging
                self.logger.debug("Input peer warmup skipped: %s", exc)

        async def _fetch_message() -> Any:
            last_error: Exception | None = None
            for attempt in range(2):
                entity = await _resolve_entity(chat_id)
                await _warm_input_peer(entity)
                try:
                    return await client.client.get_messages(entity, ids=message_id)
                except ValueError as exc:
                    if "PeerUser" not in str(exc):
                        raise
                    last_error = exc
                    self.logger.debug(
                        "Peer resolution failed for %s/%s (attempt %s): %s",
                        chat_id,
                        message_id,
                        attempt + 1,
                        exc,
                    )
                    if client.telegram_cache is not None:
                        entity_id = getattr(entity, 'id', chat_id)
                        await self.invalidate(self.INPUT_PEER, client.phone_number, entity_id)
                except Exception:
                    raise
            if last_error:
                raise last_error

        return await self.get(
            cache_type=self.MESSAGE,
            account_id=client.phone_number,
            key=(chat_id, message_id),
            fetch_func=_fetch_message,
            rate_limit_method='get_messages'
        )
    
    async def get_input_peer(self, entity: Any, client: 'Client') -> Any:
        """
        Get InputPeer for entity with caching.
        
        Args:
            entity: Entity object or identifier
            client: Telethon client instance
        
        Returns:
            InputPeer object
        """
        # Use entity ID as key if available
        entity_id = getattr(entity, 'id', entity)
        
        return await self.get(
            cache_type=self.INPUT_PEER,
            account_id=client.phone_number,
            key=entity_id,
            fetch_func=lambda: client.client.get_input_entity(entity),
            rate_limit_method='get_entity'  # Same rate limit as get_entity
        )
    
    async def get_full_channel(self, channel_id: int, client: 'Client') -> Any:
        """
        Get full channel info with caching.
        
        Args:
            channel_id: Channel/chat ID
            client: Telethon client instance
        
        Returns:
            FullChannel object with linked chat, reactions, etc.
        """
        from telethon import functions
        
        return await self.get(
            cache_type=self.FULL_CHANNEL,
            account_id=client.phone_number,
            key=channel_id,
            fetch_func=lambda: client.client(functions.channels.GetFullChannelRequest(channel=channel_id)),
            rate_limit_method='get_entity'
        )
    
    async def invalidate(self, cache_type: str, account_id: str, key: Any) -> bool:
        """
        Manually invalidate a cache entry.
        
        Args:
            cache_type: Type of cached object
            account_id: Account identifier for cache isolation
            key: Cache key to invalidate
        
        Returns:
            True if entry was removed, False if not found
        """
        cache_key = self._build_cache_key(cache_type, key, account_id)
        
        async with self._lock:
            if cache_key in self._cache:
                entry = self._cache.pop(cache_key, None)
                self._after_entry_removed(cache_key, entry)
                self.logger.debug("Cache INVALIDATE: %s:%s (account=%s)", cache_type, key, account_id)
                return True
            return False
    
    async def clear(self):
        """Clear entire cache (typically called when task ends)."""
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._in_flight.clear()
            self._account_entry_counts.clear()
            self.logger.info(f"Cache CLEARED: {count} entries removed")
    
    def get_stats(self) -> dict:
        """
        Get cache statistics.
        
        Returns:
            Dict with hits, misses, dedup_saves, evictions, hit_rate
        """
        total = self._stats['hits'] + self._stats['misses']
        hit_rate = (self._stats['hits'] / total * 100) if total > 0 else 0
        
        return {
            **self._stats,
            'total_requests': total,
            'hit_rate_percent': round(hit_rate, 2),
            'cache_size': len(self._cache),
            'in_flight': len(self._in_flight),
            'scope': self.scope.value,
            'per_account_limit': self._per_account_max_entries,
            'cleanup_interval': self._cleanup_interval if self._background_cleanup_enabled else None,
        }
