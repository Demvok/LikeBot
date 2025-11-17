"""
Humanisation utilities for Telegram interactions.
Includes rate limiting and reading time estimation to simulate human behavior.
"""

import asyncio
import time


class TelegramAPIRateLimiter:
    """
    Rate limiter for Telegram API calls to prevent flood errors.
    Tracks calls per method and enforces delays between calls.
    """
    def __init__(self):
        # Track last call time for each method
        self._last_call = {}
        # Minimum delay between calls (in seconds)
        self._min_delay = {
            'get_entity': 0.5,      # 500ms between entity lookups
            'get_messages': 0.3,    # 300ms between message fetches
            'send_reaction': 0.5,   # 500ms between reactions
            'send_message': 0.5,    # 500ms between messages
            'default': 0.2          # 200ms for other calls
        }
        self._lock = asyncio.Lock()
    
    async def wait_if_needed(self, method_name: str):
        """Wait if needed to respect rate limits."""
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
    from scipy.stats import skewnorm
    from numpy import arange, random as rnd
    
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
