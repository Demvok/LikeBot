import json, asyncio
from agent import *
from logger import setup_logger

# tmp
import os
from dotenv import load_dotenv
# 

logger = setup_logger("main", "main.log")

async def connect_clients():

    def load_account_config():
        """Load account configuration from accounts.json file."""
        with open('accounts.json', 'r', encoding='utf-8') as file:
            accounts = json.load(file)
            return accounts

    # Stage 1: Load account configuration
    logger.debug('Starting to load account configuration...')
    try:
        accounts = load_account_config()
        if not accounts:
            logger.error("No accounts found in accounts.json. Please add at least one account.")
            raise ValueError("No accounts found in accounts.json.")
        if not isinstance(accounts, list):
            logger.error("Invalid accounts.json format. Please ensure it contains a list of accounts.")
            raise ValueError("Invalid accounts.json format. Please ensure it contains a list of accounts.")
    except Exception as e:
        logger.error(f"Unknown error: {e}")
        raise
    logger.debug('Account configuration loaded successfully.')

    # Stage 2: Initialize Telegram clients for all accounts, authentificate if needed
    if not accounts:
        logger.error("No accounts found in accounts.json. Please add at least one account.")
        raise ValueError("No accounts found in accounts.json.")
    else:
        logger.info(f"Found {len(accounts)} accounts in accounts.json. Initializing clients...")
        clients = []
        for account in accounts:
            client = Client(account)
            clients.append(client)  # Don't await connect here
    
        logger.info(f"Initialized {len(clients)} clients successfully.")
        return clients

async def main():
    logger.info("System is starting...")

    load_dotenv()
    post_link = os.getenv('POST_LINK')
    if not post_link:
        logger.error("POST_LINK not found in .env file.")
        raise ValueError("POST_LINK not found in .env file.")

    clients = await connect_clients()
    
    async with clients[0] as client:
        await client.react_to_message(post_link)
        # await client.comment_on_message(post_link)

    logger.info("System has finished processing.")

asyncio.run(main())