import re, os, random, asyncio
from pandas import Timestamp 
from telethon.tl.functions.messages import SendReactionRequest
from telethon import TelegramClient, functions, types, errors
from telethon.sessions import StringSession
from logger import SAVE_TO, setup_logger, load_config
from dotenv import load_dotenv
from schemas import AccountStatus
from encryption import decrypt_secret, encrypt_secret

load_dotenv()
api_id = os.getenv('api_id')
api_hash = os.getenv('api_hash')

config = load_config()



class Account(object):

    AccountStatus = AccountStatus
    
    def __init__(self, account_data):
        try:
            self.phone_number = account_data.get('phone_number')
            self.account_id = account_data.get('account_id', None)
            self.session_name = account_data.get('session_name', None)
            self.session_encrypted = account_data.get('session_encrypted', None)
            self.twofa = account_data.get('twofa', False)
            self.password_encrypted = account_data.get('password_encrypted', None)
            self.notes = account_data.get('notes', "")
            self.status = account_data.get('status', self.AccountStatus.NEW)
            self.created_at = account_data.get('created_at', Timestamp.now())
            self.updated_at = account_data.get('updated_at', Timestamp.now())

        except KeyError as e:
            raise ValueError(f"Missing key in account configuration: {e}")

        if self.twofa and not self.password_encrypted:
            raise ValueError("2FA is enabled but no password provided in account configuration.")

        if self.session_name is None:
            self.session_name = self.phone_number
    
    def __repr__(self):
        return f"Account({self.account_id}, {self.phone_number})"
    
    def __str__(self):
        return f"Account ID: {self.account_id}, phone: {self.phone_number}, session: {self.session_name}"
    
    def to_dict(self):
        """Convert Account object to dictionary matching AccountDict schema."""
        return {
            'account_id': self.account_id,
            'session_name': self.session_name,
            'phone_number': self.phone_number,
            'session_encrypted': self.session_encrypted,
            'twofa': self.twofa,
            'password_encrypted': self.password_encrypted,
            'notes': self.notes,
            'status': self.status.name if isinstance(self.status, AccountStatus) else self.status,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }

    async def create_connection(self):
        """Create a TelegramClient connection from account, useful for debugging."""
        client = Client(self)
        await client.connect()
        client.logger.info(f"Client for {self.phone_number} connected successfully.")  # Use self.logger instead of client.logger
        return client

    async def add_password(self, password):
        """Add or update the encrypted password for 2FA."""
        if not password:
            raise ValueError("Password cannot be empty.")
        self.password_encrypted = encrypt_secret(password, b'Password')
        self.twofa = True

        from database import get_db  # Avoid circular import if any
        db = get_db()
        await db.update_account(self.phone_number, {'password_encrypted': self.password_encrypted, 'twofa': self.twofa})

    @classmethod
    def from_keys(
        cls,
        phone_number,
        account_id=None,
        session_name=None,
        session_encrypted=None,
        twofa=False,
        password_encrypted=None,
        password=None,
        notes=None,
        status=None,
        created_at=None,
        updated_at=None
    ):
        """Create an Account object from keys, matching AccountBase schema."""
        account_data = {
            'phone_number': phone_number,
            'account_id': account_id,
            'session_name': session_name,
            'session_encrypted': session_encrypted,
            'twofa': twofa,
            'password_encrypted': password_encrypted if password_encrypted else encrypt_secret(password, b'Password') if password else None,
            'notes': notes if notes is not None else "",
            'status': status if status is not None else cls.AccountStatus.NEW,
            'created_at': created_at or Timestamp.now(),
            'updated_at': updated_at or Timestamp.now()
        }
        return cls(account_data)

    @classmethod
    async def get_accounts(cls, phones:list):
        """Get a list of Account objects from a list of phone numbers."""
        from database import get_db
        db = get_db()
        all_accounts = await db.load_all_accounts()
        return [elem for elem in all_accounts if elem.phone_number in phones]


