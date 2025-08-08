import re, yaml, json, os, random
import pandas as pd
from telethon.tl.functions.messages import SendReactionRequest
from telethon import TelegramClient, functions, types
from logger import setup_logger
from dotenv import load_dotenv

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
    def load_accounts(cls, file_path='accounts.json'):
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
        df = pd.read_csv(file_path)
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
        df = pd.DataFrame([account.to_dict() for account in accounts])
        df.to_csv(file_path, index=False)

    @classmethod
    def save_accounts(cls, accounts, file_path='accounts.json'):
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

    active_emoji_palette = config['reactions']['palettes']['positive']

    def __init__(self, account):
        self.account = account
        try:
            self.session_name = account.session_name
            self.phone_number = account.phone_number
        except KeyError as e:
            raise ValueError(f"Missing key in account configuration: {e}")
        
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
            self.client = TelegramClient(f'sessions/{self.session_name}', api_id, api_hash)
            await self.client.start()
            self.logger.info(f"Client for {self.phone_number} started successfully.")
            return self  # Add this line to return the Client object
        except Exception as e:
            self.logger.error(f"Failed to connect client for {self.phone_number}: {e}")
            raise

    @classmethod
    async def connect_clients(cls, accounts, logger):
        # WIP
        logger.info(f"Found {len(accounts)} accounts in accounts.json. Initializing clients...")
        clients = []
        for account in accounts:
            client = cls(account)
            clients.append(client)  # Don't await connect here
    
        logger.info(f"Initialized {len(clients)} clients successfully.")
        return clients

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



    async def react_to_message(self, message_link:str):
        entity, message = await self._get_message_ids(message_link)
        await self._react(message, entity)

    async def comment_on_message(self, message_link:str):
        entity, message = await self._get_message_ids(message_link)
        await self._comment(message, entity)



