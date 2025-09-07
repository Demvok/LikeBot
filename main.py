import asyncio
import sys
from agent import *
from logger import setup_logger, crash_handler

from taskhandler import *
from database import get_db
db = get_db()


logger = setup_logger("main", "main.log")

@crash_handler
async def main():
    logger.info("System is starting...")
    
    task = await db.get_task(3)
    await task.start()    

    logger.info("System has finished processing.")

if __name__ == "__main__":
    asyncio.run(main())