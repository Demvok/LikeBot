"""
Client mixins package.

Provides modular components for Telegram client functionality through mixins.
Each mixin encapsulates a specific behavior domain for better maintainability.
"""

from .session import SessionMixin
from .proxy import ProxyMixin
from .locking import LockingMixin
from .connection import ConnectionMixin
from .entity_resolution import EntityResolutionMixin
from .channel_data import ChannelDataMixin
from .actions import ActionsMixin
from .cache_integration import CacheIntegrationMixin

__all__ = [
    'SessionMixin',
    'ProxyMixin',
    'LockingMixin',
    'ConnectionMixin',
    'EntityResolutionMixin',
    'ChannelDataMixin',
    'ActionsMixin',
    'CacheIntegrationMixin',
]
