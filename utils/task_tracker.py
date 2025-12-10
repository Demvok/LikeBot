"""
Global task tracker for graceful shutdown.

Provides a centralized set to track active background tasks across the application.
Used by the FastAPI lifespan manager to cancel tasks on shutdown.
"""

import asyncio

# Global set to track active background tasks
active_tasks: set[asyncio.Task] = set()


def track_task(task: asyncio.Task) -> None:
    """
    Add a task to the global tracker.
    
    Args:
        task: The asyncio.Task to track
    """
    active_tasks.add(task)
    # Automatically remove when done
    task.add_done_callback(lambda t: active_tasks.discard(t))


async def cancel_all_tasks(timeout: float = 5.0) -> None:
    """
    Cancel all tracked tasks and wait for them to finish.
    
    Args:
        timeout: Maximum time to wait for tasks to cancel (seconds)
    """
    if not active_tasks:
        return
    
    import logging
    logger = logging.getLogger("likebot.task_tracker")
    
    logger.info(f"Cancelling {len(active_tasks)} active background tasks...")
    for task in active_tasks:
        if not task.done():
            task.cancel()
    
    # Wait for all tasks to finish cancellation (with timeout)
    await asyncio.wait(active_tasks, timeout=timeout)
    logger.info("All background tasks cancelled")
