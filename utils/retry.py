"""
Centralized retry logic for async operations.

Provides a standardized async_retry decorator and helper functions
for consistent retry behavior across the codebase.

RETRY PATTERNS IN LIKEBOT:
===========================

1. DECORATOR PATTERN (@async_retry)
   - Best for: Simple functions that should retry on failure
   - Use case: Connection methods, API calls, simple operations
   - Example: Client.connect(), Client.disconnect()
   
2. CONTEXT MANAGER (RetryContext)
   - Best for: Manual retry control with custom logic between attempts
   - Use case: Complex operations needing conditional retry
   - Example: Client connection with session invalidation
   
3. WORKER RETRY CONTEXT (WorkerRetryContext)
   - Best for: Task workers processing multiple items with different outcomes
   - Use case: Task workers that need RETRY/SKIP/STOP logic per item
   - Example: Task.client_worker() processing posts with reactions
   
4. MANUAL LOOPS (for attempt in range(retries))
   - Best for: Immediate retries with NO delay (race conditions)
   - Use case: MongoDB ID allocation with DuplicateKeyError handling
   - Example: database.py add_post/add_task ID allocation
   - Note: Do NOT convert these to RetryContext (adds unnecessary delay)

CONFIGURATION:
==============
All retry behavior is configured in config.yaml under 'delays':
- action_retries: Number of retries for Telegram actions
- action_retry_delay: Delay between action retries (seconds)
- connection_retries: Number of connection retries
- reconnect_delay: Delay between reconnection attempts (seconds)
- error_retry_delay: Delay after transient errors
- rate_limit_*: API rate limiting delays

Usage Examples:
===============
    from utils.retry import async_retry, get_retry_config, get_delay_config
    
    # Using decorator with config-based defaults
    @async_retry()
    async def my_action():
        ...
    
    # Using decorator with custom settings
    @async_retry(
        max_retries=5,
        retry_delay=2.0,
        retry_exceptions=(ConnectionError, TimeoutError),
        no_retry_exceptions=(ValueError,)
    )
    async def my_connection():
        ...
    
    # Using config helpers
    retries = get_retry_config('action_retries')
    delay = get_delay_config('action_retry_delay')
    
    # Using RetryContext for manual control
    async with RetryContext(retries_key='connection_retries') as ctx:
        while ctx.should_retry():
            try:
                await connect()
                ctx.success()
            except ConnectionError as e:
                await ctx.failed(e)
    
    # Using WorkerRetryContext for complex workflows
    ctx = WorkerRetryContext(logger=logger)
    for item in items:
        ctx.reset_for_item()
        while ctx.should_retry():
            try:
                await process(item)
                ctx.success()
            except RetryableError as e:
                await ctx.retry(e, "Failed, retrying...")
            except SkipError as e:
                ctx.skip(e, "Skipping item")
            except FatalError as e:
                return ctx.stop(e, "Fatal error")
"""

import asyncio
import functools
import logging
from typing import Callable, Tuple, Type, Optional, Any, Union
from utils.logger import load_config

# Load config once at module level
_config = None


