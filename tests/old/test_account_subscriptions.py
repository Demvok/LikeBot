"""
Test suite for new Account subscriptions and Channel/Post query features.
"""

import pytest
from main_logic.agent import Account
from main_logic.schemas import AccountCreate, AccountUpdate, AccountDict


class TestAccountSubscriptions:
    """Test Account subscribed_to field."""
    
    def test_account_creation_with_subscriptions(self):
        """Test creating Account with subscribed_to field."""
        account_data = {
            'phone_number': '+1234567890',
            'subscribed_to': [-1001234567890, -1009876543210]
        }
        account = Account(account_data)
        
        assert account.phone_number == '+1234567890'
        assert account.subscribed_to == [-1001234567890, -1009876543210]
        assert len(account.subscribed_to) == 2
    
    def test_account_creation_without_subscriptions(self):
        """Test creating Account without subscribed_to defaults to empty list."""
        account_data = {
            'phone_number': '+1234567890'
        }
        account = Account(account_data)
        
        assert account.subscribed_to == []
    
    def test_account_to_dict_includes_subscriptions(self):
        """Test Account.to_dict() includes subscribed_to."""
        account_data = {
            'phone_number': '+1234567890',
            'subscribed_to': [-1001234567890]
        }
        account = Account(account_data)
        
        result = account.to_dict()
        
        assert 'subscribed_to' in result
        assert result['subscribed_to'] == [-1001234567890]
    
    def test_account_to_dict_secure_includes_subscriptions(self):
        """Test Account.to_dict(secure=True) includes subscribed_to."""
        account_data = {
            'phone_number': '+1234567890',
            'password_encrypted': 'encrypted_pw',
            'subscribed_to': [-1001234567890]
        }
        account = Account(account_data)
        
        result = account.to_dict(secure=True)
        
        assert 'subscribed_to' in result
        assert result['subscribed_to'] == [-1001234567890]
        assert 'password_encrypted' not in result  # Verify secure works
    
    def test_account_from_keys_with_subscriptions(self):
        """Test Account.from_keys() with subscribed_to."""
        account = Account.from_keys(
            phone_number='+1234567890',
            subscribed_to=[-1001234567890, -1009876543210]
        )
        
        assert account.subscribed_to == [-1001234567890, -1009876543210]
    
    def test_account_from_keys_without_subscriptions(self):
        """Test Account.from_keys() without subscribed_to defaults to empty list."""
        account = Account.from_keys(
            phone_number='+1234567890'
        )
        
        assert account.subscribed_to == []


class TestAccountSubscriptionSchemas:
    """Test Account schema updates for subscribed_to."""
    
    def test_account_create_schema_with_subscriptions(self):
        """Test AccountCreate schema with subscribed_to."""
        from main_logic.schemas import AccountCreate
        
        data = AccountCreate(
            phone_number='+1234567890',
            subscribed_to=[-1001234567890]
        )
        
        assert data.subscribed_to == [-1001234567890]
    
    def test_account_create_schema_without_subscriptions(self):
        """Test AccountCreate schema defaults subscribed_to to empty list."""
        from main_logic.schemas import AccountCreate
        
        data = AccountCreate(
            phone_number='+1234567890'
        )
        
        # Should default to empty list via default_factory
        assert data.subscribed_to == []
    
    def test_account_update_schema_with_subscriptions(self):
        """Test AccountUpdate schema with subscribed_to."""
        from main_logic.schemas import AccountUpdate
        
        data = AccountUpdate(
            subscribed_to=[-1001234567890, -1009876543210]
        )
        
        assert data.subscribed_to == [-1001234567890, -1009876543210]
    
    def test_account_dict_schema_with_subscriptions(self):
        """Test AccountDict schema with subscribed_to."""
        from main_logic.schemas import AccountDict, AccountStatus
        from datetime import datetime
        
        data = AccountDict(
            phone_number='+1234567890',
            account_id=123456,
            session_name='session',
            session_encrypted='enc_session',
            twofa=False,
            password_encrypted=None,
            notes='',
            status=AccountStatus.ACTIVE,  # Use enum instead of string
            subscribed_to=[-1001234567890],
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        )
        
        assert data.subscribed_to == [-1001234567890]


class TestDatabaseQueryMethods:
    """Test new database query methods (requires async setup)."""
    
    def test_placeholder_get_subscribed_channels(self):
        """Placeholder for get_subscribed_channels async test."""
        # Actual test would be:
        # channels = await db.get_subscribed_channels('+1234567890')
        # assert isinstance(channels, list)
        # assert all(isinstance(c, Channel) for c in channels)
        pass
    
    def test_placeholder_get_posts_by_chat_id(self):
        """Placeholder for get_posts_by_chat_id async test."""
        # Actual test would be:
        # posts = await db.get_posts_by_chat_id(-1001234567890)
        # assert isinstance(posts, list)
        # assert all(isinstance(p, Post) for p in posts)
        # assert all(p.chat_id == -1001234567890 for p in posts)
        pass


class TestChannelPrimaryKey:
    """Test Channel primary key documentation and usage."""
    
    def test_channel_chat_id_is_unique_identifier(self):
        """Test that chat_id is documented as primary key."""
        from main_logic.channel import Channel
        
        # Check class docstring mentions primary key
        assert 'Primary Key' in Channel.__doc__
        assert 'chat_id' in Channel.__doc__
    
    def test_channel_schema_documents_primary_key(self):
        """Test that ChannelBase schema documents chat_id as primary key."""
        from main_logic.schemas import ChannelBase
        
        # Check schema docstring
        assert 'Primary Key' in ChannelBase.__doc__
        
        # Check field description
        chat_id_field = ChannelBase.model_fields['chat_id']
        assert 'primary key' in chat_id_field.description.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
