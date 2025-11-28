"""
Entity resolution mixin for Telegram client.

Handles resolution of usernames, chat IDs, and message links to Telegram entities.
Provides caching and optimization for entity lookups.
"""

from urllib.parse import urlparse, unquote
from telethon import errors
from main_logic.channel import normalize_chat_id
from auxilary_logic.humaniser import rate_limiter


class EntityResolutionMixin:
    """Handles entity resolution from various identifier formats."""
    
    def _extract_identifier_from_link(self, link: str):
        """
        Extract username or chat_id from a Telegram message link.
        Used when we need to fetch an entity from a link.
        
        Args:
            link: Telegram message link
            
        Returns:
            str or int: Username (for public channels) or chat_id (for /c/ links)
        """
        try:
            link = link.strip()
            if '://' not in link:
                link = 'https://' + link
            
            parsed = urlparse(unquote(link))
            path = parsed.path.lstrip('/')
            segments = [seg for seg in path.split('/') if seg != '']
            
            if not segments or len(segments) < 2:
                raise ValueError(f"Link format not recognized: {link}")
            
            # /c/<raw>/<msg> format - return chat_id
            if segments[0] == 'c':
                if len(segments) < 3:
                    raise ValueError(f"Invalid /c/ link: {link}")
                raw = segments[1]
                if not raw.isdigit():
                    raise ValueError(f"Non-numeric in /c/ link: {link}")
                return int(f"-100{raw}")
            
            # /s/<username>/<msg> or /<username>/<msg> format - return username
            if segments[0] == 's':
                if len(segments) < 3:
                    raise ValueError(f"Invalid /s/ link: {link}")
                username = segments[1]
            else:
                username = segments[0]
            
            return username.lstrip('@')
            
        except Exception as e:
            self.logger.warning(f"Error extracting identifier from '{link}': {e}")
            raise
    
    async def get_message_ids(self, link: str):
        """
        Extract (chat_id, message_id, entity) from a Telegram link of types:
        - https://t.me/c/<raw>/<msg>
        - https://t.me/<username>/<msg>
        - https://t.me/s/<username>/<msg>
        - with or without @, with query params
        
        Returns:
            tuple: (chat_id, message_id, entity) where entity is None for /c/ links
                   and the cached entity object for username-based links
        """
        try:
            link = link.strip()
            if '://' not in link:
                link = 'https://' + link
            
            # First, try to find a stored Post in DB with the same link. If it exists and
            # is already validated, use its chat_id/message_id and skip network resolution.
            try:
                from main_logic.database import get_db
                db = get_db()

                try:
                    post_obj = await db.get_post_by_link(link)
                    if post_obj and getattr(post_obj, 'is_validated', False):
                        # Ensure both ids exist and are integers
                        if post_obj.chat_id is not None and post_obj.message_id is not None:
                            try:
                                chat_id_db = int(post_obj.chat_id)
                                message_id_db = int(post_obj.message_id)
                                self.logger.debug(f"Found validated post in DB for link {link}: chat_id={chat_id_db}, message_id={message_id_db}")
                                return chat_id_db, message_id_db, None  # No entity for DB cached posts
                            except Exception:
                                self.logger.debug(f"DB post for link {link} had non-integer ids, falling back to resolution")
                except Exception as _db_err:
                    # Do not fail on DB errors; fall back to Telethon resolution
                    self.logger.debug(f"DB lookup by message_link failed for '{link}': {_db_err}")
            except Exception:
                pass
            
            # Parse link to extract message_id
            parsed = urlparse(unquote(link))
            path = parsed.path.lstrip('/')
            segments = [seg for seg in path.split('/') if seg != '']
            if not segments or len(segments) < 2:
                raise ValueError(f"Link format not recognized: {link}")

            # Extract message_id from the last segment
            # For /c/<raw>/<msg>, /s/<username>/<msg>, /<username>/<msg>
            msg = segments[-1]
            if not msg.isdigit():
                raise ValueError(f"Message part is not numeric: {link}")
            message_id = int(msg)
            
            # Use _extract_identifier_from_link to get the identifier (username or chat_id)
            # This handles all link formats: /c/, /s/, and direct username
            identifier = self._extract_identifier_from_link(link)
            
            # If identifier is an int (from /c/ links), return immediately without entity
            if isinstance(identifier, int):
                # /c/ link - identifier is already the chat_id
                return identifier, message_id, None
            
            # Identifier is a username - fetch entity with caching and rate limiting
            await self.ensure_connected()
            
            try:
                entity = await self.get_entity_cached(identifier)
            except (errors.AuthKeyUnregisteredError, errors.AuthKeyInvalidError, errors.SessionRevokedError, errors.UserDeactivatedError, errors.UserDeactivatedBanError) as auth_error:
                # Session is invalid/expired/revoked or account is banned - re-raise for proper handling upstream
                self.logger.error(f"Session invalid/expired or account deactivated while resolving '{identifier}': {auth_error}")
                raise
            except Exception as e1:
                # Try several fallbacks: full URL with scheme, http, www variant, and @username.
                last_exc = e1
                tried = []
                candidates = [
                    f"https://{parsed.netloc}/{identifier}",
                    f"http://{parsed.netloc}/{identifier}",
                    f"{parsed.netloc}/{identifier}",
                    f"@{identifier}",
                ]
                entity = None
                for candidate in candidates:
                    tried.append(candidate)
                    try:
                        entity = await self.get_entity_cached(candidate)
                        break
                    except (errors.AuthKeyUnregisteredError, errors.AuthKeyInvalidError, errors.SessionRevokedError, errors.UserDeactivatedError, errors.UserDeactivatedBanError) as auth_error:
                        # Re-raise auth related errors immediately so they can be handled upstream
                        self.logger.error(f"Session invalid/expired or account deactivated while resolving '{identifier}' using '{candidate}': {auth_error}")
                        raise
                    except Exception as e2:
                        last_exc = e2
                        self.logger.debug(f"get_entity failed for candidate '{candidate}': {e2}")

                if entity is None:
                    self.logger.error(f"Failed to resolve username '{identifier}' from link {link}. Tried: {tried}. Errors: {e1}, last: {last_exc}")
                    raise ValueError(f"Cannot resolve username '{identifier}' from link {link}")

            chat_id = normalize_chat_id(entity.id)
            return chat_id, message_id, entity  # Return entity to avoid redundant get_entity call

        except (errors.AuthKeyUnregisteredError, errors.AuthKeyInvalidError, errors.UserDeactivatedError, errors.UserDeactivatedBanError):
            # Re-raise auth errors without wrapping them
            raise
        except Exception as e:
            self.logger.warning(f"Error extracting IDs from '{link}': {e}")
            raise

    async def get_entity_cached(self, identifier):
        """
        Get entity with caching and rate limiting using task-scoped cache.
        
        Args:
            identifier: Can be username, user_id, or other entity identifier
            
        Returns:
            Entity object from Telegram
            
        Raises:
            RuntimeError: If telegram_cache not injected (client used outside task context)
            Exception: Any Telegram API errors during entity fetch
            
        Note:
            For debugging/testing outside Task context, call init_standalone_cache() first.
        """
        if self.telegram_cache is None:
            # Client being used outside task context - provide helpful error
            self.logger.error(f"get_entity_cached called without telegram_cache for {identifier}")
            raise RuntimeError(
                f"Client.telegram_cache not initialized. "
                f"This client must be used within a Task context that injects the cache. "
                f"For debugging/testing, call client.init_standalone_cache() first, "
                f"or use client.client.get_entity() directly for uncached access."
            )
        
        return await self.telegram_cache.get_entity(identifier, self)
    
    async def get_message_content(self, chat_id=None, message_id=None, message_link=None) -> str | None:
        """
        Retrieve the content of a single message by chat and message_id.
        
        Args:
            chat_id: Chat/channel ID
            message_id: Message ID
            message_link: Alternative to chat_id/message_id - message link
        
        Returns:
            Message text content or None
        """
        try:
            await self.ensure_connected()
            if message_link and not (message_id and chat_id):
                entity, message = await self._get_message_ids(message_link)
                return message.message if message else None
            else:
                if not chat_id or not message_id:
                    raise ValueError("Either message_link or both chat_id and message_id must be provided.")

            # Use cached get_entity with rate limiting
            entity = await self.get_entity_cached(chat_id)
            # Rate limit message fetching
            await rate_limiter.wait_if_needed('get_messages')
            message = await self.client.get_messages(entity, ids=message_id)
            return message.message if message else None
        except Exception as e:
            self.logger.warning(f"Error retrieving message content: {e}")
            raise