def _get_config():
    """Lazy-load config to avoid circular imports."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def get_retry_config(key: str, default: int = None) -> int:
    """
    Get retry count from config.
    
    Args:
        key: Config key under 'delays' section (e.g., 'action_retries', 'connection_retries')
        default: Default value if not found in config
        
    Returns:
        Retry count from config or default
    """
    config = _get_config()
    defaults = {
        'action_retries': 1,
        'connection_retries': 5,
        'session_creation_retries': 2,
        'entity_resolution_retries': 1,
    }
    if default is None:
        default = defaults.get(key, 1)
    return config.get('delays', {}).get(key, default)


def get_delay_config(key: str, default: float = None) -> float:
    """
    Get delay value from config.
    
    Args:
        key: Config key under 'delays' section
        default: Default value if not found in config
        
    Returns:
        Delay value from config or default
    """
    config = _get_config()
    defaults = {
        'action_retry_delay': 2.0,
        'reconnect_delay': 3.0,
        'error_retry_delay': 5.0,
        'entity_resolution_retry_delay': 30.0,
        'anti_spam_delay_min': 0.5,
        'anti_spam_delay_max': 2.0,
        'min_delay_between_reactions': 3.0,
        'max_delay_between_reactions': 8.0,
        'min_delay_before_reaction': 1.0,
        'max_delay_before_reaction': 3.0,
        'worker_start_delay_min': 2.0,
        'worker_start_delay_max': 10.0,
        'batch_error_delay': 0.2,
        'reading_fallback_delay_min': 2.0,
        'reading_fallback_delay_max': 5.0,
        'minimal_humanization_delay_min': 1.5,
        'minimal_humanization_delay_max': 4.0,
    }
    if default is None:
        default = defaults.get(key, 2.0)
    return float(config.get('delays', {}).get(key, default))


def get_delay_range(min_key: str, max_key: str) -> Tuple[float, float]:
    """
    Get a delay range (min, max) from config.
    
    Args:
        min_key: Config key for minimum delay
        max_key: Config key for maximum delay
        
    Returns:
        Tuple of (min_delay, max_delay)
    """
    return (get_delay_config(min_key), get_delay_config(max_key))


async def random_delay(min_key: str, max_key: str, logger: logging.Logger = None, reason: str = None):
    """
    Sleep for a random duration between configured min and max.
    
    Args:
        min_key: Config key for minimum delay
        max_key: Config key for maximum delay
        logger: Optional logger for debug output
        reason: Optional reason string for logging
    """
    import random
    min_delay, max_delay = get_delay_range(min_key, max_key)
    delay = random.uniform(min_delay, max_delay)
    if logger and reason:
        logger.debug(f"{reason}: {delay:.2f}s")
    await asyncio.sleep(delay)


def async_retry(
    max_retries: int = None,
    retry_delay: float = None,
    retry_exceptions: Tuple[Type[Exception], ...] = (Exception,),
    no_retry_exceptions: Tuple[Type[Exception], ...] = (),
    delay_key: str = 'action_retry_delay',
    retries_key: str = 'action_retries',
    exponential_backoff: bool = False,
    backoff_multiplier: float = 2.0,
    max_delay: float = 60.0,
    on_retry: Callable[[int, Exception, Any], None] = None,
    logger_attr: str = 'logger',
):
    """
    Async retry decorator with configurable behavior.
    
    Args:
        max_retries: Maximum retry attempts. If None, reads from config using retries_key.
        retry_delay: Delay between retries. If None, reads from config using delay_key.
        retry_exceptions: Tuple of exception types that trigger a retry.
        no_retry_exceptions: Tuple of exception types that should NOT be retried (takes precedence).
        delay_key: Config key for delay value (used if retry_delay is None).
        retries_key: Config key for retry count (used if max_retries is None).
        exponential_backoff: If True, delay increases exponentially with each retry.
        backoff_multiplier: Multiplier for exponential backoff.
        max_delay: Maximum delay when using exponential backoff.
        on_retry: Optional callback(attempt, exception, self) called before each retry.
        logger_attr: Attribute name to get logger from self (for instance methods).
        
    Returns:
        Decorated async function with retry logic.
        
    Example:
        @async_retry(
            retries_key='connection_retries',
            delay_key='reconnect_delay',
            no_retry_exceptions=(AuthKeyUnregisteredError, ValueError)
        )
        async def connect(self):
            ...
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Determine retry count and delay
            retries = max_retries if max_retries is not None else get_retry_config(retries_key)
            base_delay = retry_delay if retry_delay is not None else get_delay_config(delay_key)
            
            # Get logger from self if available
            logger = None
            if args and hasattr(args[0], logger_attr):
                logger = getattr(args[0], logger_attr)
            
            last_exception = None
            
            for attempt in range(1, retries + 1):
                try:
                    return await func(*args, **kwargs)
                except no_retry_exceptions as e:
                    # These exceptions should not be retried
                    raise
                except retry_exceptions as e:
                    last_exception = e
                    
                    if attempt >= retries:
                        # Last attempt failed
                        if logger:
                            logger.error(f"{func.__name__} failed after {retries} attempts: {e}")
                        raise
                    
                    # Calculate delay
                    if exponential_backoff:
                        current_delay = min(base_delay * (backoff_multiplier ** (attempt - 1)), max_delay)
                    else:
                        current_delay = base_delay
                    
                    # Log retry
                    if logger:
                        logger.warning(f"{func.__name__} failed (attempt {attempt}/{retries}): {e}. Retrying in {current_delay:.1f}s...")
                    
                    # Call on_retry callback if provided
                    if on_retry:
                        try:
                            if args:
                                on_retry(attempt, e, args[0])
                            else:
                                on_retry(attempt, e, None)
                        except Exception:
                            pass  # Don't let callback errors break retry logic
                    
                    await asyncio.sleep(current_delay)
            
            # Should not reach here, but just in case
            if last_exception:
                raise last_exception
                
        return wrapper
    return decorator


