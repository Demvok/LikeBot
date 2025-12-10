"""
Actions mixin for Telegram client.

Handles core Telegram actions: reactions, comments, and undo operations.
Uses humanization helpers from auxilary_logic for realistic behavior simulation.
"""

import random
import asyncio
from typing import Optional, TYPE_CHECKING, List
from telethon.tl.functions.messages import SendReactionRequest, GetMessagesViewsRequest
from telethon import functions, types, errors

from main_logic.channel import normalize_chat_id, Channel
from auxilary_logic.humaniser import rate_limiter, apply_reading_delay, apply_pre_action_delay, apply_anti_spam_delay
from utils.logger import load_config

config = load_config()

if TYPE_CHECKING:
    from main_logic.post import Post


class ActionsMixin:
    """
    Handles Telegram actions: reactions, comments, undo operations.
    Uses EntityResolutionMixin and ChannelDataMixin functionality.
    """

    async def _random_delay(self, min_delay: float = 0.15, max_delay: float = 0.45) -> None:
        """Sleep for a short random interval to mimic human delays."""
        if max_delay <= min_delay:
            max_delay = min_delay + 0.05
        await asyncio.sleep(random.uniform(min_delay, max_delay))

    async def _fetch_full_channel_snapshot(self, input_peer) -> None:
        """Warm up channel context with GetFullChannel before acting."""
        try:
            await self.client(functions.channels.GetFullChannelRequest(channel=input_peer))
            await self._random_delay(0.05, 0.2)
        except errors.RPCError as exc:
            self.logger.debug(f"GetFullChannelRequest failed: {exc}")
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.debug(f"Unexpected GetFullChannelRequest issue: {exc}")

    async def _maybe_fetch_neighbor_posts(self, input_peer, message_id: int) -> None:
        """Optionally load surrounding posts via history or direct message fetch."""
        if random.random() < 0.2:
            self.logger.debug("Skipping neighbor history fetch for this iteration")
            return

        await self._random_delay(0.05, 0.25)
        neighbor_ids = {max(message_id + delta, 1) for delta in (-1, 0, 1)}
        try:
            if random.random() < 0.5:
                offset_id = max(message_id + random.choice([-1, 0, 1]), 1)
                await self.client(functions.messages.GetHistoryRequest(
                    peer=input_peer,
                    offset_id=offset_id,
                    offset_date=None,
                    add_offset=-1,
                    limit=len(neighbor_ids) + 1,
                    max_id=0,
                    min_id=0,
                    hash=0
                ))
            else:
                message_refs = [types.InputMessageID(id=mid) for mid in sorted(neighbor_ids)]
                await self.client(functions.messages.GetMessagesRequest(id=message_refs))
            await self._random_delay(0.05, 0.15)
        except errors.RPCError as exc:
            self.logger.debug(f"Neighbor history fetch failed: {exc}")
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.debug(f"Unexpected neighbor fetch issue: {exc}")

    async def _prefetch_media_payload(self, message) -> None:
        """Prime message media or previews before interacting."""
        await self._random_delay(0.05, 0.2)
        try:
            prefer_preview = random.random() < 0.25
            text_content = (message.message or "").strip() if hasattr(message, "message") else ""

            if prefer_preview and text_content:
                await self.client(functions.messages.GetWebPagePreviewRequest(message=text_content))
            else:
                # Telegram API does not expose a standalone GetMessageMedia, so fetch via GetMessages.
                await self.client(functions.messages.GetMessagesRequest(
                    id=[types.InputMessageID(id=message.id)]
                ))
        except errors.RPCError as exc:
            self.logger.debug(f"Preloading media/preview failed: {exc}")
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.debug(f"Unexpected media preload issue: {exc}")

    async def _maybe_open_replies(self, input_peer, message_id: int) -> None:
        """With a tiny probability, open replies to mimic organic exploration."""
        chance = random.randint(1, 5) / 100.0
        if random.random() >= chance:
            return

        await self._random_delay(0.05, 0.15)
        try:
            discussion = await self.client(functions.messages.GetDiscussionMessageRequest(
                peer=input_peer,
                msg_id=message_id
            ))
            if not discussion.messages:
                return

            discussion_peer = discussion.chats[0] if discussion.chats else input_peer
            top_message_id = discussion.messages[0].id
            limit = random.randint(5, 15)
            await self.client(functions.messages.GetRepliesRequest(
                peer=discussion_peer,
                msg_id=top_message_id,
                offset_id=0,
                offset_date=None,
                add_offset=0,
                limit=limit,
                max_id=0,
                min_id=0,
                hash=0
            ))
            await self._random_delay(0.05, 0.2)
        except errors.RPCError as exc:
            self.logger.debug(f"Optional replies fetch failed: {exc}")
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.debug(f"Unexpected replies fetch issue: {exc}")

    async def _prepare_message_context(self, input_peer, message) -> None:
        """Aggregate all lightweight warm-up requests before the actual action."""
        await self._fetch_full_channel_snapshot(input_peer)
        await self._maybe_fetch_neighbor_posts(input_peer, message.id)
        await self._prefetch_media_payload(message)
        await self._maybe_open_replies(input_peer, message.id)

    async def _gather_reaction_whitelist(self, input_peer, message_id: int) -> Optional[List[str]]:
        """Fetch existing reactions to refine emoji selection."""
        try:
            await self._random_delay(0.05, 0.15)
            reaction_list = await self.client(functions.messages.GetMessageReactionsListRequest(
                peer=input_peer,
                id=message_id,
                limit=64,
                offset=None
            ))
        except errors.RPCError as exc:
            self.logger.debug(f"GetMessageReactionsListRequest failed: {exc}")
            return None
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.debug(f"Unexpected reaction list issue: {exc}")
            return None

        pool: List[str] = []
        available = getattr(reaction_list, 'available_reactions', None)
        if available:
            for reaction in available:
                emoji = getattr(reaction, 'reaction', None)
                emoticon = getattr(reaction, 'emoticon', None)
                value = emoji or emoticon
                if isinstance(value, str):
                    pool.append(value)

        if not pool and hasattr(reaction_list, 'reactions'):
            for reaction in getattr(reaction_list, 'reactions', []):
                emoticon = getattr(reaction, 'reaction', None)
                if isinstance(emoticon, types.ReactionEmoji):
                    pool.append(emoticon.emoticon)

        # Deduplicate while preserving order
        deduped = list(dict.fromkeys([emoji for emoji in pool if emoji]))
        return deduped or None
    
    async def _react(self, message, target_chat, channel: Channel = None):
        """
        React to a message with an emoji from the active palette.
        
        Args:
            message: Telethon message object
            target_chat: Target chat entity
            channel: Optional Channel object with metadata
        
        Raises:
            ValueError: If no valid emojis are available after filtering
        """
        await self.ensure_connected()
        
        # Convert entity to InputPeer with caching
        if self.telegram_cache is not None:
            input_peer = await self.telegram_cache.get_input_peer(target_chat, self)
        else:
            input_peer = await self.client.get_input_entity(target_chat)
        
        # Check subscription status and warn if not subscribed
        chat_id = normalize_chat_id(target_chat.id if hasattr(target_chat, 'id') else target_chat)
        is_subscribed = await self._check_subscription(chat_id)
        
        if not is_subscribed:
            self.logger.warning(
                f"⚠️  DANGER: Account {self.phone_number} is NOT subscribed to channel {chat_id}. "
                f"Reacting to posts from unsubscribed channels significantly increases ban risk. "
                f"Telegram may flag this as spam behavior."
            )
        
        await self._prepare_message_context(input_peer, message)

        await self.client(GetMessagesViewsRequest(
            peer=input_peer,
            id=[message.id],
            increment=True
        ))

        # Apply reading delay using humaniser helper
        msg_content = message.message if hasattr(message, 'message') else None
        await apply_reading_delay(msg_content, self.logger)

        # Check for active emoji palette
        if not self.active_emoji_palette:
            error_msg = "No emoji palette configured for this client. Palette must be set before reacting."
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        # Get allowed reactions from message (already fetched - use it directly!)
        allowed_reactions = None
        try:
            # Use the message object we already have instead of fetching again
            if hasattr(message, 'reactions') and message.reactions:
                # Check available_reactions if it exists (Telegram's list of allowed emojis)
                if hasattr(message.reactions, 'available_reactions'):
                    available_reactions_list = []
                    for reaction in message.reactions.available_reactions:
                        if hasattr(reaction, 'emoticon'):
                            available_reactions_list.append(reaction.emoticon)
                    
                    if available_reactions_list:
                        self.logger.debug(f"Message has restricted reactions: {available_reactions_list}")
                        # If available_reactions exists, it means only these are allowed
                        allowed_reactions = available_reactions_list
            
            if not allowed_reactions:
                self.logger.debug("Message has no reaction restrictions - will try palette emojis")
                
        except Exception as e:
            self.logger.warning(f"Could not fetch message reactions metadata: {e}. Will try palette emojis.")

        reaction_whitelist = await self._gather_reaction_whitelist(input_peer, message.id)
        if reaction_whitelist:
            if allowed_reactions:
                merged = [emoji for emoji in reaction_whitelist if emoji in allowed_reactions]
                allowed_reactions = merged or reaction_whitelist
            else:
                allowed_reactions = reaction_whitelist
            self.logger.debug(f"Reaction whitelist after MessageReactionList check: {allowed_reactions}")
        
        # Filter palette based on allowed reactions (only if explicitly restricted)
        if allowed_reactions:
            # Filter to only emojis that are in the allowed list
            filtered_palette = [emoji for emoji in self.active_emoji_palette if emoji in allowed_reactions]
            
            if not filtered_palette:
                # None of our palette emojis are in the allowed reactions
                error_msg = f"None of the palette emojis {self.active_emoji_palette} are in allowed reactions {allowed_reactions}"
                self.logger.error(error_msg)
                raise ValueError(error_msg)
            
            self.logger.info(f"Filtered palette from {len(self.active_emoji_palette)} to {len(filtered_palette)} emojis based on allowed reactions")
        else:
            # No explicit restrictions - use full palette and rely on try-catch
            filtered_palette = self.active_emoji_palette.copy()
            self.logger.debug(f"Using full palette ({len(filtered_palette)} emojis) - will try until one works")
        
        if not filtered_palette:
            error_msg = f"No valid emojis available after filtering. Palette: {self.active_emoji_palette}, Allowed: {allowed_reactions}"
            self.logger.error(error_msg)
            raise ValueError(error_msg)
        
        # Apply pre-action delay using humaniser helper
        await apply_pre_action_delay(self.logger)
        
        # Try to send reaction
        if self.palette_ordered:
            # Ordered mode: try emojis in sequence until one succeeds
            last_error = None
            for idx, emoticon in enumerate(filtered_palette, 1):
                try:
                    self.logger.debug(f"Attempting emoji (ordered, rank {idx}/{len(filtered_palette)}): {emoticon}")
                    # Rate limit reaction sending
                    await rate_limiter.wait_if_needed('send_reaction')
                    await self.client(SendReactionRequest(
                        peer=input_peer,
                        msg_id=message.id,
                        reaction=[types.ReactionEmoji(emoticon=emoticon)],
                        add_to_recent=True
                    ))
                    self.logger.info(f"Successfully reacted with {emoticon} (rank {idx}/{len(filtered_palette)})")
                    return  # Success - exit method
                except errors.ReactionInvalidError as e:
                    # This emoji is not allowed, try next one
                    self.logger.warning(f"Emoji {emoticon} not allowed (rank {idx}/{len(filtered_palette)}): {e}")
                    last_error = e
                    if idx < len(filtered_palette):
                        continue  # Try next emoji
                    else:
                        # No more emojis to try
                        error_msg = f"All {len(filtered_palette)} emojis failed. Last error: {last_error}"
                        self.logger.error(error_msg)
                        raise ValueError(error_msg)
                except Exception as e:
                    # Other error - don't retry, raise immediately
                    error_msg = f"Failed to send reaction {emoticon} to message {message.id}: {e}"
                    self.logger.error(error_msg)
                    raise RuntimeError(error_msg)
        else:
            # Random mode: try random emojis until one succeeds or all fail
            # Shuffle palette to try in random order
            shuffled_palette = filtered_palette.copy()
            random.shuffle(shuffled_palette)
            
            last_error = None
            for idx, emoticon in enumerate(shuffled_palette, 1):
                try:
                    self.logger.debug(f"Attempting emoji (random, attempt {idx}/{len(shuffled_palette)}): {emoticon}")
                    # Rate limit reaction sending
                    await rate_limiter.wait_if_needed('send_reaction')
                    await self.client(SendReactionRequest(
                        peer=input_peer,
                        msg_id=message.id,
                        reaction=[types.ReactionEmoji(emoticon=emoticon)],
                        add_to_recent=True
                    ))
                    self.logger.info(f"Successfully reacted with {emoticon} (attempt {idx}/{len(shuffled_palette)})")
                    return  # Success - exit method
                except errors.ReactionInvalidError as e:
                    # This emoji is not allowed, try another random one
                    self.logger.warning(f"Emoji {emoticon} not allowed (attempt {idx}/{len(shuffled_palette)}): {e}")
                    last_error = e
                    if idx < len(shuffled_palette):
                        continue  # Try next random emoji
                    else:
                        # No more emojis to try
                        error_msg = f"All {len(shuffled_palette)} emojis failed. Last error: {last_error}"
                        self.logger.error(error_msg)
                        raise ValueError(error_msg)
                except Exception as e:
                    # Other error - don't retry, raise immediately
                    error_msg = f"Failed to send reaction {emoticon} to message {message.id}: {e}"
                    self.logger.error(error_msg)
                    raise RuntimeError(error_msg)

    async def _comment(self, message, target_chat, content, channel: Channel = None):
        """Comment on a message in a channel's discussion group."""
        await self.ensure_connected()
        
        # Convert entity to InputPeer with caching
        if self.telegram_cache is not None:
            input_peer = await self.telegram_cache.get_input_peer(target_chat, self)
        else:
            input_peer = await self.client.get_input_entity(target_chat)
        
        # Check subscription requirements for commenting
        chat_id = normalize_chat_id(target_chat.id if hasattr(target_chat, 'id') else target_chat)
        is_subscribed_to_channel = await self._check_subscription(chat_id)
        
        # If not subscribed to channel
        if not is_subscribed_to_channel:
            # Check if channel is private
            if channel and channel.is_private:
                error_msg = (
                    f"Cannot comment on private channel {chat_id}: "
                    f"account {self.phone_number} is not subscribed to this channel. "
                    f"Private channels require subscription to comment."
                )
                self.logger.error(error_msg)
                raise ValueError(error_msg)
            
            # Channel is public - check discussion group subscription
            if channel and channel.has_discussion_group:
                discussion_chat_id = channel.discussion_chat_id
                is_subscribed_to_discussion = await self._check_subscription(discussion_chat_id)
                
                if not is_subscribed_to_discussion:
                    error_msg = (
                        f"Cannot comment on channel {chat_id}: "
                        f"account {self.phone_number} is not subscribed to the discussion group (chat_id: {discussion_chat_id}). "
                        f"You must subscribe to the discussion group to comment on posts from unsubscribed channels."
                    )
                    self.logger.error(error_msg)
                    raise ValueError(error_msg)
                
                self.logger.info(
                    f"Account {self.phone_number} is not subscribed to channel {chat_id}, "
                    f"but is subscribed to discussion group {discussion_chat_id}. Proceeding with comment."
                )
            else:
                # No discussion group info - warn but proceed
                self.logger.warning(
                    f"Account {self.phone_number} is not subscribed to channel {chat_id}. "
                    f"No discussion group info available. Attempting to comment anyway."
                )

        await self.client(GetMessagesViewsRequest(
            peer=input_peer,
            id=[message.id],
            increment=True
        ))

        # Apply reading delay using humaniser helper
        # Use message object directly instead of fetching again (saves API call)
        msg_content = message.message if hasattr(message, 'message') else None
        await apply_reading_delay(msg_content, self.logger)

        discussion = await self.client(functions.messages.GetDiscussionMessageRequest(
            peer=target_chat,
            msg_id=message.id
        ))
        self.logger.debug(f"Discussion found: {discussion.messages[0].id}")
        
        # Use the discussion message ID, not the original channel message ID
        discussion_message_id = discussion.messages[0].id
        discussion_chat = discussion.chats[0]
        
        # Apply anti-spam delay using humaniser helper
        await apply_anti_spam_delay(self.logger)

        # Rate limit message sending
        await rate_limiter.wait_if_needed('send_message')
        await self.client.send_message(
            entity=discussion_chat,
            message=content,
            reply_to=discussion_message_id  # Use discussion message ID, not original message.id
        )

    async def _undo_reaction(self, message, target_chat):
        """Remove reaction from a message."""
        await self.ensure_connected()
        
        # Convert entity to InputPeer with caching
        if self.telegram_cache is not None:
            input_peer = await self.telegram_cache.get_input_peer(target_chat, self)
        else:
            input_peer = await self.client.get_input_entity(target_chat)
        
        await self.client(GetMessagesViewsRequest(
            peer=input_peer,
            id=[message.id],
            increment=True
        ))
        
        # Apply anti-spam delay using humaniser helper
        await apply_anti_spam_delay(self.logger)
        
        # Rate limit reaction removal
        await rate_limiter.wait_if_needed('send_reaction')
        await self.client(SendReactionRequest(
            peer=input_peer,
            msg_id=message.id,
            reaction=[],  # Empty list removes reaction
            add_to_recent=False
        ))

    async def _undo_comment(self, message, target_chat):
        """Delete all user comments on a given post."""
        await self.ensure_connected()

        # Convert entity to InputPeer with caching
        if self.telegram_cache is not None:
            input_peer = await self.telegram_cache.get_input_peer(target_chat, self)
        else:
            input_peer = await self.client.get_input_entity(target_chat)

        await self.client(GetMessagesViewsRequest(
            peer=input_peer,
            id=[message.id],
            increment=True
        ))

        # Apply anti-spam delay using humaniser helper
        await apply_anti_spam_delay(self.logger)
        
        discussion = await self.client(functions.messages.GetDiscussionMessageRequest(
            peer=input_peer,
            msg_id=message.id
        ))
        discussion_chat = discussion.chats[0]
        
        # Find comments by this user on this discussion
        async for msg in self.client.iter_messages(discussion_chat, reply_to=discussion.messages[0].id, from_user='me'):
            await msg.delete()

