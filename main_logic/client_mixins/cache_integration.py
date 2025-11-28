"""
Cache integration mixin for Telegram client.

Provides standalone cache initialization and cached message fetching for debugging and testing purposes.
"""

from auxilary_logic.humaniser import rate_limiter


class CacheIntegrationMixin:
    """Provides standalone cache initialization and cached API methods."""
    
    async def get_message_cached(self, chat_id: int, message_id: int):
        """
        Get message with task-scoped caching.
        
        This is the preferred method for fetching messages in production code.
        It uses the task-scoped TelegramCache when available (injected by Task._run()),
        falling back to direct API calls for debugging/testing scenarios.
        
        Args:
            chat_id: Telegram chat/channel ID
            message_id: Message ID to fetch
            
        Returns:
            Telethon message object
            
        Raises:
            RuntimeError: If telegram_cache not injected and no fallback possible
            Exception: Any Telegram API errors during message fetch
            
        Usage:
            # In production (within Task context):
            message = await client.get_message_cached(chat_id, message_id)
            
            # For debugging (outside Task context):
            client.init_standalone_cache()
            message = await client.get_message_cached(chat_id, message_id)
        """
        if self.telegram_cache is None:
            # Fallback for debugging/testing outside Task context
            self.logger.warning(
                f"get_message_cached called without telegram_cache for chat_id={chat_id}, "
                f"message_id={message_id}. Using direct API call (no caching). "
                f"For debugging, call client.init_standalone_cache() first."
            )
            # Fetch entity (with caching if available from get_entity_cached)
            entity = await self.get_entity_cached(chat_id)
            # Apply rate limiting and fetch message directly
            await rate_limiter.wait_if_needed('get_messages')
            return await self.client.get_messages(entity, ids=message_id)
        
        # Use task-scoped cache (production path)
        return await self.telegram_cache.get_message(chat_id, message_id, self)
    
    def init_standalone_cache(self, max_size: int = 500):
        """
        Initialize a standalone cache for debugging/testing purposes.
        
        This allows using Client outside of a Task context by creating
        a dedicated TelegramCache instance for this client only.
        
        WARNING: This is intended for debugging, testing, and scripts only.
        In production task execution, the cache is injected by Task._run()
        and shared across all workers for better performance.
        
        Args:
            max_size: Maximum cache entries (default 500)
            
        Usage:
            client = Client(account)
            await client.connect()
            client.init_standalone_cache()
            entity = await client.get_entity_cached(chat_id)  # Now works!
        """
        from auxilary_logic.telegram_cache import TelegramCache
        
        if self.telegram_cache is not None:
            self.logger.warning("Overwriting existing telegram_cache with standalone cache")
        
        self.telegram_cache = TelegramCache(task_id=None, max_size=max_size)
        self.logger.info(f"Initialized standalone cache (max_size={max_size}) for debugging")