class RetryContext:
    """
    Context manager for manual retry control.
    
    Useful when you need more control than the decorator provides,
    or when retry logic needs to be conditional.
    
    Usage:
        async with RetryContext(max_retries=3, delay=2.0) as ctx:
            while ctx.should_retry():
                try:
                    result = await some_operation()
                    ctx.success()
                    break
                except SomeError as e:
                    ctx.failed(e)
    """
    
    def __init__(
        self,
        max_retries: int = None,
        delay: float = None,
        retries_key: str = 'action_retries',
        delay_key: str = 'action_retry_delay',
        logger: logging.Logger = None,
    ):
        self.max_retries = max_retries if max_retries is not None else get_retry_config(retries_key)
        self.delay = delay if delay is not None else get_delay_config(delay_key)
        self.logger = logger
        self.attempt = 0
        self.last_error = None
        self._succeeded = False
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False  # Don't suppress exceptions
    
    def should_retry(self) -> bool:
        """Check if another attempt should be made."""
        return self.attempt < self.max_retries and not self._succeeded
    
    async def failed(self, error: Exception, delay: bool = True):
        """
        Mark current attempt as failed.
        
        Args:
            error: The exception that caused the failure
            delay: Whether to sleep before next retry
        """
        self.attempt += 1
        self.last_error = error
        
        if self.logger:
            if self.attempt < self.max_retries:
                self.logger.warning(f"Attempt {self.attempt}/{self.max_retries} failed: {error}. Retrying...")
            else:
                self.logger.error(f"All {self.max_retries} attempts failed. Last error: {error}")
        
        if delay and self.attempt < self.max_retries:
            await asyncio.sleep(self.delay)
    
    def success(self):
        """Mark current attempt as successful."""
        self._succeeded = True
    
    def raise_if_exhausted(self):
        """Raise last error if all retries exhausted."""
        if not self._succeeded and self.last_error:
            raise self.last_error


# Pre-configured decorators for common use cases
def action_retry(
    no_retry_exceptions: Tuple[Type[Exception], ...] = (),
    **kwargs
):
    """
    Retry decorator configured for Telegram actions (react, comment, etc.).
    Uses action_retries and action_retry_delay from config.
    """
    return async_retry(
        retries_key='action_retries',
        delay_key='action_retry_delay',
        no_retry_exceptions=no_retry_exceptions,
        **kwargs
    )


def connection_retry(
    no_retry_exceptions: Tuple[Type[Exception], ...] = (),
    **kwargs
):
    """
    Retry decorator configured for connection operations.
    Uses connection_retries and reconnect_delay from config.
    """
    return async_retry(
        retries_key='connection_retries',
        delay_key='reconnect_delay',
        no_retry_exceptions=no_retry_exceptions,
        **kwargs
    )


