import os, re
from dotenv import load_dotenv
from telethon import TelegramClient, functions, types
from telethon.tl.functions.messages import SendReactionRequest
from logger import setup_logger

load_dotenv()

api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")
session_name = os.getenv("SESSION_NAME", "default")
TARGET_CHAT='https://t.me/+trTEmD0st1s4MmEy'

logger = setup_logger("main", "main.log")


client = TelegramClient(f'sessions/{session_name}', api_id, api_hash)

async def react_to_message(client, message, target_chat):
    try:
        await client(SendReactionRequest(
            peer=target_chat,
            msg_id=message.id,
            reaction=[types.ReactionEmoji(emoticon='👍')],
            add_to_recent=True
        ))
        logger.error("Реакцію додано!")
    except Exception as e:
        logger.warning(f"Помилка реакції: {e}")

async def comment_on_message(client, message, target_chat):
    try:
        discussion = await client(functions.messages.GetDiscussionMessageRequest(
            peer=target_chat,
            msg_id=message.id
        ))
        logger.debug(f"Обговорення знайдено: {discussion.messages[0].id}")
        await client.send_message(
                entity=discussion.messages[0].chat_id,
                message="Дякую за пост! 🔥",
                reply_to=message.id
            )
        logger.debug("Коментар додано!")
    except Exception as e:
        logger.warning(f"Помилка коментаря: {e}")



async def get_message_and_chat_id_from_link(link):
    # Example link: https://t.me/c/123456789/12345 or https://t.me/username/12345
    match = re.match(r'https://t\.me/(c/)?([\w\d_]+)/(\d+)', link)
    if not match:
        raise ValueError("Invalid Telegram message link format.")

    is_private = match.group(1) == 'c/'
    chat_part = match.group(2)
    message_id = int(match.group(3))

    if is_private:
        # For private groups/channels, chat_id is -100 + chat_part
        chat_id = int(f"-100{chat_part}")
    else:
        # For public, chat_part is username
        chat_id = chat_part

    # Get entity and message using TelegramClient
    entity = await client.get_entity(chat_id)
    message = await client.get_messages(entity, ids=message_id)

    return entity, message






async def main():
    await client.start()
    logger.info("✅ Бот запущено...")
   
    entity, message = await get_message_and_chat_id_from_link('https://t.me/c/2723750105/12')

    await react_to_message(message, entity)
    await comment_on_message(message, entity)

    await client.run_until_disconnected()


if __name__ == "__main__":
    with client:
        client.loop.run_until_complete(main())