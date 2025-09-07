import asyncio
import sys
import atexit
from agent import *
from logger import setup_logger, crash_handler, cleanup_logging

from taskhandler import *
from database import get_db


# Register cleanup function
atexit.register(cleanup_logging)

@crash_handler
async def main():
    logger = setup_logger("main", "main.log")
    logger.info("System is starting...")
    
    try:
        db = get_db()
        task = await db.get_task(3)

        await task.run_and_wait()    
        
    except Exception as e:
        logger.error(f"Error in main: {e}")
        raise
    finally:
        logger.info("Main function completed")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        cleanup_logging()