class Client(object):

    def __init__(self, account):
        self.account = account
        try:
            for attr in vars(account):  # Copy all fields from account
                setattr(self, attr, getattr(account, attr))
        except KeyError as e:
            raise ValueError(f"Missing key in account configuration: {e}")
        
        self.active_emoji_palette = config.get('reactions_palettes', []).get('positive', [])  # Default emoji palette, is replaced automatically
        if not self.active_emoji_palette:
            raise ValueError("Emoji palette is empty in the configuration.")
        
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

    async def _fetch_verification_code(self):
        """Fetch the verification code from an external source or user input."""
        # Placeholder for actual implementation
        # For now, we will just ask the user to input the code manually
        code = input(f'Enter the verification code for {self.phone_number}: ').strip()
        return code

    async def _get_session(self):
        if self.session_encrypted:
            self.logger.info(f"Using existing session for {self.phone_number}.")
            return StringSession(decrypt_secret(self.session_encrypted))
        else:
            self.logger.info(f"Creating new session for {self.phone_number}.")

            try:
                session_folder = config.get('filepaths', {}).get('sessions_folder', 'sessions/')

                if not os.path.exists(session_folder):
                    os.makedirs(session_folder)

                self.client = TelegramClient(
                    f"{session_folder}{self.session_name}",
                    api_id=api_id,
                    api_hash=api_hash
                )

                await self.client.connect()  # Connect before checking authorization
                
                if await self.client.is_user_authorized():
                    self.logger.info(f"Client for {self.phone_number} is already authorized.")
                else:
                    self.logger.info(f"Client for {self.phone_number} is not authorized. Sending code...")
                    
                    await self.client.send_code_request(self.phone_number)  # Sending the verification code
                    self.logger.debug(f"Verification code sent to {self.phone_number}.")

                    code = await self._fetch_verification_code()
                    if not code:
                        self.logger.warning("No verification code provided.")
                        raise ValueError("Verification code cannot be empty.")
                    else:
                        self.logger.debug(f"Received verification code: {code}")

                    try:
                        self.logger.debug("Attempting to sign in with verification code.")
                        await self.client.sign_in(phone=self.phone_number, code=code)
                    except errors.SessionPasswordNeededError:
                        self.logger.info(f"2FA is enabled for {self.phone_number}, password required.")
                        if not self.password_encrypted:
                            self.logger.error("2FA is required but no password is configured.")
                            raise ValueError("2FA password is required but not provided.")
                        
                        password = decrypt_secret(self.password_encrypted, b'Password')
                        if not password:
                            self.logger.error("Failed to decrypt 2FA password.")
                            raise ValueError("Failed to decrypt 2FA password.")
                        
                        self.logger.debug("Signing in with 2FA password.")
                        await self.client.sign_in(password=password)
                    except Exception as e:
                        self.logger.error(f"Error during sign-in for {self.phone_number}: {e}")
                        raise

                self.session_encrypted = encrypt_secret(StringSession.save(self.client.session))
                await self.client.disconnect()  # Ensure the session file is closed before deleting
                
                from database import get_db  # Avoid circular import if any
                db = get_db()
                await db.update_account(self.phone_number, {'session_encrypted': self.session_encrypted, 'status': AccountStatus.LOGGED_IN.name})
                
                self.logger.info(f"Session for {self.phone_number} saved.")

                return StringSession(decrypt_secret(self.session_encrypted))
            except Exception as e:
                raise e
            finally:
                try:                
                    await self.client.disconnect()
                    os.remove(f"{config.get('filepaths', {}).get('sessions_folder', 'sessions/')}{self.session_name}.session")
                    self.logger.info(f"Temporary session file deleted for {self.phone_number}.")
                except Exception as e:
                    self.logger.error(f"Failed to delete temporary session file for {self.phone_number}: {e}")
        


    async def connect(self):
        retries = config.get('delays', {}).get('connection_retries', 5)
        delay = config.get('delays', {}).get('reconnect_delay', 3)
        attempt = 0
        while attempt < retries:  # Actually this could be implemented via inbuilt TelegramClient properties, but I'm not sure how to log it
            try:    
                try:
                    session = await self._get_session()
                except Exception as e:
                    self.logger.error(f"Session creation attempt failed: {e}")
                    self.session_encrypted = None
                    raise
                
                self.client = TelegramClient(
                    session=session,
                    api_id=api_id,
                    api_hash=api_hash 
                    # Add proxy logic here
                )                
                if not self.client:
                    raise ValueError("TelegramClient is not initialized.")

                self.logger.debug(f"Starting client for {self.phone_number}...")
                await self.client.connect()

                self.logger.debug(f"Client for {self.phone_number} started successfully.")
                if not self.account_id:
                    await self.update_account_id_from_telegram()
            
                return self
            except ValueError as e:
                self.logger.error(f"Failed to create session for {self.phone_number}: {e}")
            except Exception as e:
                attempt += 1
                self.logger.error(f"Failed to connect client for {self.phone_number} (attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    await asyncio.sleep(delay)
                else:
                    self.logger.critical(f"All connection attempts failed for {self.phone_number}. Error: {e}")
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
                    self.logger.critical(f"All disconnection attempts failed for {self.phone_number}. Error: {e}")
                    raise

    async def ensure_connected(self):
        if not self.client or not self.is_connected:
            self.logger.info(f"Client for {self.phone_number} is not connected. Reconnecting...")
            await self.connect()

    @classmethod
    async def connect_clients(cls, accounts: list[Account], logger):
        if logger:
            logger.info(f"Connecting clients for {len(accounts)} accounts...")

        clients = [Client(account) for account in accounts]
        
        await asyncio.gather(*(client.connect() for client in clients))  # Connect all clients in parallel

        if logger:
            logger.info(f"Connected clients for {len(clients)} accounts.")

        return clients if clients else None
    
    @classmethod
    async def disconnect_clients(cls, clients: list["Client"], logger):
        if not clients:
            if logger:
                logger.info("No clients to disconnect.")
            return None
            
        if logger:
            logger.info(f"Disconnecting {len(clients)} clients...")

        await asyncio.gather(*(client.disconnect() for client in clients))

        if logger:
            logger.info(f"Disconnected {len(clients)} clients.")

        return None  # Return None to indicate all clients are disconnected

    async def get_message_content(self, chat_id=None, message_id=None, message_link=None) -> str | None:
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
            raise

    async def update_account_id_from_telegram(self):
        """Fetch account id from Telegram and update the account record in accounts file."""
        try:
            await self.ensure_connected()
            me = await self.client.get_me()
            account_id = me.id if hasattr(me, 'id') else None
            if account_id:
                from database import get_db  # Avoid circular import if any
                db = get_db()
                await db.update_account(self.phone_number, {'account_id': account_id})
                self.logger.info(f"Updated account_id for {self.phone_number} to {account_id}")
                self.account.account_id = account_id
            else:
                self.logger.warning("Could not fetch account_id from Telegram.")
        except Exception as e:
            self.logger.error(f"Error updating account_id from Telegram: {e}")
            raise

    @staticmethod
    def estimate_reading_time(text:str, wpm=None) -> float:
        """
        Estimate the reading time for a given text in seconds. It uses a statistical model to predict reading speed.
        """
        from scipy.stats import skewnorm
        from numpy import arange, random as rnd
        try:
            words = len(str(text).split())
            if wpm is None:
                wpm_list = arange(160, 301, dtype=int)
                wpm_distribution = skewnorm.pdf(wpm_list, loc=230, scale=30, a=0)
                wpm_distribution = wpm_distribution / wpm_distribution.max()
                probs = wpm_distribution / wpm_distribution.sum()
                wpm = rnd.choice(wpm_list, p=probs, size=1)[0]
            return round(float(words / wpm * 60), 3)
        except Exception as e:
            raise ValueError(f"Error estimating reading time: {e}")

    # Basic actions

    async def _react(self, message, target_chat):
        await self.ensure_connected()
        if config.get('delays', {}).get('humanisation_level', 1) >= 1:  # If humanisation level is 1 it should consider reading time
            msg_content = await self.get_message_content(chat_id=target_chat.id if hasattr(target_chat, 'id') else target_chat, message_id=message.id)
            if not msg_content:
                self.logger.warning("Message content is empty, skipping reaction.")
                return
            reading_time = self.estimate_reading_time(msg_content)
            self.logger.debug(f"Estimated reading time: {reading_time} seconds")
            await asyncio.sleep(reading_time)

        emoticon = random.choice(self.active_emoji_palette)

        await asyncio.sleep(random.uniform(0.5, 2))  # Simulate human-like delay and prevent spam

        await self.client(SendReactionRequest(
            peer=target_chat,
            msg_id=message.id,
            reaction=[types.ReactionEmoji(emoticon=emoticon)],
            add_to_recent=True
        ))

    async def _comment(self, message, target_chat, content):
        await self.ensure_connected()
        if config.get('delays', {}).get('humanisation_level', 1) >= 1:  # If humanisation level is 1 it should consider reading time
            msg_content = await self.get_message_content(chat_id=target_chat.id if hasattr(target_chat, 'id') else target_chat, message_id=message.id)
            reading_time = self.estimate_reading_time(msg_content)
            self.logger.info(f"Estimated reading time: {reading_time} seconds")
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

    async def _undo_reaction(self, message, target_chat):
        await self.ensure_connected()
        await asyncio.sleep(random.uniform(0.5, 1))  # Prevent spam if everything is broken
        await self.client(SendReactionRequest(
            peer=target_chat,
            msg_id=message.id,
            reaction=[],  # Empty list removes reaction
            add_to_recent=False
        ))

    async def _undo_comment(self, message, target_chat):
        """
        Deletes all user comments on given post.
        """
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

    async def get_message_ids(self, link):
        """
        Extract integer chat_id and message_id from a Telegram message link.
        Returns (int chat_id, int message_id).
        Example link: https://t.me/c/123456789/12345 or https://t.me/username/12345
        """
        try:
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
                # For public, chat_part is username, need to resolve to int id
                await self.ensure_connected()
                entity = await self.client.get_entity(chat_part)
                chat_id = entity.id

            self.logger.debug(f"Extracted chat_id {chat_id} and message_id {message_id} from link")
            return chat_id, message_id
        except Exception as e:
            self.logger.warning(f"Error extracting message IDs from link: {e}")
            raise

    # Actions

    async def undo_reaction(self, message_id: int=None, chat_id: str=None, message_link:str=None):
        retries = config.get('delays', {}).get('action_retries', 3)
        delay = config.get('delays', {}).get('action_retry_delay', 3)
        attempt = 0
        while attempt < retries:
            try:
                if message_link:
                    chat_id, message_id = await self.get_message_ids(message_link)
                    entity = await self.client.get_entity(chat_id)
                    message = await self.client.get_messages(entity, ids=message_id)
                else:
                    entity = await self.client.get_entity(chat_id)
                    message = await self.client.get_messages(entity, ids=message_id)
                await self._undo_reaction(message, entity)
                self.logger.info("Reaction removed successfully")
                return
            except Exception as e:
                attempt += 1
                self.logger.warning(f"Undo reaction failed (attempt {attempt}/{retries}): {e}")
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
                    chat_id, message_id = await self.get_message_ids(message_link)
                    entity = await self.client.get_entity(chat_id)
                    message = await self.client.get_messages(entity, ids=message_id)
                else:
                    entity = await self.client.get_entity(chat_id)
                    message = await self.client.get_messages(entity, ids=message_id)
                await self._undo_comment(message, entity)
                self.logger.info(f"Comment {message} deleted successfully!")
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
                    chat_id, message_id = await self.get_message_ids(message_link)
                    entity = await self.client.get_entity(chat_id)
                    message = await self.client.get_messages(entity, ids=message_id)
                else:
                    entity = await self.client.get_entity(chat_id)
                    message = await self.client.get_messages(entity, ids=message_id)
                await self._react(message, entity)
                self.logger.info("Reaction added successfully")
                return
            except Exception as e:
                attempt += 1
                self.logger.warning(f"React failed (attempt {attempt}/{retries}): {e}")
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
                    chat_id, message_id = await self.get_message_ids(message_link)
                    entity = await self.client.get_entity(chat_id)
                    message = await self.client.get_messages(entity, ids=message_id)
                else:
                    entity = await self.client.get_entity(chat_id)
                    message = await self.client.get_messages(entity, ids=message_id)
                await self._comment(message=message, target_chat=entity, content=content)
                self.logger.info("Comment added successfully!")
                return
            except Exception as e:
                attempt += 1
                self.logger.warning(f"Comment failed (attempt {attempt}/{retries}): {e}")
                if attempt < retries:
                    await asyncio.sleep(delay)
                else:
                    raise