# Public action methods

    async def undo_reaction(self, message_link: str = None):
        """Remove reaction from a message."""
        chat_id, message_id, entity = await self.get_message_ids(message_link)
        # Use entity from get_message_ids if available, otherwise fetch it
        if entity is None:
            # Extract username/identifier from link and use that to fetch entity
            identifier = self._extract_identifier_from_link(message_link)
            entity = await self.get_entity_cached(identifier)
        
        # Get or fetch channel data (for consistency, though not strictly needed for undo)
        channel = await self._get_or_fetch_channel_data(chat_id, entity=entity)
        
        # Fetch message with caching
        message = await self.get_message_cached(chat_id, message_id)
        await self._undo_reaction(message, entity)
        self.logger.info("Reaction removed successfully")

    async def undo_comment(self, message_link: str = None):
        """Delete all user comments on a post."""
        chat_id, message_id, entity = await self.get_message_ids(message_link)
        # Use entity from get_message_ids if available, otherwise fetch it
        if entity is None:
            # Extract username/identifier from link and use that to fetch entity
            identifier = self._extract_identifier_from_link(message_link)
            entity = await self.get_entity_cached(identifier)
        
        # Get or fetch channel data (for consistency)
        channel = await self._get_or_fetch_channel_data(chat_id, entity=entity)
        
        # Fetch message with caching
        message = await self.get_message_cached(chat_id, message_id)
        await self._undo_comment(message, entity)
        self.logger.info(f"Comment {message} deleted successfully!")

    async def react(self, message_link: Optional[str] = None, post: Optional["Post"] = None):
        """
        React to a message by link or by a preloaded Post object.
        
        Args:
            message_link: Telegram message link
            post: Optional Post instance with validated chat/message identifiers
        """
        link = message_link or (getattr(post, 'message_link', None) if post else None)
        if not link:
            raise ValueError("Either message_link or post.message_link must be provided for reaction.")

        chat_id, message_id, entity = await self.get_message_ids(link, post=post)
        # Use entity from get_message_ids if available, otherwise fetch it
        if entity is None:
            # Extract username/identifier from link and use that to fetch entity
            # (using raw chat_id doesn't work for channels without access_hash)
            identifier = self._extract_identifier_from_link(link)
            entity = await self.get_entity_cached(identifier)
        
        # Get or fetch channel data (minimizes API calls by reusing entity)
        channel = await self._get_or_fetch_channel_data(chat_id, entity=entity)
        
        # Fetch message with caching
        message = await self.get_message_cached(chat_id, message_id)

        
        await self._react(message, entity, channel=channel)
        self.logger.info("Reaction added successfully")
    
    async def comment(self, content, message_id: int = None, chat_id: str = None, message_link: str = None):
        """
        Comment on a message by its link.
        
        Args:
            content: Comment text content
            message_link: Telegram message link
        """
        chat_id, message_id, entity = await self.get_message_ids(message_link)
        # Use entity from get_message_ids if available, otherwise fetch it
        if entity is None:
            # Extract username/identifier from link and use that to fetch entity
            identifier = self._extract_identifier_from_link(message_link)
            entity = await self.get_entity_cached(identifier)
        
        # Get or fetch channel data (minimizes API calls by reusing entity)
        channel = await self._get_or_fetch_channel_data(chat_id, entity=entity)
        
        # Fetch message with caching
        message = await self.get_message_cached(chat_id, message_id)
        await self._comment(message=message, target_chat=entity, content=content, channel=channel)
        self.logger.info("Comment added successfully!")