class ActionOutcome:
    """
    Represents the outcome of handling an exception in a retry loop.
    
    Outcomes:
        - RETRY: Increment attempt counter and retry after delay
        - SKIP: Skip current item and move to next (break inner loop)
        - STOP: Stop the worker entirely (return from function)
        - SUCCESS: Action succeeded (break inner loop, no error)
    """
    RETRY = 'retry'
    SKIP = 'skip'
    STOP = 'stop'
    SUCCESS = 'success'


class WorkerRetryContext:
    """
    Context manager for complex worker retry loops with multiple outcome types.
    
    Designed for task workers that need to handle different exception types
    with different outcomes (retry, skip item, stop worker).
    
    Usage:
        ctx = WorkerRetryContext(logger=self.logger)
        
        for item in items:
            ctx.reset_for_item()  # Reset attempt counter for new item
            
            while ctx.should_retry():
                try:
                    await process_item(item)
                    ctx.success()
                except RetryableError as e:
                    await ctx.retry(e, "Processing failed")
                except SkippableError as e:
                    ctx.skip(e, "Item invalid, skipping")
                except FatalError as e:
                    return ctx.stop(e, "Fatal error, stopping worker")
            
            if ctx.outcome == ActionOutcome.SKIP:
                continue  # Move to next item
            elif ctx.outcome == ActionOutcome.STOP:
                return ctx.stop_result
    """
    
    def __init__(
        self,
        max_retries: int = None,
        delay: float = None,
        retries_key: str = 'action_retries',
        delay_key: str = 'error_retry_delay',
        logger: logging.Logger = None,
    ):
        self.max_retries = max_retries if max_retries is not None else get_retry_config(retries_key)
        self.delay = delay if delay is not None else get_delay_config(delay_key)
        self.logger = logger
        self.reset_for_item()
    
    def reset_for_item(self):
        """Reset state for processing a new item."""
        self.attempt = 0
        self.last_error = None
        self.outcome = None
        self.stop_result = None
    
    def should_retry(self) -> bool:
        """Check if another attempt should be made for current item."""
        # Stop if we've succeeded, skipped, or stopped
        if self.outcome in (ActionOutcome.SUCCESS, ActionOutcome.SKIP, ActionOutcome.STOP):
            return False
        return self.attempt < self.max_retries
    
    def success(self):
        """Mark current item as successfully processed."""
        self.outcome = ActionOutcome.SUCCESS
    
    async def retry(self, error: Exception, message: str = None, delay: bool = True):
        """
        Mark current attempt as failed, will retry.
        
        Args:
            error: The exception that caused the failure
            message: Optional log message
            delay: Whether to sleep before next retry
        """
        self.attempt += 1
        self.last_error = error
        self.outcome = ActionOutcome.RETRY
        
        if self.logger and message:
            self.logger.warning(f"{message} (attempt {self.attempt}/{self.max_retries}): {error}")
        
        if delay and self.attempt < self.max_retries:
            await asyncio.sleep(self.delay)
    
    def skip(self, error: Exception = None, message: str = None):
        """
        Skip current item and move to next.
        
        Args:
            error: Optional exception that caused the skip
            message: Optional log message
        """
        self.last_error = error
        self.outcome = ActionOutcome.SKIP
        
        if self.logger and message:
            self.logger.warning(message)
    
    def stop(self, error: Exception = None, message: str = None, result: Any = None) -> Any:
        """
        Stop the worker entirely.
        
        Args:
            error: Optional exception that caused the stop
            message: Optional log message
            result: Value to return from the worker
            
        Returns:
            The result value (for convenient `return ctx.stop(...)`)
        """
        self.last_error = error
        self.outcome = ActionOutcome.STOP
        self.stop_result = result
        
        if self.logger and message:
            self.logger.error(message)
        
        return result
    
    @property
    def retries_exhausted(self) -> bool:
        """Check if all retries have been exhausted without success."""
        return self.attempt >= self.max_retries and self.outcome != ActionOutcome.SUCCESS
