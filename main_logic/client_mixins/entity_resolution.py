"""
Entity resolution mixin for Telegram client.

Handles resolution of usernames, chat IDs, and message links to Telegram entities.
Provides caching and optimization for entity lookups.
"""

import asyncio
from typing import Optional, TYPE_CHECKING
from urllib.parse import urlparse, unquote
from telethon import errors, functions
from main_logic.channel import normalize_chat_id
from auxilary_logic.humaniser import rate_limiter
from utils.retry import async_retry

if TYPE_CHECKING:
    from main_logic.post import Post


class EntityResolutionMixin:
    """Handles entity resolution from various identifier formats."""
    
    def _normalize_url_identifier(self, identifier: str) -> str:
        """
        Normalize a URL identifier to canonical form for comparison.
        Removes @ prefix and converts to lowercase for username matching.
        
        Args:
            identifier: Username or identifier from URL
            
        Returns:
            Normalized identifier
        """
        if not identifier:
            return identifier
        # Remove @ prefix and normalize case for username comparison
        stripped = identifier.strip()
        normalized = stripped.lstrip('@').lower()
        return normalized
    
    def _sanitize_username_identifier(self, identifier: str) -> str:
        """Strip @ prefix while preserving case for Telegram resolution calls."""
        if not identifier:
            return identifier
        stripped = identifier.strip()
        if stripped.startswith('@'):
            return stripped[1:]
        return stripped
    
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
                chat_id = int(f"-100{raw}")
                # For /c/ links, also store the raw number as an alias
                return chat_id
            
            # /s/<username>/<msg> or /<username>/<msg> format - return username
            if segments[0] == 's':
                if len(segments) < 3:
                    raise ValueError(f"Invalid /s/ link: {link}")
                username = segments[1]
            else:
                username = segments[0]

            sanitized_username = self._sanitize_username_identifier(username)
            return sanitized_username
            
        except Exception as e:
            self.logger.warning(f"Error extracting identifier from '{link}': {e}")
            raise
    
    def _get_url_alias_from_link(self, link: str) -> str:
        """
        Extract the URL alias identifier from a Telegram link for storage/lookup.
        This is different from _extract_identifier_from_link in that it returns
        the exact alias string to be stored in database, not for API calls.
        
        For /c/ links: returns the raw numeric part (without -100 prefix)
        For username links: returns normalized username
        
        Args:
            link: Telegram message link
            
        Returns:
            URL alias string for database storage/lookup
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
            
            # /c/<raw>/<msg> format - return raw number as alias
            if segments[0] == 'c':
                if len(segments) < 3:
                    raise ValueError(f"Invalid /c/ link: {link}")
                raw = segments[1]
                if not raw.isdigit():
                    raise ValueError(f"Non-numeric in /c/ link: {link}")
                # Store just the raw number without -100 prefix as the alias
                return raw
            
            # /s/<username>/<msg> or /<username>/<msg> format - return normalized username
            if segments[0] == 's':
                if len(segments) < 3:
                    raise ValueError(f"Invalid /s/ link: {link}")
                username = segments[1]
            else:
                username = segments[0]
            
            return self._normalize_url_identifier(username)
            
        except Exception as e:
            self.logger.warning(f"Error extracting URL alias from '{link}': {e}")
            raise
    
    async def get_message_ids(self, link: Optional[str] = None, post: Optional["Post"] = None):
        """
        Extract (chat_id, message_id, entity) from a Telegram link of types:
        - https://t.me/c/<raw>/<msg>
        - https://t.me/<username>/<msg>
        - https://t.me/s/<username>/<msg>
        - with or without @, with query params
        
        Optimized to check database for cached channel data before making Telegram API calls.
        
        Returns:
            tuple: (chat_id, message_id, entity) where entity is None for /c/ links
                   and the cached entity object for username-based links
        """
        try:
            if link is None and post is not None:
                link = getattr(post, 'message_link', None)
            if not link:
                raise ValueError("Message link or Post with message_link is required.")

            link = link.strip()
            if '://' not in link:
                link = 'https://' + link

            skip_db_lookup = post is not None

            if post is not None:
                chat_id_from_post = getattr(post, 'chat_id', None)
                message_id_from_post = getattr(post, 'message_id', None)
                if getattr(post, 'is_validated', False) and chat_id_from_post is not None and message_id_from_post is not None:
                    try:
                        message_id = int(message_id_from_post)
                    except (TypeError, ValueError):
                        message_id = message_id_from_post
                    normalized_chat_id = normalize_chat_id(chat_id_from_post)
                    self.logger.debug(f"Using validated post data for link {link}: chat_id={normalized_chat_id}, message_id={message_id}")
                    return normalized_chat_id, message_id, None
            
            # First, try to find a stored Post in DB with the same link. If it exists and
            # is already validated, use its chat_id/message_id and skip network resolution.
            if not skip_db_lookup:
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
            
            # NEW: Check if we have this channel cached in DB by URL alias
            # This reduces API calls by ~80% for channels we've seen before
            try:
                from main_logic.database import get_db
                db = get_db()
                
                # Extract the URL alias for database lookup
                url_alias = self._get_url_alias_from_link(link)
                
                # Try to find channel by this alias
                channel = await db.get_channel_by_url_alias(url_alias)
                if channel:
                    # Found cached channel! Use its chat_id without API call
                    self.logger.debug(f"Found channel in DB cache for alias '{url_alias}': chat_id={channel.chat_id}")
                    # For cached channels, we don't have the entity but we have the chat_id
                    # If caller needs entity, they'll fetch it separately
                    return channel.chat_id, message_id, None
                else:
                    self.logger.debug(f"No channel found in DB for alias '{url_alias}', will fetch from API")
            except Exception as db_err:
                # Don't fail on DB errors - just fall through to API call
                self.logger.debug(f"DB channel lookup failed for link '{link}': {db_err}")
            
            # Use _extract_identifier_from_link to get the identifier (username or chat_id)
            # This handles all link formats: /c/, /s/, and direct username
            identifier = self._extract_identifier_from_link(link)
            
            # If identifier is an int (from /c/ links), return immediately without entity
            if isinstance(identifier, int):
                # /c/ link - identifier is already the chat_id
                # Store the raw number (without -100) as an alias for future lookups
                chat_id = identifier
                try:
                    from main_logic.database import get_db
                    db = get_db()
                    url_alias = self._get_url_alias_from_link(link)
                    # Add this alias to the channel if it exists, or we'll create it later
                    await db.add_channel_url_alias(chat_id, url_alias)
                    self.logger.debug(f"Stored URL alias '{url_alias}' for chat_id {chat_id}")
                except Exception as store_err:
                    self.logger.debug(f"Failed to store URL alias for /c/ link: {store_err}")
                
                return chat_id, message_id, None
            
            # Identifier is a username - fetch entity with caching and rate limiting
            await self.ensure_connected()
            
            try:
                entity = await self.get_entity_cached(identifier)
            except (errors.AuthKeyUnregisteredError, errors.AuthKeyInvalidError, errors.SessionRevokedError, errors.UserDeactivatedError, errors.UserDeactivatedBanError) as auth_error:
                # Session is invalid/expired/revoked or account is banned - re-raise for proper handling upstream
                self.logger.error(f"Session invalid/expired or account deactivated while resolving '{identifier}': {auth_error}")
                raise
            except errors.UsernameNotOccupiedError as username_error:
                # Try accessing the channel via link - sometimes accounts need to "join" first
                # This resolves the entity and adds it to the account's dialog list
                self.logger.info(f"Username '{identifier}' not found in cached entities, attempting to access channel via link...")
                try:
                    # Try to access the channel using Telegram's link resolver
                    # This works even if the account hasn't joined the channel
                    # Use the full link to resolve
                    resolved_entity = await self.client(functions.contacts.ResolveUsernameRequest(username=identifier))
                    if resolved_entity and resolved_entity.chats:
                        # Successfully resolved! Get the chat/channel
                        entity = resolved_entity.chats[0]
                        self.logger.info(f"Successfully resolved username '{identifier}' via ResolveUsername")
                    else:
                        raise username_error
                except Exception as resolve_error:
                    # Username genuinely doesn't exist
                    self.logger.warning(f"Username '{identifier}' from link {link} does not exist (deleted, changed, or never existed). Resolve attempt also failed: {resolve_error}")
                    raise ValueError(
                        f"Username '{identifier}' does not exist. "
                        f"The channel may have been deleted, changed its username, or the link is incorrect. "
                        f"Link: {link}"
                    ) from username_error
            except errors.UsernameInvalidError as invalid_error:
                # Username format is invalid
                self.logger.warning(f"Username '{identifier}' from link {link} has invalid format")
                raise ValueError(
                    f"Username '{identifier}' has invalid format. "
                    f"Check the link for typos. Link: {link}"
                ) from invalid_error
            except Exception as resolve_error:
                self.logger.error(f"Failed to resolve username '{identifier}' from link {link}: {resolve_error}")
                raise ValueError(
                    f"Cannot resolve username '{identifier}' from link {link}. "
                    f"Please verify that the link is correct and the channel still exists."
                ) from resolve_error

            chat_id = normalize_chat_id(entity.id)
            
            # Store the URL alias for this channel for future fast lookups
            try:
                from main_logic.database import get_db
                db = get_db()
                url_alias = self._get_url_alias_from_link(link)
                await db.add_channel_url_alias(chat_id, url_alias)
                self.logger.debug(f"Stored URL alias '{url_alias}' for chat_id {chat_id}")
            except Exception as store_err:
                self.logger.debug(f"Failed to store URL alias: {store_err}")
            
            return chat_id, message_id, entity  # Return entity to avoid redundant get_entity call

        except (errors.AuthKeyUnregisteredError, errors.AuthKeyInvalidError, errors.UserDeactivatedError, errors.UserDeactivatedBanError):
            # Re-raise auth errors without wrapping them
            raise
        except Exception as e:
            self.logger.warning(f"Error extracting IDs from '{link}': {e}")
            raise

    @async_retry(
        retries_key='entity_resolution_retries',
        delay_key='entity_resolution_retry_delay',
        retry_exceptions=(errors.RPCError, ConnectionError, asyncio.TimeoutError),
        no_retry_exceptions=(
            errors.AuthKeyUnregisteredError,
            errors.AuthKeyInvalidError,
            errors.SessionRevokedError,
            errors.UserDeactivatedError,
            errors.UserDeactivatedBanError,
            errors.UsernameNotOccupiedError,
            errors.UsernameInvalidError,
        ),
        logger_attr='logger',
    )
    async def _fetch_entity_with_retry(self, identifier):
        """Fetch entity via telegram_cache with configurable retries for transient errors."""
        return await self.telegram_cache.get_entity(identifier, self)

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
        
        return await self._fetch_entity_with_retry(identifier)
    
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
