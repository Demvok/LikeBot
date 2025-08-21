import re, yaml, os, random, asyncio
from telethon.tl.functions.messages import SendReactionRequest, GetMessagesRequest
from telethon import TelegramClient, functions, types
from logger import setup_logger
from dotenv import load_dotenv
from humaniser import estimate_reading_time

load_dotenv()
api_id = os.getenv('api_id')
api_hash = os.getenv('api_hash')

def load_config():
    with open('config.yaml', 'r', encoding='utf-8') as file:
        return yaml.safe_load(file)
config = load_config()




class Account(object):
    
    def __init__(self, account_data):
        try:
            self.account_id = account_data.get('account_id', None)
            self.session_name = account_data.get('session_name', None)
            self.phone_number = account_data.get('phone_number')
        except KeyError as e:
            raise ValueError(f"Missing key in account configuration: {e}")

        if  self.session_name is None:  # Session creation should be linked somewhere here
            self.session_name = self.phone_number
    
    def __repr__(self):
        return f"Account({self.account_id}, {self.phone_number})"
    
    def __str__(self):
        return f"Account ID: {self.account_id}, phone: {self.phone_number}, session: {self.session_name}"
    
    def to_dict(self):
        """Convert Account object to dictionary."""
        return {
            'account_id': self.account_id,
            'session_name': self.session_name,
            'phone_number': self.phone_number
        }

    async def create_connection(self):
        """Create a TelegramClient connection from account, useful for debugging."""
        client = Client(self)
        await client.connect()
        client.logger.info(f"Client for {self.phone_number} connected successfully.")  # Use self.logger instead of client.logger
        return client

    @classmethod
    def from_keys(cls, phone_number, account_id=None, session_name=None):
        """Create an Account object from a dictionary."""
        account_data = {
            'account_id': account_id,
            'session_name': session_name,
            'phone_number': phone_number
        }
        return cls(account_data)

    @classmethod
    def get_accounts(cls, phones:list):
        """Get a list of Account objects from a list of phone numbers."""
        from database import get_db
        db = get_db()
        return [elem for elem in db.load_all_accounts() if elem.phone_number in phones]




