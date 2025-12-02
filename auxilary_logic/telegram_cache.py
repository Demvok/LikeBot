"""
Telegram Cache Module

Task-scoped, thread-safe cache for Telegram API objects with per-account isolation.

Features:
- Per-account cache isolation (entities from one account cannot be used by another)
- Thread-safe concurrent access with asyncio.Lock
- In-flight request de-duplication (prevents redundant API calls)
- Auto-expiring entries with configurable TTL per object type
- LRU eviction when max size exceeded
- Integrated rate limiting on cache misses
- Support for multiple object types (entities, messages, channels)

Usage:
    # In task.py _run():
    cache = TelegramCache(task_id=self.task_id)
    
    # Inject into clients:
    for client in clients:
        client.telegram_cache = cache
    
    # In client methods (cache uses client.phone_number for isolation):
    entity = await self.telegram_cache.get_entity(identifier, self.client)
"""

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional, Callable, Dict, Tuple, TYPE_CHECKING

from utils.logger import setup_logger, load_config
from auxilary_logic.humaniser import rate_limiter

# Forward references to avoid circular imports
if TYPE_CHECKING:
    from main_logic.agent import Client


@dataclass
class CacheEntry:
    """Single cache entry with metadata."""
    value: Any
    timestamp: float
    ttl: float
    cache_type: str
    key: str
    
    def is_expired(self) -> bool:
        """Check if entry has expired based on TTL."""
        return time.time() - self.timestamp > self.ttl


@dataclass
class InFlightRequest:
    """Tracks ongoing API requests to prevent duplicate calls."""
    future: asyncio.Future
    started_at: float
    waiters: int = 0


