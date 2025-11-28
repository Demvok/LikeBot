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


async def apply_reading_delay(message_content: str = None, logger=None):
    """
    Apply reading time delay based on message content.
    
    Simulates human reading behavior with either content-based estimation
    or fallback random delay.
    
    Args:
        message_content: Message text to estimate reading time for
        logger: Optional logger instance for debug output
    """
    from utils.retry import random_delay
    from utils.logger import load_config
    
    config = load_config()
    humanisation_level = config.get('delays', {}).get('humanisation_level', 1)
    
    if humanisation_level >= 1 and message_content:
        reading_time = estimate_reading_time(message_content)
        if logger:
            logger.debug(f"Estimated reading time: {reading_time}s")
        await asyncio.sleep(reading_time)
    else:
        # Fallback delay
        await random_delay(
            'reading_fallback_delay_min', 
            'reading_fallback_delay_max',
            logger, 
            "Message content empty, using fallback delay"
        )


async def apply_pre_action_delay(logger=None):
    """
    Apply random delay before action (reaction/comment).
    
    Adds unpredictability to prevent detection of automated behavior.
    
    Args:
        logger: Optional logger instance for debug output
    """
    import random
    from utils.logger import load_config
    
    config = load_config()
    min_delay = config.get('delays', {}).get('min_delay_before_reaction', 1)
    max_delay = config.get('delays', {}).get('max_delay_before_reaction', 3)
    delay = random.uniform(min_delay, max_delay)
    
    if logger:
        logger.debug(f"Pre-action delay: {delay:.2f}s")
    
    await asyncio.sleep(delay)


async def apply_anti_spam_delay(logger=None):
    """
    Apply anti-spam delay between actions.
    
    Prevents rapid-fire actions that could trigger spam detection.
    
    Args:
        logger: Optional logger instance for debug output
    """
    from utils.retry import random_delay
    
    await random_delay(
        'anti_spam_delay_min', 
        'anti_spam_delay_max',
        logger, 
        "Anti-spam delay"
    )
