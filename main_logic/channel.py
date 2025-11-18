"""channel.py
Domain class for Telegram channel representation.

Provides Channel class with:
- Channel metadata (chat_id, name, privacy settings)
- Reaction configuration
- Discussion group linking
- Tag-based categorization
"""

from pandas import Timestamp
from typing import Optional, List
from utils.logger import setup_logger, load_config

config = load_config()
logger = setup_logger("Channel", "main.log")

def normalize_chat_id(chat_id: int) -> int:
    """
    Normalize chat_id by removing -100 prefix if present.
    
    Telegram uses -100 prefix for supergroups/channels in some contexts,
    but the actual ID is the part after -100.
    
    Examples:
        -1002723750105 -> 2723750105
        2723750105 -> 2723750105
        -100123 -> 123
        123 -> 123
    
    Args:
        chat_id: Original chat ID (may have -100 prefix)
        
    Returns:
        Normalized chat ID without -100 prefix
    """
    if chat_id is None:
        return None
    
    # Convert to string to check prefix
    chat_id_str = str(chat_id)
    
    # Check if it starts with -100
    if chat_id_str.startswith('-100'):
        # Remove -100 prefix and convert back to int
        return int(chat_id_str[4:])
    
    # Return absolute value if negative but not -100 prefix
    return abs(chat_id)

class Channel:
    """
    Represents a Telegram channel with metadata and configuration.
    
    Primary Key: chat_id (unique Telegram chat identifier)
    
    Attributes:
        chat_id: Telegram chat ID (unique identifier, primary key)
        is_private: Whether the channel is private
        channel_hash: Channel hash for private channels (blank for now)
        has_enabled_reactions: Whether reactions are enabled
        reactions_only_for_subscribers: Whether reactions are restricted to subscribers
        discussion_chat_id: Linked discussion group chat ID
        channel_name: Channel name/title
        tags: List of tags for categorization
        created_at: Creation timestamp
        updated_at: Last update timestamp
    """

    def __init__(
        self,
        chat_id: int,
        is_private: bool = False,
        channel_hash: Optional[str] = "",
        has_enabled_reactions: bool = True,
        reactions_only_for_subscribers: bool = False,
        discussion_chat_id: Optional[int] = None,
        channel_name: Optional[str] = None,
        tags: Optional[List[str]] = None,
        created_at=None,
        updated_at=None
    ):
        self.chat_id = normalize_chat_id(chat_id)
        self.is_private = is_private
        self.channel_hash = channel_hash or ""
        self.has_enabled_reactions = has_enabled_reactions
        self.reactions_only_for_subscribers = reactions_only_for_subscribers
        self.discussion_chat_id = normalize_chat_id(discussion_chat_id) if discussion_chat_id else None
        self.channel_name = channel_name
        self.tags = tags or []
        self.created_at = created_at or Timestamp.now()
        self.updated_at = updated_at or Timestamp.now()

    def __repr__(self):
        """String representation of Channel."""
        name_part = f"'{self.channel_name}'" if self.channel_name else "unnamed"
        privacy = "private" if self.is_private else "public"
        return f"Channel({self.chat_id}, {name_part}, {privacy})"

    def to_dict(self):
        """
        Convert Channel object to dictionary with serializable timestamps.
        
        Returns:
            Dictionary representation of the Channel
        """
        return {
            'chat_id': self.chat_id,
            'is_private': self.is_private,
            'channel_hash': self.channel_hash,
            'has_enabled_reactions': self.has_enabled_reactions,
            'reactions_only_for_subscribers': self.reactions_only_for_subscribers,
            'discussion_chat_id': self.discussion_chat_id,
            'channel_name': self.channel_name,
            'tags': self.tags if self.tags else [],
            'created_at': self.created_at.isoformat() if isinstance(self.created_at, Timestamp) else self.created_at,
            'updated_at': self.updated_at.isoformat() if isinstance(self.updated_at, Timestamp) else self.updated_at
        }

    @classmethod
    def from_dict(cls, data: dict):
        """
        Create a Channel object from a dictionary.
        
        Args:
            data: Dictionary with channel data
            
        Returns:
            Channel instance
        """
        return cls(
            chat_id=data['chat_id'],
            is_private=data.get('is_private', False),
            channel_hash=data.get('channel_hash', ""),
            has_enabled_reactions=data.get('has_enabled_reactions', True),
            reactions_only_for_subscribers=data.get('reactions_only_for_subscribers', False),
            discussion_chat_id=data.get('discussion_chat_id'),
            channel_name=data.get('channel_name'),
            tags=data.get('tags', []),
            created_at=data.get('created_at'),
            updated_at=data.get('updated_at')
        )

    @classmethod
    def from_keys(
        cls,
        chat_id: int,
        is_private: bool = False,
        channel_hash: Optional[str] = "",
        has_enabled_reactions: bool = True,
        reactions_only_for_subscribers: bool = False,
        discussion_chat_id: Optional[int] = None,
        channel_name: Optional[str] = None,
        tags: Optional[List[str]] = None
    ):
        """
        Create a Channel object from individual keys.
        
        Args:
            chat_id: Telegram chat ID
            is_private: Whether the channel is private
            channel_hash: Channel hash for private channels
            has_enabled_reactions: Whether reactions are enabled
            reactions_only_for_subscribers: Whether reactions are restricted
            discussion_chat_id: Linked discussion group chat ID
            channel_name: Channel name/title
            tags: List of tags
            
        Returns:
            Channel instance
        """
        return cls(
            chat_id=chat_id,
            is_private=is_private,
            channel_hash=channel_hash,
            has_enabled_reactions=has_enabled_reactions,
            reactions_only_for_subscribers=reactions_only_for_subscribers,
            discussion_chat_id=discussion_chat_id,
            channel_name=channel_name,
            tags=tags
        )

    def update(self, **kwargs):
        """
        Update channel attributes.
        
        Args:
            **kwargs: Keyword arguments for attributes to update
        """
        allowed_fields = {
            'is_private', 'channel_hash', 'has_enabled_reactions',
            'reactions_only_for_subscribers', 'discussion_chat_id',
            'channel_name', 'tags'
        }
        
        for key, value in kwargs.items():
            if key in allowed_fields:
                setattr(self, key, value)
        
        # Update the timestamp
        self.updated_at = Timestamp.now()

    def add_tag(self, tag: str):
        """
        Add a tag to the channel.
        
        Args:
            tag: Tag to add
        """
        tag = tag.strip()
        if tag and tag not in self.tags:
            self.tags.append(tag)
            self.updated_at = Timestamp.now()

    def remove_tag(self, tag: str):
        """
        Remove a tag from the channel.
        
        Args:
            tag: Tag to remove
        """
        if tag in self.tags:
            self.tags.remove(tag)
            self.updated_at = Timestamp.now()

    def has_tag(self, tag: str) -> bool:
        """
        Check if channel has a specific tag.
        
        Args:
            tag: Tag to check
            
        Returns:
            True if tag exists, False otherwise
        """
        return tag in self.tags

    @property
    def can_react(self) -> bool:
        """
        Check if reactions are possible on this channel.
        
        Returns:
            True if reactions are enabled, False otherwise
        """
        return self.has_enabled_reactions

    @property
    def requires_subscription_for_reactions(self) -> bool:
        """
        Check if subscription is required to react.
        
        Returns:
            True if subscription required, False otherwise
        """
        return self.reactions_only_for_subscribers

    @property
    def has_discussion_group(self) -> bool:
        """
        Check if channel has a linked discussion group.
        
        Returns:
            True if discussion group exists, False otherwise
        """
        return self.discussion_chat_id is not None
