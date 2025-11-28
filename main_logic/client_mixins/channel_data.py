"""
Channel data and subscription mixin for Telegram client.

Handles channel metadata fetching, subscription checking, and account ID updates.
"""

from datetime import datetime, timezone
from telethon import functions
from main_logic.channel import normalize_chat_id, Channel
from auxilary_logic.humaniser import rate_limiter


class ChannelDataMixin:
    """Handles channel data fetching, synchronization, and subscription checking."""
    
    async def _check_subscription(self, chat_id: int) -> bool:
        """
        Check if account is subscribed to a channel.
        
        Args:
            chat_id: Normalized chat ID to check
        
        Returns:
            True if subscribed, False otherwise
        """
        # Check account's subscribed_to list
        if hasattr(self.account, 'subscribed_to') and self.account.subscribed_to:
            is_subscribed = chat_id in self.account.subscribed_to
            self.logger.debug(f"Subscription check for {chat_id}: {is_subscribed}")
            return is_subscribed
        
        self.logger.debug(f"No subscription list available for account, assuming not subscribed to {chat_id}")
        return False
    
    async def _get_or_fetch_channel_data(self, chat_id: int, entity=None):
        """
        Get channel data from database or fetch from Telegram if not exists.
        Minimizes API calls by reusing entity if provided.
        
        Args:
            chat_id: Normalized chat ID
            entity: Optional entity object already fetched (to avoid redundant API calls)
        
        Returns:
            Channel object from database (existing or newly created)
        """
        from main_logic.database import get_db
        
        db = get_db()
        
        # First, check if channel exists in database
        channel = await db.get_channel(chat_id)
        if channel:
            self.logger.debug(f"Channel {chat_id} found in database")
            return channel
        
        # Channel not in DB - fetch from Telegram
        self.logger.info(f"Channel {chat_id} not in database, fetching from Telegram")
        
        await self.ensure_connected()
        
        # Use provided entity or fetch it (with caching)
        if entity is None:
            entity = await self.get_entity_cached(chat_id)
        
        # Extract channel data from entity (same as in fetch_and_update_subscribed_channels)
        channel_data = {
            'chat_id': chat_id,
            'is_private': not getattr(entity, 'username', None),
            'channel_name': getattr(entity, 'title', None),
            'has_enabled_reactions': getattr(entity, 'reactions_enabled', True),
            'tags': []
        }
        
        # Get channel hash for private channels
        if hasattr(entity, 'access_hash') and entity.access_hash:
            channel_data['channel_hash'] = str(entity.access_hash)
        else:
            channel_data['channel_hash'] = ""
        
        # Try to get full channel info for discussion group and reaction settings (with caching)
        try:
            if self.telegram_cache is not None:
                full_channel = await self.telegram_cache.get_full_channel(chat_id, self)
            else:
                await rate_limiter.wait_if_needed('get_entity')
                full_channel = await self.client(functions.channels.GetFullChannelRequest(
                    channel=entity
                ))
            
            # Check for linked discussion group
            if hasattr(full_channel.full_chat, 'linked_chat_id'):
                channel_data['discussion_chat_id'] = full_channel.full_chat.linked_chat_id
            else:
                channel_data['discussion_chat_id'] = None
            
            # Check reaction settings more accurately
            if hasattr(full_channel.full_chat, 'available_reactions'):
                reactions = full_channel.full_chat.available_reactions
                if reactions is None:
                    channel_data['has_enabled_reactions'] = False
                elif hasattr(reactions, 'reactions'):
                    channel_data['has_enabled_reactions'] = len(reactions.reactions) > 0
            
            # Check if reactions are only for subscribers
            channel_data['reactions_only_for_subscribers'] = False
            
        except Exception as e:
            self.logger.warning(f"Could not fetch full channel info for {chat_id}: {e}")
            channel_data['discussion_chat_id'] = None
            channel_data['reactions_only_for_subscribers'] = False
        
        # Add timestamps
        channel_data['created_at'] = datetime.now(timezone.utc)
        channel_data['updated_at'] = datetime.now(timezone.utc)
        
        # Save to database
        try:
            await db.add_channel(channel_data)
            self.logger.info(f"Added new channel to database: {channel_data['channel_name']} ({chat_id})")
        except ValueError:
            # Channel was added by another process (race condition)
            self.logger.debug(f"Channel {chat_id} already exists (race condition), fetching from DB")
            channel = await db.get_channel(chat_id)
            if channel:
                return channel
        
        return Channel.from_dict(channel_data)
    
    async def fetch_and_update_subscribed_channels(self):
        """
        Fetch all channels the account is subscribed to from Telegram,
        update the account's subscribed_to field in database,
        and upsert channel data to the channels collection.
        
        This method minimizes API calls by:
        - Using a single GetDialogsRequest to fetch all channels
        - Batch processing channel data
        - Only fetching full channel details for channels not in DB
        
        Returns:
            List of chat_ids that were added/updated
        """
        from main_logic.database import get_db
        
        db = get_db()
        
        try:
            await self.ensure_connected()
            self.logger.info(f"Fetching subscribed channels for {self.phone_number}")
            
            # Get all dialogs (chats/channels) with a single API call
            # This returns channels, groups, and private chats
            dialogs = await self.client.get_dialogs()
            
            # Filter for channels only (not groups or private chats)
            # Channel types: Channel (broadcast) and Megagroup (discussion-enabled channels)
            channel_dialogs = [
                d for d in dialogs 
                if hasattr(d.entity, 'broadcast') or 
                   (hasattr(d.entity, 'megagroup') and d.entity.megagroup)
            ]
            
            self.logger.info(f"Found {len(channel_dialogs)} subscribed channels")
            
            if not channel_dialogs:
                self.logger.info("No channels found - account not subscribed to any channels")
                # Update account with empty list
                await db.update_account(self.phone_number, {'subscribed_to': []})
                self.account.subscribed_to = []
                return []
            
            chat_ids = []
            channels_to_upsert = []
            
            for dialog in channel_dialogs:
                entity = dialog.entity
                chat_id = normalize_chat_id(entity.id)
                chat_ids.append(chat_id)
                
                # Extract channel data from the entity we already have
                # No additional API calls needed!
                channel_data = {
                    'chat_id': chat_id,
                    'is_private': not getattr(entity, 'username', None),  # No username = private
                    'channel_name': getattr(entity, 'title', None),
                    'has_enabled_reactions': getattr(entity, 'reactions_enabled', True),
                    'tags': []  # Will be managed manually by user
                }
                
                # Get channel hash for private channels (access_hash)
                if hasattr(entity, 'access_hash') and entity.access_hash:
                    channel_data['channel_hash'] = str(entity.access_hash)
                else:
                    channel_data['channel_hash'] = ""
                
                # Check if channel has linked discussion group
                # Only fetch if we don't already have this channel in DB
                existing_channel = await db.get_channel(chat_id)
                
                if existing_channel:
                    # Channel exists - only update basic fields that might have changed
                    update_data = {
                        'channel_name': channel_data['channel_name'],
                        'is_private': channel_data['is_private'],
                        'has_enabled_reactions': channel_data['has_enabled_reactions']
                    }
                    await db.update_channel(chat_id, update_data)
                    self.logger.debug(f"Updated existing channel: {channel_data['channel_name']} ({chat_id})")
                else:
                    # New channel - try to get discussion group info
                    # This is the only additional API call we make, and only for new channels
                    try:
                        full_channel = await self.client(functions.channels.GetFullChannelRequest(
                            channel=entity
                        ))
                        
                        # Check for linked discussion group
                        if hasattr(full_channel.full_chat, 'linked_chat_id'):
                            channel_data['discussion_chat_id'] = full_channel.full_chat.linked_chat_id
                        else:
                            channel_data['discussion_chat_id'] = None
                        
                        # Check reaction settings more accurately
                        if hasattr(full_channel.full_chat, 'available_reactions'):
                            reactions = full_channel.full_chat.available_reactions
                            if reactions is None:
                                channel_data['has_enabled_reactions'] = False
                            elif hasattr(reactions, 'reactions'):
                                channel_data['has_enabled_reactions'] = len(reactions.reactions) > 0
                        
                        # Check if reactions are only for subscribers
                        if hasattr(full_channel.full_chat, 'reactions_limit'):
                            # If there's a limit, it might be subscriber-only
                            # This is a heuristic - Telegram doesn't expose this directly
                            channel_data['reactions_only_for_subscribers'] = False
                        else:
                            channel_data['reactions_only_for_subscribers'] = False
                            
                    except Exception as e:
                        self.logger.warning(f"Could not fetch full channel info for {chat_id}: {e}")
                        # Use defaults if full channel fetch fails
                        channel_data['discussion_chat_id'] = None
                        channel_data['reactions_only_for_subscribers'] = False
                    
                    # Add timestamps
                    channel_data['created_at'] = datetime.now(timezone.utc)
                    channel_data['updated_at'] = datetime.now(timezone.utc)
                    
                    channels_to_upsert.append(channel_data)
            
            # Batch insert new channels
            for channel_data in channels_to_upsert:
                try:
                    await db.add_channel(channel_data)
                    self.logger.debug(f"Added new channel: {channel_data['channel_name']} ({channel_data['chat_id']})")
                except ValueError as e:
                    # Channel already exists (race condition) - update instead
                    self.logger.debug(f"Channel {channel_data['chat_id']} already exists, updating: {e}")
                    update_data = {k: v for k, v in channel_data.items() if k not in ['chat_id', 'created_at']}
                    await db.update_channel(channel_data['chat_id'], update_data)
            
            # Update account's subscribed_to field
            await db.update_account(self.phone_number, {'subscribed_to': chat_ids})
            self.account.subscribed_to = chat_ids
            
            self.logger.info(
                f"Successfully updated subscriptions for {self.phone_number}: "
                f"{len(chat_ids)} channels, {len(channels_to_upsert)} new channels added"
            )
            
            return chat_ids
            
        except Exception as e:
            self.logger.error(f"Error fetching subscribed channels: {e}")
            raise
    
    async def update_account_id_from_telegram(self):
        """Fetch account id from Telegram and update the account record in database."""
        try:
            await self.ensure_connected()
            me = await self.client.get_me()
            account_id = me.id if hasattr(me, 'id') else None
            if account_id:
                from main_logic.database import get_db
                db = get_db()
                await db.update_account(self.phone_number, {'account_id': account_id})
                self.logger.info(f"Updated account_id for {self.phone_number} to {account_id}")
                self.account.account_id = account_id
                self.account_id = account_id
            else:
                self.logger.warning("Could not fetch account_id from Telegram.")
        except Exception as e:
            self.logger.error(f"Error updating account_id from Telegram: {e}")
            raise
