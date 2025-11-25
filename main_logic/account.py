"""
Account data model for Telegram accounts.

This module defines the Account class which represents a Telegram account's
data, status, and provides methods for status management and serialization.
"""

from pandas import Timestamp
from main_logic.schemas import AccountStatus, status_name
from auxilary_logic.encryption import (
    encrypt_secret,
    PURPOSE_PASSWORD,
)


class Account(object):
    """
    Represents a Telegram account with its configuration and status.
    
    Attributes:
        phone_number: The phone number associated with the account
        account_id: Telegram account ID (fetched from Telegram)
        session_name: Name for the session file
        session_encrypted: Encrypted session string
        twofa: Whether 2FA is enabled
        password_encrypted: Encrypted 2FA password
        notes: User notes about the account
        subscribed_to: List of channel chat_ids the account is subscribed to
        status: Current AccountStatus
        created_at: Timestamp when account was created
        updated_at: Timestamp when account was last updated
        last_error: Last error message
        last_error_type: Type of last error
        last_error_time: When last error occurred
        last_success_time: When last successful action occurred
        last_checked: When account was last checked
        flood_wait_until: When flood wait expires (if any)
        last_channel_sync_at: Timestamp of last channel sync operation
        last_channel_sync_count: Number of channels found in last sync
    """

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
            self.subscribed_to = account_data.get('subscribed_to', [])
            self.status = account_data.get('status', self.AccountStatus.NEW)
            self.created_at = account_data.get('created_at', Timestamp.now())
            self.updated_at = account_data.get('updated_at', Timestamp.now())
            
            # Status tracking fields
            self.last_error = account_data.get('last_error', None)
            self.last_error_type = account_data.get('last_error_type', None)
            self.last_error_time = account_data.get('last_error_time', None)
            self.last_success_time = account_data.get('last_success_time', None)
            self.last_checked = account_data.get('last_checked', None)
            self.flood_wait_until = account_data.get('flood_wait_until', None)
            
            # Channel sync metadata
            self.last_channel_sync_at = account_data.get('last_channel_sync_at', None)
            self.last_channel_sync_count = account_data.get('last_channel_sync_count', None)

        except KeyError as e:
            raise ValueError(f"Missing key in account configuration: {e}")

        if self.twofa and not self.password_encrypted:
            raise ValueError("2FA is enabled but no password provided in account configuration.")

        if self.session_name is None:
            self.session_name = self.phone_number
    
    def __repr__(self):
        return f"Account({self.account_id}, {self.phone_number}, status={self.status})"
    
    def __str__(self):
        return f"Account ID: {self.account_id}, phone: {self.phone_number}, status: {self.status}"
    
    def is_usable(self) -> bool:
        """Check if account can be used in tasks."""
        return AccountStatus.is_usable(self.status)
    
    def needs_attention(self) -> bool:
        """Check if account requires manual intervention."""
        return AccountStatus.needs_attention(self.status)
    
    async def update_status(self, new_status: AccountStatus, error: Exception = None, success: bool = False):
        """
        Update account status and related tracking fields in database.
        
        Args:
            new_status: New AccountStatus to set
            error: Optional exception that caused the status change
            success: If True, updates last_success_time
        """
        from main_logic.database import get_db
        from datetime import datetime, timezone
        
        db = get_db()
        update_data = {
            'status': status_name(new_status),
            'last_checked': datetime.now(timezone.utc),
            'updated_at': datetime.now(timezone.utc)
        }
        
        if error:
            update_data['last_error'] = str(error)
            update_data['last_error_type'] = type(error).__name__
            update_data['last_error_time'] = datetime.now(timezone.utc)
        
        if success:
            update_data['last_success_time'] = datetime.now(timezone.utc)
            # Clear error fields on success
            update_data['last_error'] = None
            update_data['last_error_type'] = None
        
        # Update local instance
        self.status = new_status
        self.last_checked = update_data['last_checked']
        if error:
            self.last_error = update_data['last_error']
            self.last_error_type = update_data['last_error_type']
            self.last_error_time = update_data['last_error_time']
        if success:
            self.last_success_time = update_data['last_success_time']
            self.last_error = None
            self.last_error_type = None
        
        # Update database
        await db.update_account(self.phone_number, update_data)
    
    async def set_flood_wait(self, seconds: int, error: Exception = None):
        """Set flood wait status and expiration time.

        Args:
            seconds: number of seconds the flood wait will last
            error: optional exception to record in last_error/last_error_type
        """
        from main_logic.database import get_db
        from datetime import datetime, timezone, timedelta
        
        db = get_db()
        flood_wait_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)

        # The explicit FLOOD_WAIT status was removed from AccountStatus. Use
        # ERROR to flag the account for attention while still recording the
        # flood-wait expiration. Record the error details when available.
        update_payload = {
            'status': AccountStatus.ERROR.name,
            'flood_wait_until': flood_wait_until,
            'last_checked': datetime.now(timezone.utc)
        }
        if error:
            update_payload['last_error'] = str(error)
            update_payload['last_error_type'] = type(error).__name__
            update_payload['last_error_time'] = datetime.now(timezone.utc)

        await db.update_account(self.phone_number, update_payload)
        
        self.status = AccountStatus.ERROR
        self.flood_wait_until = flood_wait_until
        if error:
            self.last_error = update_payload.get('last_error')
            self.last_error_type = update_payload.get('last_error_type')
            self.last_error_time = update_payload.get('last_error_time')
    
    def to_dict(self, secure=False):
        """Convert Account object to dictionary matching AccountDict schema.
        
        Args:
            secure (bool): If True, excludes password_encrypted field for security
        """
        base_dict = {
            'account_id': self.account_id,
            'session_name': self.session_name,
            'phone_number': self.phone_number,
            'session_encrypted': self.session_encrypted,
            'twofa': self.twofa,
            'notes': self.notes,
            'subscribed_to': self.subscribed_to if hasattr(self, 'subscribed_to') else [],
            'status': status_name(self.status),
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'last_error': self.last_error,
            'last_error_type': self.last_error_type,
            'last_error_time': self.last_error_time,
            'last_success_time': self.last_success_time,
            'last_checked': self.last_checked,
            'flood_wait_until': self.flood_wait_until,
            'last_channel_sync_at': self.last_channel_sync_at,
            'last_channel_sync_count': self.last_channel_sync_count
        }
        
        if not secure:
            base_dict['password_encrypted'] = self.password_encrypted
            
        return base_dict

    async def create_connection(self):
        """Create a TelegramClient connection from account, useful for debugging."""
        # Import here to avoid circular import
        from main_logic.agent import Client
        client = Client(self)
        await client.connect()
        client.logger.info(f"Client for {self.phone_number} connected successfully.")
        return client

    async def add_password(self, password):
        """Add or update the encrypted password for 2FA."""
        if not password:
            raise ValueError("Password cannot be empty.")
        self.password_encrypted = encrypt_secret(password, PURPOSE_PASSWORD)
        self.twofa = True

        from main_logic.database import get_db
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
        subscribed_to=None,
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
            'password_encrypted': password_encrypted if password_encrypted else encrypt_secret(password, PURPOSE_PASSWORD) if password else None,
            'notes': notes if notes is not None else "",
            'subscribed_to': subscribed_to if subscribed_to is not None else [],
            'status': status if status is not None else cls.AccountStatus.NEW,
            'created_at': created_at or Timestamp.now(),
            'updated_at': updated_at or Timestamp.now()
        }
        return cls(account_data)

    @classmethod
    async def get_accounts(cls, phones: list):
        """Get a list of Account objects from a list of phone numbers."""
        from main_logic.database import get_db
        db = get_db()
        all_accounts = await db.load_all_accounts()
        return [elem for elem in all_accounts if elem.phone_number in phones]
