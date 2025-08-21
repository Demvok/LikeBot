import asyncio
from agent import *
from logger import setup_logger


logger = setup_logger("main", "main.log")

async def main():
    logger.info("System is starting...")

    # NYI

    # clients = await connect_clients(logger)
    


    logger.info("System has finished processing.")

asyncio.run(main())