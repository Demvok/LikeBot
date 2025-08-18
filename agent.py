import re, yaml, json, os, random, asyncio
from pandas import read_csv, DataFrame
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

        if  self.session_name is None:
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
        """Create a TelegramClient connection."""
        client = Client(self)  # Create Client instance
        await client.connect()  # Connect the client
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
    def load_accounts(cls, file_path=config.get('filepaths', {}).get('accounts', 'accounts.json')):
        """Load accounts from accounts.json or accounts.csv file."""
        if os.path.exists(file_path):
            if file_path.endswith('.json'):
                return cls._load_accounts_from_json(file_path)
            elif file_path.endswith('.csv'):
                return cls._load_accounts_from_csv(file_path)
        raise FileNotFoundError("No accounts.json or accounts.csv file found.")

    @classmethod
    def _load_accounts_from_json(cls, file_path):
        """Load accounts from a JSON file."""
        with open(file_path, 'r', encoding='utf-8') as file:
            accounts = json.load(file)
            return [cls(account) for account in accounts]

    @classmethod
    def _load_accounts_from_csv(cls, file_path):
        """Load accounts from a CSV file."""
        df = read_csv(file_path)
        accounts = [cls(row.to_dict()) for index, row in df.iterrows()]
        return accounts

    @classmethod
    def _save_accounts_to_json(cls, accounts, file_path):
        """Save accounts to a JSON file."""
        with open(file_path, 'w', encoding='utf-8') as file:
            json.dump([account.to_dict() for account in accounts], file, indent=4)

    @classmethod
    def _save_accounts_to_csv(cls, accounts, file_path):
        """Save accounts to a CSV file."""
        df = DataFrame([account.to_dict() for account in accounts])
        df.to_csv(file_path, index=False)

    @classmethod
    def save_accounts(cls, accounts, file_path=config.get('filepaths', {}).get('accounts', 'accounts.json')):
        """Save accounts to a file, either JSON or CSV."""
        if file_path.endswith('.json'):
            cls._save_accounts_to_json(accounts, file_path)
        elif file_path.endswith('.csv'):
            cls._save_accounts_to_csv(accounts, file_path)
        else:
            raise ValueError("Unsupported file type. Use 'json' or 'csv'.")
        
    @classmethod
    def get_accounts(cls, phones:list):
        """Get a list of Account objects from a list of phone numbers."""
        return [elem for elem in Account.load_accounts() if elem.phone_number in phones]




class Client(object):

    def __init__(self, account):
        self.account = account
        try:
            self.session_name = account.session_name
            self.phone_number = account.phone_number
        except KeyError as e:
            raise ValueError(f"Missing key in account configuration: {e}")
        self.active_emoji_palette = ['👍', '❤️', '🔥']  # Default emoji palette, replaces automatically
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
        try:
            self.client = TelegramClient(f'{config.get('filepaths', {}).get('sessions_folder', 'sessions/')}{self.session_name}', api_id, api_hash)
            await self.client.start()
            self.logger.debug(f"Client for {self.phone_number} started successfully.")
            return self  # Add this line to return the Client object
        except Exception as e:
            self.logger.error(f"Failed to connect client for {self.phone_number}: {e}")
            raise

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

    async def get_message_content(self, chat_id=None, message_id=None, message_link=None):
        """
        Retrieve the content of a single message by chat and message_id.
        """
        try:
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

    # Basic actions

    async def _react(self, message, target_chat):
        try:
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
            
            # await asyncio.sleep(random.uniform(0.5, 2))  # Prevent spam if everything is broken

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

        # Get entity and message using TelegramClient
        entity = await self.client.get_entity(chat_id)
        message = await self.client.get_messages(entity, ids=message_id)
        self.logger.debug(f"Retrieved message {message_id} from chat {chat_id}")
        return entity, message


    # Actions

    async def undo_reaction(self, message_id: int=None, chat_id: str=None, message_link:str=None):
        if message_link:
            entity, message = await self._get_message_ids(message_link)
        else:
            entity = await self.client.get_entity(chat_id)
            message = await self.client.get_messages(entity, ids=message_id)
        await self._undo_reaction(message, entity)

    async def undo_comment(self, message_id: int=None, chat_id: str=None, message_link:str=None):
        if message_link:
            entity, message = await self._get_message_ids(message_link)
        else:
            entity = await self.client.get_entity(chat_id)
            message = await self.client.get_messages(entity, ids=message_id)
        await self._undo_comment(message, entity)

    async def react(self, message_id:int=None, chat_id:str=None, message_link:str=None):
        """React to a message by its ID in a specific chat."""
        if message_link:
            entity, message = await self._get_message_ids(message_link)
        else:
            entity = await self.client.get_entity(chat_id)
            message = await self.client.get_messages(entity, ids=message_id)
        await self._react(message, entity)

    async def comment(self, content, message_id:int=None, chat_id:str=None, message_link:str=None):
        """Comment on a message by its ID in a specific chat."""
        if message_link:
            entity, message = await self._get_message_ids(message_link)
        else:
            entity = await self.client.get_entity(chat_id)
            message = await self.client.get_messages(entity, ids=message_id)
        await self._comment(message=message, entity=entity, content=content)