class TelegramCache:
    """
    Task-scoped cache for Telegram API objects with per-account isolation.
    
    Features:
    - Per-account cache isolation (entities from one account cannot be used by another)
    - Thread-safe concurrent access with asyncio.Lock
    - In-flight request de-duplication (prevents redundant API calls)
    - Auto-expiring entries with configurable TTL per object type
    - LRU eviction when max size exceeded
    - Integrated rate limiting on cache misses
    - Support for multiple object types (entities, messages, channels)
    
    Usage:
        # In task.py _run():
        cache = TelegramCache(task_id=self.task_id)
        
        # Inject into clients:
        for client in clients:
            client.telegram_cache = cache
        
        # In client methods (cache uses client.phone_number for isolation):
        entity = await self.telegram_cache.get_entity(identifier, self.client)
    """
    
    # Cache type constants
    ENTITY = "entity"
    MESSAGE = "message"
    FULL_CHANNEL = "full_channel"
    DISCUSSION = "discussion"
    INPUT_PEER = "input_peer"
    
    def __init__(self, task_id: int = None, max_size: int = None):
        """
        Initialize task-scoped cache.
        
        Args:
            task_id: Optional task ID for logging context
            max_size: Maximum cache entries (default from config)
        """
        self.task_id = task_id
        self.logger = setup_logger(f"TelegramCache_Task{task_id}", "main.log")
        
        # Load configuration
        config = load_config()
        cache_config = config.get('cache', {})
        
        # Cache storage: {(cache_type, account_id, key): CacheEntry}
        # account_id ensures entities are cached per-account (session-specific)
        self._cache: OrderedDict[Tuple[str, str, str], CacheEntry] = OrderedDict()
        
        # In-flight requests: {(cache_type, account_id, key): InFlightRequest}
        self._in_flight: Dict[Tuple[str, str, str], InFlightRequest] = {}
        
        # Thread safety
        self._lock = asyncio.Lock()
        
        # Configuration
        self._max_size = max_size or cache_config.get('max_size', 500)
        self._enable_dedup = cache_config.get('enable_in_flight_dedup', True)
        
        # TTL per cache type (seconds)
        self._ttls = {
            self.ENTITY: cache_config.get('entity_ttl', 300),
            self.MESSAGE: cache_config.get('message_ttl', 60),
            self.FULL_CHANNEL: cache_config.get('full_channel_ttl', 600),
            self.DISCUSSION: cache_config.get('discussion_ttl', 300),
            self.INPUT_PEER: cache_config.get('input_peer_ttl', 300),
        }
        
        # Statistics
        self._stats = {
            'hits': 0,
            'misses': 0,
            'dedup_saves': 0,  # Times we avoided duplicate API calls
            'evictions': 0,
        }
        
        self.logger.info(f"TelegramCache initialized: max_size={self._max_size}, dedup={self._enable_dedup}")

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
    
    def _normalize_key(self, cache_type: str, account_id: str, key: Any) -> Tuple[str, str, str]:
        """
        Normalize cache key to consistent format with account isolation.
        
        Args:
            cache_type: Cache type constant
            account_id: Account identifier (phone_number) to isolate cache per account
            key: Raw key (int, str, tuple, etc.)
        
        Returns:
            Tuple of (cache_type, account_id, normalized_key_string)
            
        Examples:
            _normalize_key(ENTITY, "+1234567890", 12345) -> ("entity", "+1234567890", "12345")
            _normalize_key(ENTITY, "+1234567890", "@username") -> ("entity", "+1234567890", "username")
            _normalize_key(MESSAGE, "+1234567890", (12345, 678)) -> ("message", "+1234567890", "12345:678")
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
        
        return (cache_type, account_id, normalized)
    
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
        cache_key = self._normalize_key(cache_type, account_id, key)
        ttl = ttl or self._ttls.get(cache_type, 300)
        
        # Fast path: Check cache without lock (read-mostly optimization)
        entry = self._cache.get(cache_key)
        if entry and not entry.is_expired():
            async with self._lock:  # Brief lock to update stats
                self._stats['hits'] += 1
                self.logger.debug(f"Cache HIT: {cache_type}:{account_id}:{key}")
            return entry.value
        
        # Slow path: Need to fetch (acquire lock for coordination)
        async with self._lock:
            # Double-check after acquiring lock (another worker may have fetched)
            entry = self._cache.get(cache_key)
            if entry and not entry.is_expired():
                self._stats['hits'] += 1
                self.logger.debug(f"Cache HIT (after lock): {cache_type}:{account_id}:{key}")
                return entry.value
            
            # Check if request already in-flight
            if self._enable_dedup and cache_key in self._in_flight:
                in_flight = self._in_flight[cache_key]
                in_flight.waiters += 1
                self._stats['dedup_saves'] += 1
                self.logger.debug(f"In-flight WAIT: {cache_type}:{account_id}:{key} ({in_flight.waiters} waiters)")
                
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
                self.logger.debug(f"Cache MISS: {cache_type}:{account_id}:{key} (fetching)")
                self._stats['misses'] += 1
                future = None  # Signal that we need to fetch        # If we're waiting on another request
        if future is not None:
            try:
                result = await future
                return result
            except Exception as e:
                self.logger.warning(f"In-flight request failed: {cache_type}:{account_id}:{key}: {e}")
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
                    key=str(key)
                )
                
                # LRU: Move to end (most recently used)
                if cache_key in self._cache:
                    del self._cache[cache_key]
                self._cache[cache_key] = entry
                
                # Evict if over size limit (remove oldest = first item)
                while len(self._cache) > self._max_size:
                    evicted_key, _ = self._cache.popitem(last=False)
                    self._stats['evictions'] += 1
                    self.logger.debug(f"Cache EVICT: {evicted_key}")
                
                # Complete in-flight future
                if cache_key in self._in_flight:
                    in_flight = self._in_flight[cache_key]
                    in_flight.future.set_result(value)
                    del self._in_flight[cache_key]
                    if in_flight.waiters > 0:
                        self.logger.debug(f"In-flight COMPLETE: {cache_type}:{account_id}:{key} ({in_flight.waiters} waiters notified)")
            
            return value
            
        except Exception as e:
            # Propagate exception to all waiters
            async with self._lock:
                if cache_key in self._in_flight:
                    in_flight = self._in_flight[cache_key]
                    if not in_flight.future.done():
                        in_flight.future.set_exception(e)
                    del self._in_flight[cache_key]
            
            self.logger.error(f"Fetch failed: {cache_type}:{account_id}:{key}: {e}")
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
            chat_id: Chat/channel ID
            message_id: Message ID
            client: Telethon client instance
        
        Returns:
            Telethon message object
        """
        return await self.get(
            cache_type=self.MESSAGE,
            account_id=client.phone_number,
            key=(chat_id, message_id),
            fetch_func=lambda: client.client.get_messages(chat_id, ids=message_id),
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
        cache_key = self._normalize_key(cache_type, account_id, key)
        
        async with self._lock:
            if cache_key in self._cache:
                del self._cache[cache_key]
                self.logger.debug(f"Cache INVALIDATE: {cache_type}:{account_id}:{key}")
                return True
            return False
    
    async def clear(self):
        """Clear entire cache (typically called when task ends)."""
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._in_flight.clear()
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
            'in_flight': len(self._in_flight)
        }
