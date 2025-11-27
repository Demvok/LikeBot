"""
Humanisation utilities for Telegram interactions.
Includes rate limiting and reading time estimation to simulate human behavior.
"""

import asyncio
import time
from scipy.stats import skewnorm
from numpy import arange, random as rnd
from utils.retry import get_delay_config


class TelegramAPIRateLimiter:
    """
    Rate limiter for Telegram API calls to prevent flood errors.
    Tracks calls per method and enforces delays between calls.
    Uses config-based delays for consistency with rest of codebase.
    """
    def __init__(self):
        # Track last call time for each method
        self._last_call = {}
        # Load delays from config (lazy-loaded on first use)
        self._min_delay = None
        self._lock = asyncio.Lock()
    
    def _ensure_delays_loaded(self):
        """Lazy-load delay configuration from config.yaml."""
        if self._min_delay is None:
            self._min_delay = {
                'get_entity': get_delay_config('rate_limit_get_entity', 3.0),
                'get_messages': get_delay_config('rate_limit_get_messages', 0.3),
                'send_reaction': get_delay_config('rate_limit_send_reaction', 0.5),
                'send_message': get_delay_config('rate_limit_send_message', 0.5),
                'default': get_delay_config('rate_limit_default', 0.2)
            }
    
    async def wait_if_needed(self, method_name: str):
        """Wait if needed to respect rate limits."""
        self._ensure_delays_loaded()
        async with self._lock:
            now = time.time()
            delay = self._min_delay.get(method_name, self._min_delay['default'])
            
            if method_name in self._last_call:
                elapsed = now - self._last_call[method_name]
                if elapsed < delay:
                    wait_time = delay - elapsed
                    await asyncio.sleep(wait_time)
            
            self._last_call[method_name] = time.time()


# Global rate limiter instance
rate_limiter = TelegramAPIRateLimiter()


def estimate_reading_time(text: str, wpm=None) -> float:
    """
    Estimate the reading time for a given text in seconds. 
    It uses a statistical model to predict reading speed.
    
    Args:
        text: The text to estimate reading time for
        wpm: Words per minute (optional). If None, uses statistical distribution
        
    Returns:
        Estimated reading time in seconds
    """
    
    try:
        words = len(str(text).split())
        if wpm is None:
            wpm_list = arange(160, 301, dtype=int)
            wpm_distribution = skewnorm.pdf(wpm_list, loc=230, scale=30, a=0)
            wpm_distribution = wpm_distribution / wpm_distribution.max()
            probs = wpm_distribution / wpm_distribution.sum()
            wpm = rnd.choice(wpm_list, p=probs, size=1)[0]
        return round(float(words / wpm * 60), 3)
    except Exception as e:
        raise ValueError(f"Error estimating reading time: {e}")
