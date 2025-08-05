import re, yaml
from telethon.tl.functions.messages import SendReactionRequest
from telethon import TelegramClient, functions, types
from logger import setup_logger
import random

def load_config():
    with open('config.yaml', 'r', encoding='utf-8') as file:
        return yaml.safe_load(file)
config = load_config()

class Client(object):

    active_emoji_palette = config['reactions']['palettes']['positive']

    def __init__(self, account):
        self.account = account
        try:
            self.api_id = account['api_id']
            self.api_hash = account['api_hash']
            self.session_name = account['session_name']
            self.phone_number = account['phone_number']
        except KeyError as e:
            raise ValueError(f"Missing key in account configuration: {e}")
        
        self.logger = setup_logger(f"{self.phone_number}", f"accounts/account_{self.phone_number}.log")
        self.logger.info(f"Initializing client for {self.phone_number}. Awaiting connection...")
        self.client = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.disconnect()
            self.logger.info(f"Client for {self.phone_number} disconnected.")

    async def connect(self):
        try:
            self.client = TelegramClient(f'sessions/{self.session_name}', self.api_id, self.api_hash)
            await self.client.start()
            self.logger.info(f"Client for {self.phone_number} started successfully.")
        except Exception as e:
            self.logger.error(f"Failed to connect client for {self.phone_number}: {e}")
            raise

    async def _react(self, message, target_chat):
        try:
            emoticon = random.choice(self.active_emoji_palette)
            await self.client(SendReactionRequest(
                peer=target_chat,
                msg_id=message.id,
                reaction=[types.ReactionEmoji(emoticon=emoticon)],
                add_to_recent=True
            ))
            self.logger.info("Reaction added successfully")
        except Exception as e:
            self.logger.warning(f"Error adding reaction: {e}")

    async def _comment(self, message, target_chat):
        try:
            discussion = await self.client(functions.messages.GetDiscussionMessageRequest(
                peer=target_chat,
                msg_id=message.id
            ))
            self.logger.debug(f"Discussion found: {discussion.messages[0].id}")
            
            # Use the discussion message ID, not the original channel message ID
            discussion_message_id = discussion.messages[0].id
            discussion_chat = discussion.chats[0]
            
            await self.client.send_message(
                entity=discussion_chat,
                message="Дякую за пост! 🔥",
                reply_to=discussion_message_id  # Use discussion message ID, not original message.id
            )
            self.logger.info("Comment added successfully!")
        except Exception as e:
            self.logger.warning(f"Error adding comment: {e}")

    async def _get_message_and_chat_id_from_link(self, link):
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
        entity = await self.client.get_entity(chat_id)
        message = await self.client.get_messages(entity, ids=message_id)
        self.logger.debug(f"Retrieved message {message_id} from chat {chat_id}")
        return entity, message
    



    async def react_to_message(self, message_link:str):
        entity, message = await self._get_message_and_chat_id_from_link(message_link)
        await self._react(message, entity)

    async def comment_on_message(self, message_link:str):
        entity, message = await self._get_message_and_chat_id_from_link(message_link)
        await self._comment(message, entity)
