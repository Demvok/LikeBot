from pandas import Timestamp
import asyncio
import datetime

from telethon import errors

from utils.logger import load_config
from main_logic.channel import normalize_chat_id

config = load_config()


class Post:

    def __init__(self, message_link:str, post_id:int=None, chat_id:int=None, message_id:int=None, created_at=None, updated_at=None):
        self.post_id = post_id
        self.chat_id = normalize_chat_id(chat_id) if chat_id else None
        self.message_id = message_id
        self.message_link = message_link
        self.created_at = created_at or Timestamp.now()
        self.updated_at = updated_at or Timestamp.now()

    def __repr__(self):
        return f"Post({self.post_id if self.post_id else 'unassigned'}, {'validated' if self.is_validated else 'unvalidated'}, {self.message_link})"

    @property
    def is_validated(self):
        """Check if the post has been validated by checking chat_id and message_id, and updated within 1 day."""
        if self.chat_id is None or self.message_id is None:
            return False
        # Check if updated_at is within 1 day from now
        now = Timestamp.now()
        if isinstance(self.updated_at, Timestamp):
            delta = now - self.updated_at
            return delta.days <= 1
        elif isinstance(self.updated_at, str):
            delta = now - Timestamp(self.updated_at)
            return delta.days <= 1
        elif isinstance(self.updated_at, datetime.datetime):
            delta = now.to_pydatetime() - self.updated_at
            return delta.days <= 1
        return False

    def to_dict(self):
        """Convert Post object to dictionary with serializable timestamps."""
        return {
            'post_id': self.post_id,
            'chat_id': self.chat_id,
            'message_id': self.message_id,
            'message_link': self.message_link,
            'is_validated': self.is_validated,
            'created_at': self.created_at.isoformat() if isinstance(self.created_at, Timestamp) else self.created_at,
            'updated_at': self.updated_at.isoformat() if isinstance(self.updated_at, Timestamp) else self.updated_at
        }

    @classmethod
    def from_keys(cls, message_link:str, post_id:int=None, chat_id:int=None, message_id:int=None):
        """Create a Post object from keys."""
        return cls(
            post_id=post_id,
            message_link=message_link,
            chat_id=chat_id,
            message_id=message_id
        )



    async def validate(self, client, logger=None):
        """Validate the post by fetching its chat_id and message_id, and update the record in file."""
        from main_logic.database import get_db
        db = get_db()
        retries = config.get('delays', {}).get('action_retries', 5)
        delay = config.get('delays', {}).get('action_retry_delay', 3)
        attempt = 0
        while attempt < retries:
            try:
                chat_id, message_id, _ = await client.get_message_ids(self.message_link)
                self.chat_id = normalize_chat_id(chat_id)
                self.message_id = message_id
                self.updated_at = Timestamp.now()
                await db.update_post(self.post_id, {
                    'chat_id': self.chat_id,
                    'message_id': self.message_id,
                    'updated_at': str(self.updated_at)
                })
                break  # If you got here - task succeeded
            except Exception as e:
                attempt += 1
                if attempt < retries:
                    await asyncio.sleep(delay)
                elif logger:
                    logger.error(f"Failed to validate post {self.post_id} after {retries} attempts. Error: {e}")
                    raise
        return self
    
    @classmethod
    async def mass_validate_posts(cls, posts, clients, logger=None, max_clients_per_post=3):
        """Validate multiple posts asynchronously, trying multiple clients if one fails."""
        if logger:
            logger.info(f"Validating {len(posts)} posts...")
        if not posts:
            if logger:
                logger.warning("No posts to validate.")
            return []
        if not isinstance(posts, list):
            raise ValueError("Posts should be a list of Post objects.")
        if not clients:
            raise ValueError("No clients provided for validation.")
        
        # Ensure clients is a list
        if not isinstance(clients, list):
            clients = [clients]
        
        from main_logic.database import get_db
        db = get_db()
        already_validated, newly_validated, failed_validation = 0, 0, 0
        new_posts = []
        
        for post in posts:
            try:               
                if post.is_validated:
                    already_validated += 1
                    new_posts.append(post)
                    continue
                
                # Try validation with limited number of clients.
                # Skip any clients that are currently non-usable (status changed while running)
                validation_succeeded = False
                last_error = None
                usable_clients = [c for c in clients if getattr(c, 'account', None) and c.account.is_usable()]
                if not usable_clients:
                    # Nothing to try - all clients are non-usable
                    if logger:
                        logger.error(f"No usable clients available to validate post {post.post_id}.")
                    raise ValueError(f"No usable clients available to validate post {post.post_id}.")

                clients_to_try = min(max_clients_per_post, len(usable_clients))

                for client_idx, client in enumerate(usable_clients[:clients_to_try]):
                    try:
                        if logger:
                            logger.debug(f"Attempting to validate post {post.post_id} with client {client_idx + 1}/{clients_to_try}")
                        
                        await post.validate(client=client, logger=logger)
                        new_post = await db.get_post(post.post_id)
                        new_posts.append(new_post)

                        if new_post.is_validated:
                            newly_validated += 1
                            validation_succeeded = True
                            if logger:
                                logger.debug(f"Post {post.post_id} validated successfully with client {client_idx + 1}")
                            break
                        else:
                            if logger:
                                logger.warning(f"Post {post.post_id} validation returned but not validated with client {client_idx + 1}")
                    
                    except (errors.AuthKeyUnregisteredError, errors.AuthKeyInvalidError, errors.SessionRevokedError) as auth_error:
                        last_error = auth_error
                        if logger:
                            logger.error(f"Client {client.phone_number} has invalid/expired session while validating post {post.post_id}: {auth_error}")
                        # Use centralized mapping to decide action and status
                        from auxilary_logic.telethon_error_handler import map_telethon_exception
                        mapping = map_telethon_exception(auth_error)
                        try:
                            if mapping.get('status'):
                                await client.account.update_status(mapping['status'], error=auth_error)
                                if logger:
                                    logger.info(f"Marked account {client.phone_number} as {mapping['status']}")
                        except Exception as update_error:
                            if logger:
                                logger.warning(f"Failed to update account status for {client.phone_number}: {update_error}")
                        # Try next client
                        continue
                    
                    except errors.UserDeactivatedBanError as ban_error:
                        last_error = ban_error
                        if logger:
                            logger.error(f"Client {client.phone_number} is banned: {ban_error}")
                        from auxilary_logic.telethon_error_handler import map_telethon_exception
                        mapping = map_telethon_exception(ban_error)
                        try:
                            if mapping.get('status'):
                                await client.account.update_status(mapping['status'], error=ban_error)
                                if logger:
                                    logger.info(f"Marked account {client.phone_number} as {mapping['status']}")
                        except Exception as update_error:
                            if logger:
                                logger.warning(f"Failed to update account status for {client.phone_number}: {update_error}")
                        # Try next client
                        continue
                    
                    except errors.PhoneNumberBannedError as ban_error:
                        last_error = ban_error
                        if logger:
                            logger.error(f"Client {client.phone_number} phone number is banned: {ban_error}")
                        from auxilary_logic.telethon_error_handler import map_telethon_exception
                        mapping = map_telethon_exception(ban_error)
                        try:
                            if mapping.get('status'):
                                await client.account.update_status(mapping['status'], error=ban_error)
                                if logger:
                                    logger.info(f"Marked account {client.phone_number} as {mapping['status']}")
                        except Exception as update_error:
                            if logger:
                                logger.warning(f"Failed to update account status for {client.phone_number}: {update_error}")
                        # Try next client
                        continue
                    
                    except errors.RPCError as rpc_error:
                        last_error = rpc_error
                        if logger:
                            logger.warning(f"Telegram error validating post {post.post_id} with client {client.phone_number}: {rpc_error}")
                        # Try next client
                        continue
                    
                    except Exception as client_error:
                        last_error = client_error
                        if logger:
                            logger.warning(f"Error validating post {post.post_id} with client {client.phone_number}: {client_error}")
                        # Try next client
                        continue
                
                # If all attempted clients failed, add to failed count and raise the last error
                if not validation_succeeded:
                    failed_validation += 1
                    error_type = type(last_error).__name__ if last_error else "Unknown"
                    if logger:
                        logger.error(f"Post {post.post_id} failed validation with {clients_to_try} clients. Last error ({error_type}): {last_error}")
                    
                    # Provide more helpful error message based on error type
                    if isinstance(last_error, (errors.AuthKeyUnregisteredError, errors.SessionRevokedError)):
                        raise ValueError(f"Post {post.post_id} validation failed: All {clients_to_try} client sessions are invalid/expired or revoked. Please re-login accounts.")
                    else:
                        raise last_error if last_error else ValueError(f"Post {post.post_id} failed validation with {clients_to_try} clients")

            except Exception as e:
                if logger:
                    logger.error(f"Exception during validation of post {getattr(post, 'post_id', None)}: {e}")
                raise
        
        if logger:
            logger.info(f"Validated {len(posts)} posts: {newly_validated} newly validated, {already_validated} already validated, {failed_validation} failed validation.")
        
        return new_posts