class Client(object):

    def __init__(self, account):
        self.account = account
        try:
            self.session_name = account.session_name
            self.phone_number = account.phone_number
        except KeyError as e:
            raise ValueError(f"Missing key in account configuration: {e}")
        self.active_emoji_palette = ['👍', '❤️', '🔥']  # Default emoji palette, is replaced automatically
        self.logger = setup_logger(f"{self.phone_number}", f"accounts/account_{self.phone_number}.log")
        self.logger.info(f"Initializing client for {self.phone_number}. Awaiting connection...")
        self.client = None

    def __repr__(self):
        return f"Client({self.account}) connected: {self.is_connected}"
    
    def __str__(self):
        return f"Client ({'connected' if self.is_connected else 'disconnected'}) for {self.phone_number} with session {self.session_name}"

    @property
    def is_connected(self):
        return self.client.is_connected()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.disconnect()
            self.logger.info(f"Client for {self.phone_number} disconnected.")


    async def connect(self):
        retries = config.get('delays', {}).get('connection_retries', 5)
        delay = config.get('delays', {}).get('reconnect_delay', 3)
        attempt = 0
        while attempt < retries:  # Actually this could be implemented via inbuilt TelegramClient properties, but I'm not sure how to log it
            try:
                self.client = TelegramClient(
                    f"{config.get('filepaths', {}).get('sessions_folder', 'sessions/')}{self.session_name}",
                    api_id, api_hash  # Add proxy logic here
                )
                await self.client.start()
                self.logger.debug(f"Client for {self.phone_number} started successfully.")
                await self.update_account_id_from_telegram()
                return self
            except Exception as e:
                attempt += 1
                self.logger.error(f"Failed to connect client for {self.phone_number} (attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    await asyncio.sleep(delay)
                else:
                    raise

    async def disconnect(self):
        """Disconnect the client."""
        retries = config.get('delays', {}).get('connection_retries', 5)
        delay = config.get('delays', {}).get('reconnect_delay', 3)
        attempt = 0
        while attempt < retries:
            try:
                await self.client.disconnect()
                self.logger.info(f"Client for {self.phone_number} disconnected.")
                break
            except Exception as e:
                attempt += 1
                self.logger.error(f"Failed to disconnect client for {self.phone_number} (attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    await asyncio.sleep(delay)
                else:
                    raise

    async def ensure_connected(self):
        if not self.client or not self.is_connected:
            self.logger.info(f"Client for {self.phone_number} is not connected. Reconnecting...")
            await self.connect()

    @classmethod
    async def connect_clients(cls, accounts, logger):
        if logger:
            logger.info(f"Connecting clients for {len(accounts)} accounts...")
        
        clients = []
        for account in accounts:  # Connect clients sequentially to avoid database lock
            client = Client(account)
            await client.connect()
            clients.append(client)
        
        if logger:
            logger.info(f"Connected clients for {len(clients)} accounts.")
        
        return clients if clients else None
    
    @classmethod
    async def disconnect_clients(cls, clients, logger):
        for client in clients:
            try:
               await client.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting client: {e}")
        return None  # Return None to indicate all clients are disconnected

    async def get_message_content(self, chat_id=None, message_id=None, message_link=None):
        """
        Retrieve the content of a single message by chat and message_id.
        """
        try:
            await self.ensure_connected()
            if message_link and not (message_id and chat_id):
                entity, message = await self._get_message_ids(message_link)
                return message.message if message else None
            else:
                if not chat_id or not message_id:
                    raise ValueError("Either message_link or both chat_id and message_id must be provided.")

            entity = await self.client.get_entity(chat_id)
            message = await self.client.get_messages(entity, ids=message_id)
            return message.message if message else None
        except Exception as e:
            self.logger.warning(f"Error retrieving message content: {e}")
            return None

    async def update_account_id_from_telegram(self):
        """Fetch account id from Telegram and update the account record in accounts file."""
        await self.ensure_connected()
        me = await self.client.get_me()
        account_id = me.id if hasattr(me, 'id') else None
        if account_id:
            from database import get_db  # Avoid circular import if any
            db = get_db()
            db.update_account(self.phone_number, {'account_id': account_id})
            self.logger.info(f"Updated account_id for {self.phone_number} to {account_id}")
            self.account.account_id = account_id
        else:
            self.logger.warning("Could not fetch account_id from Telegram.")

    # Basic actions

    async def _react(self, message, target_chat):
        try:
            await self.ensure_connected()
            if config.get('delays', {}).get('humanisation_level', 1) >= 1:  # If humanisation level is 1 or higher it should consider reading time
                msg_content = await self.get_message_content(chat_id=target_chat, message_id=message.id)
                if not msg_content:
                    self.logger.warning("Message content is empty, skipping reaction.")
                    return
                reading_time = estimate_reading_time(msg_content)
                self.logger.debug(f"Estimated reading time: {reading_time} seconds")
                await asyncio.sleep(reading_time)

            if not self.active_emoji_palette:
                # Load emoji palette from config
                self.active_emoji_palette = config.get('reactions_palettes', []).get('positive', [])
                if not self.active_emoji_palette:
                    raise ValueError("Emoji palette is empty in the configuration.")
            emoticon = random.choice(self.active_emoji_palette)

            await asyncio.sleep(random.uniform(0.5, 2))  # Simulate human-like delay and prevent spam

            await self.client(SendReactionRequest(
                peer=target_chat,
                msg_id=message.id,
                reaction=[types.ReactionEmoji(emoticon=emoticon)],
                add_to_recent=True
            ))
            self.logger.info("Reaction added successfully")
        except Exception as e:
            self.logger.warning(f"Error adding reaction: {e}")

    async def _comment(self, message, target_chat, content):
        try:
            await self.ensure_connected()
            if config.get('delays', {}).get('humanisation_level', 1) >= 1:  # If humanisation level is 1 or higher it should consider reading time
                msg_content = await self.get_message_content(chat_id=target_chat, message_id=message.id)
                reading_time = estimate_reading_time(msg_content)
                self.logger.debug(f"Estimated reading time: {reading_time} seconds")
                await asyncio.sleep(reading_time)

            discussion = await self.client(functions.messages.GetDiscussionMessageRequest(
                peer=target_chat,
                msg_id=message.id
            ))
            self.logger.debug(f"Discussion found: {discussion.messages[0].id}")
            
            # Use the discussion message ID, not the original channel message ID
            discussion_message_id = discussion.messages[0].id
            discussion_chat = discussion.chats[0]
            
            # Text typing speed should be added to simulate properly

            await asyncio.sleep(random.uniform(0.5, 2))  # Prevent spam if everything is broken

            await self.client.send_message(
                entity=discussion_chat,
                message=content,
                reply_to=discussion_message_id  # Use discussion message ID, not original message.id
            )
            self.logger.info("Comment added successfully!")
        except Exception as e:
            self.logger.warning(f"Error adding comment: {e}")

    async def _undo_reaction(self, message, target_chat):
        try:
            await self.ensure_connected()
            await asyncio.sleep(random.uniform(0.5, 1))  # Prevent spam if everything is broken
            await self.client(SendReactionRequest(
                peer=target_chat,
                msg_id=message.id,
                reaction=[],  # Empty list removes reaction
                add_to_recent=False
            ))
            self.logger.info("Reaction removed successfully")
        except Exception as e:
            self.logger.warning(f"Error removing reaction: {e}")

    async def _undo_comment(self, message, target_chat):
        """
        Deletes all user comments on given post.
        """
        try:
            await self.ensure_connected()
            await asyncio.sleep(random.uniform(0.5, 1))  # Prevent spam if everything is broken
            discussion = await self.client(functions.messages.GetDiscussionMessageRequest(
                peer=target_chat,
                msg_id=message.id
            ))
            discussion_chat = discussion.chats[0]
            # Find comments by this user on this discussion
            async for msg in self.client.iter_messages(discussion_chat, reply_to=discussion.messages[0].id, from_user='me'):
                await msg.delete()
                self.logger.info(f"Comment {msg.id} deleted successfully!")
        except Exception as e:
            self.logger.warning(f"Error deleting comment: {e}")

    async def _get_message_ids(self, link):
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
        
        await self.ensure_connected()
        # Get entity and message using TelegramClient
        entity = await self.client.get_entity(chat_id)
        message = await self.client.get_messages(entity, ids=message_id)
        self.logger.debug(f"Retrieved message {message_id} from chat {chat_id}")
        return entity, message


    # Actions

    async def undo_reaction(self, message_id: int=None, chat_id: str=None, message_link:str=None):
        retries = config.get('delays', {}).get('action_retries', 3)
        delay = config.get('delays', {}).get('action_retry_delay', 3)
        attempt = 0
        while attempt < retries:
            try:
                if message_link:
                    entity, message = await self._get_message_ids(message_link)
                else:
                    entity = await self.client.get_entity(chat_id)
                    message = await self.client.get_messages(entity, ids=message_id)
                await self._undo_reaction(message, entity)
                return
            except Exception as e:
                attempt += 1
                self.logger.warning(f"undo_reaction failed (attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    await asyncio.sleep(delay)
                else:
                    raise

    async def undo_comment(self, message_id: int=None, chat_id: str=None, message_link:str=None):
        retries = config.get('delays', {}).get('action_retries', 3)
        delay = config.get('delays', {}).get('action_retry_delay', 3)
        attempt = 0
        while attempt < retries:
            try:
                if message_link:
                    entity, message = await self._get_message_ids(message_link)
                else:
                    entity = await self.client.get_entity(chat_id)
                    message = await self.client.get_messages(entity, ids=message_id)
                await self._undo_comment(message, entity)
                return
            except Exception as e:
                attempt += 1
                self.logger.warning(f"undo_comment failed (attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    await asyncio.sleep(delay)
                else:
                    raise

    async def react(self, message_id:int=None, chat_id:str=None, message_link:str=None):
        """React to a message by its ID in a specific chat."""
        retries = config.get('delays', {}).get('action_retries', 3)
        delay = config.get('delays', {}).get('action_retry_delay', 3)
        attempt = 0
        while attempt < retries:
            try:
                if message_link:
                    entity, message = await self._get_message_ids(message_link)
                else:
                    entity = await self.client.get_entity(chat_id)
                    message = await self.client.get_messages(entity, ids=message_id)
                await self._react(message, entity)
                return
            except Exception as e:
                attempt += 1
                self.logger.warning(f"react failed (attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    await asyncio.sleep(delay)
                else:
                    raise

    async def comment(self, content, message_id:int=None, chat_id:str=None, message_link:str=None):
        """Comment on a message by its ID in a specific chat."""
        retries = config.get('delays', {}).get('action_retries', 3)
        delay = config.get('delays', {}).get('action_retry_delay', 3)
        attempt = 0
        while attempt < retries:
            try:
                if message_link:
                    entity, message = await self._get_message_ids(message_link)
                else:
                    entity = await self.client.get_entity(chat_id)
                    message = await self.client.get_messages(entity, ids=message_id)
                await self._comment(message=message, entity=entity, content=content)
                return
            except Exception as e:
                attempt += 1
                self.logger.warning(f"comment failed (attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    await asyncio.sleep(delay)
                else:
                    raise



