"""
Proxy configuration mixin for Telegram client.

Handles proxy selection, load balancing, and usage tracking.
"""


class ProxyMixin:
    """Handles proxy configuration and fallback logic."""
    
    async def _get_proxy_config(self):
        """
        Get proxy configuration for this connection.
        Selects the least-used active proxy for load balancing.
        Returns a tuple (proxy_candidates, proxy_data) where:
        - proxy_candidates is a list of proxy dicts to try (ordered by preference)
        - proxy_data is the raw proxy data from database
        """
        from auxilary_logic.proxy import get_proxy_config
        
        candidates, proxy_data = await get_proxy_config(self.phone_number, self.logger)
        
        if proxy_data:
            # Store proxy name for usage tracking (not a permanent assignment)
            self.proxy_name = proxy_data.get('proxy_name')
        
        return candidates, proxy_data
    
    async def _increment_proxy_usage(self):
        """Track proxy usage for load balancing."""
        if self.proxy_name:
            from main_logic.database import get_db
            db = get_db()
            await db.increment_proxy_usage(self.proxy_name)
            self.logger.debug(f"Incremented usage counter for proxy {self.proxy_name}")
    
    async def _decrement_proxy_usage(self):
        """Release proxy usage counter."""
        if self.proxy_name:
            from main_logic.database import get_db
            db = get_db()
            await db.decrement_proxy_usage(self.proxy_name)
            self.logger.debug(f"Decremented usage counter for proxy {self.proxy_name}")
            # Clear proxy name after releasing
            self.proxy_name = None
