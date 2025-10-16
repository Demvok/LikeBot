"""
LikeBot Data Schemas

This module centralizes all data structures used in the LikeBot project to ensure consistency
across all components. It includes Pydantic models for validation, type hints, and serialization.

Usage locations to update when modifying schemas:
1. agent.py - Account class
2. taskhandler.py - Post and Task classes  
3. main.py - Pydantic models for API endpoints
4. database.py - Storage interface and implementations
5. reporter.py - Run and Event data structures
6. API_Documentation.md - Data models section

IMPORTANT: When modifying schemas, search for and update ALL usage locations listed above.
Use global find/replace to ensure consistency across the codebase.
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Union, Literal
from enum import Enum, auto
from pydantic import BaseModel, Field, field_validator, model_validator
from pandas import Timestamp
from asyncio import Future
from telethon import TelegramClient


# ============= ENUMS =============

class AccountStatus(Enum):
    """Account status enumeration."""
    ACTIVE = auto()
    LOGGED_IN = auto()
    NEW = auto()
    BANNED = auto()
    ERROR = auto()
    
    def __str__(self):
        return self.name
    
    def __repr__(self):
        return self.name


class TaskStatus(Enum):
    """Task execution status enumeration."""
    PENDING = auto()
    RUNNING = auto()
    PAUSED = auto()
    FINISHED = auto()
    CRASHED = auto()
    
    def __str__(self):
        return self.name
    
    def __repr__(self):
        return self.name


class ReactionPalette(Enum):
    """Available emoji reaction palettes."""
    POSITIVE = "positive"
    NEGATIVE = "negative"


class ActionType(Enum):
    """Available action types for tasks."""
    REACT = "react"
    COMMENT = "comment"


class EventLevel(Enum):
    """Event logging levels."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class LoginStatus(Enum):
    """Login process status enumeration."""
    WAIT_CODE = "wait_code"
    WAIT_2FA = "wait_2fa"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    
    def __str__(self):
        return self.value
    
    def __repr__(self):
        return self.value


# ============= BASE SCHEMAS =============

class TimestampMixin(BaseModel):
    """Mixin for models that need created_at and updated_at timestamps."""
    created_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }


# ============= ACCOUNT SCHEMAS =============

class AccountBase(BaseModel):
    """Base schema for Account data."""
    phone_number: str = Field(..., description="Phone number with country code (e.g., +1234567890)")
    account_id: Optional[int] = Field(None, description="Telegram account ID")
    session_name: Optional[str] = Field(None, description="Telegram session name")
    session_encrypted: Optional[str] = Field(None, description="Encrypted Telegram session string")
    twofa: bool = Field(False, description="Is 2FA enabled for this account?")
    password_encrypted: Optional[str] = Field(None, description="Encrypted password for 2FA")
    notes: Optional[str] = Field("", description="Account notes")

    @field_validator('phone_number')
    def validate_phone_number(cls, v):
        if not v.startswith('+'):
            raise ValueError('Phone number must start with + and include country code')
        if len(v) < 10:
            raise ValueError('Phone number must be at least 10 characters long')
        return v

    @model_validator(mode='after')
    def validate_twofa_password(self):
        if self.twofa and not self.password_encrypted:
            raise ValueError('password_encrypted is required when twofa is enabled')
        return self


class AccountCreate(BaseModel):
    """Schema for creating new accounts."""
    phone_number: str = Field(..., description="Phone number with country code (e.g., +1234567890)")
    session_name: Optional[str] = Field(None, description="Telegram session name")
    twofa: bool = Field(False, description="Is 2FA enabled for this account?")
    password: Optional[str] = Field(None, description="Plain text password for 2FA (will be encrypted server-side)")
    notes: Optional[str] = Field("", description="Account notes")

    @field_validator('phone_number')
    def validate_phone_number(cls, v):
        if not v.startswith('+'):
            raise ValueError('Phone number must start with + and include country code')
        if len(v) < 10:
            raise ValueError('Phone number must be at least 10 characters long')
        return v

    @model_validator(mode='after')
    def validate_twofa_password(self):
        if self.twofa and not self.password:
            raise ValueError('password is required when twofa is enabled')
        return self

class LoginProcess(BaseModel):
    """Schema for tracking login process state."""
    login_session_id: str = Field(..., description="Unique login session identifier (UUID)")
    phone_number: str = Field(..., description="Phone number with country code (e.g., +1234567890)")
    status: LoginStatus = Field(default=LoginStatus.PROCESSING, description="Current status of the login process")
    telethon_client: Optional[TelegramClient] = Field(None, description="Telethon client instance (not serializable)")
    code_future: Optional[Future] = Field(None, description="Future to await verification code from user")
    password_future: Optional[Future] = Field(None, description="Future to await 2FA password from user")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp when the login process was created")
    expires_at: Optional[datetime] = Field(None, description="Timestamp when the login process expires")
    error_message: Optional[str] = Field(None, description="Error message if login failed")
    session_string: Optional[str] = Field(None, description="Encrypted session string after successful login")
    
    class Config:
        arbitrary_types_allowed = True
        # Don't use use_enum_values here - we need the enum object for comparisons


class AccountUpdate(BaseModel):
    """Schema for updating existing accounts."""
    account_id: Optional[int] = Field(None, description="Telegram account ID")
    session_name: Optional[str] = Field(None, description="Telegram session name")
    session_encrypted: Optional[str] = Field(None, description="Encrypted Telegram session string")
    twofa: Optional[bool] = Field(None, description="Is 2FA enabled for this account?")
    password: Optional[str] = Field(None, description="Plain text password for 2FA (will be encrypted server-side)")
    notes: Optional[str] = Field(None, description="Account notes")
    status: Optional[AccountStatus] = Field(None, description="Account status")

    class Config:
        use_enum_values = True


class AccountResponse(AccountBase, TimestampMixin):
    """Schema for account responses (includes all fields except password)."""
    status: Optional[AccountStatus] = Field(default=AccountStatus.NEW, description="Account status")

    class Config:
        use_enum_values = True
        validate_by_name = True


class AccountPasswordResponse(BaseModel):
    """Schema for secure password retrieval (mockup)."""
    phone_number: str = Field(..., description="Phone number with country code")
    has_password: bool = Field(..., description="Whether account has a password set")
    password: Optional[str] = Field(None, description="Decrypted password (only returned in secure context)")

    class Config:
        use_enum_values = True


class AccountDict(BaseModel):
    """Schema for Account.to_dict() output."""
    account_id: Optional[int]
    session_name: Optional[str]
    phone_number: str
    session_encrypted: Optional[str]
    twofa: bool = Field(False)
    password_encrypted: Optional[str]
    notes: Optional[str]
    status: Optional[AccountStatus]
    created_at: Optional[Union[str, datetime]]
    updated_at: Optional[Union[str, datetime]]

    class Config:
        use_enum_values = True
        validate_by_name = True


class AccountDictSecure(BaseModel):
    """Schema for Account.to_dict() output without password information."""
    account_id: Optional[int]
    session_name: Optional[str]
    phone_number: str
    session_encrypted: Optional[str]
    twofa: bool = Field(False)
    notes: Optional[str]
    status: Optional[AccountStatus]
    created_at: Optional[Union[str, datetime]]
    updated_at: Optional[Union[str, datetime]]

    class Config:
        use_enum_values = True
        validate_by_name = True


# ============= POST SCHEMAS =============

class PostBase(BaseModel):
    """Base schema for Post data."""
    message_link: str = Field(..., description="Telegram message link")
    chat_id: Optional[int] = Field(None, description="Telegram chat ID")
    message_id: Optional[int] = Field(None, description="Telegram message ID")

    @field_validator('message_link')
    def validate_message_link(cls, v):
        if not v.startswith('https://t.me/'):
            raise ValueError('Message link must be a valid Telegram link starting with https://t.me/')
        return v


class PostCreate(PostBase):
    """Schema for creating new posts."""
    post_id: Optional[int] = Field(None, description="Post ID (auto-generated if not provided)")


class PostUpdate(BaseModel):
    """Schema for updating existing posts."""
    message_link: Optional[str] = Field(None, description="Telegram message link")
    chat_id: Optional[int] = Field(None, description="Telegram chat ID")
    message_id: Optional[int] = Field(None, description="Telegram message ID")

    @field_validator('message_link')
    def validate_message_link(cls, v):
        if v is not None and not v.startswith('https://t.me/'):
            raise ValueError('Message link must be a valid Telegram link starting with https://t.me/')
        return v


class PostResponse(PostBase, TimestampMixin):
    """Schema for post responses (includes all fields)."""
    post_id: int = Field(..., description="Unique post identifier")
    is_validated: bool = Field(default=False, description="Whether the post has been validated")


class PostDict(BaseModel):
    """Schema for Post.to_dict() output."""
    post_id: Optional[int]
    chat_id: Optional[int]
    message_id: Optional[int]
    message_link: str
    is_validated: bool
    created_at: Union[str, datetime]
    updated_at: Union[str, datetime]

    class Config:
        arbitrary_types_allowed = True


# ============= ACTION SCHEMAS =============

class ReactAction(BaseModel):
    """Schema for reaction actions."""
    type: Literal["react"] = Field(default="react", description="Action type")
    palette: ReactionPalette = Field(..., description="Emoji palette to use")

    class Config:
        use_enum_values = True


class CommentAction(BaseModel):
    """Schema for comment actions."""
    type: Literal["comment"] = Field(default="comment", description="Action type")
    content: str = Field(..., description="Comment content", min_length=1, max_length=4096)


class UndoReactionAction(BaseModel):
    """Schema for undo reaction actions."""
    type: Literal["undo_reaction"] = Field(default="undo_reaction", description="Action type")


class UndoCommentAction(BaseModel):
    """Schema for undo comment actions."""
    type: Literal["undo_comment"] = Field(default="undo_comment", description="Action type")


# Union of all action types
TaskAction = Union[ReactAction, CommentAction, UndoReactionAction, UndoCommentAction]


# ============= TASK SCHEMAS =============

class TaskBase(BaseModel):
    """Base schema for Task data."""
    name: str = Field(..., description="Task name", min_length=1, max_length=255)
    description: Optional[str] = Field(None, description="Task description", max_length=1000)
    post_ids: List[int] = Field(..., description="List of post IDs to process", min_items=1)
    accounts: List[str] = Field(..., description="List of phone numbers to use", min_items=1)
    action: TaskAction = Field(..., description="Action configuration")

    @field_validator('accounts')
    def validate_accounts(cls, v):
        for phone in v:
            if not phone.startswith('+'):
                raise ValueError(f'Phone number {phone} must start with + and include country code')
        return v


class TaskCreate(TaskBase):
    """Schema for creating new tasks."""
    task_id: Optional[int] = Field(None, description="Task ID (auto-generated if not provided)")


class TaskUpdate(BaseModel):
    """Schema for updating existing tasks."""
    name: Optional[str] = Field(None, description="Task name", min_length=1, max_length=255)
    description: Optional[str] = Field(None, description="Task description", max_length=1000)
    post_ids: Optional[List[int]] = Field(None, description="List of post IDs", min_items=1)
    accounts: Optional[List[str]] = Field(None, description="List of phone numbers", min_items=1)
    action: Optional[TaskAction] = Field(None, description="Action configuration")
    status: Optional[TaskStatus] = Field(None, description="Task status")

    @field_validator('accounts')
    def validate_accounts(cls, v):
        if v is not None:
            for phone in v:
                if not phone.startswith('+'):
                    raise ValueError(f'Phone number {phone} must start with + and include country code')
        return v

    class Config:
        use_enum_values = True


class TaskResponse(TaskBase, TimestampMixin):
    """Schema for task responses (includes all fields)."""
    task_id: int = Field(..., description="Unique task identifier")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="Task execution status")

    class Config:
        use_enum_values = True


class TaskDict(BaseModel):
    """Schema for Task.to_dict() output."""
    task_id: Optional[int]
    name: str
    description: Optional[str]
    post_ids: List[int]
    accounts: List[str]
    action: Dict[str, Any]
    status: Union[str, TaskStatus]
    created_at: Union[str, datetime]
    updated_at: Union[str, datetime]

    class Config:
        arbitrary_types_allowed = True
        use_enum_values = True


# ============= REPORTER/EVENT SCHEMAS =============

class RunMeta(BaseModel):
    """Schema for run metadata."""
    task_name: Optional[str] = None
    action: Optional[str] = None
    client_count: Optional[int] = None
    post_count: Optional[int] = None


class RunCreate(BaseModel):
    """Schema for creating new runs."""
    task_id: str = Field(..., description="Task identifier")
    meta: Optional[RunMeta] = Field(default_factory=RunMeta, description="Run metadata")


class RunResponse(BaseModel):
    """Schema for run responses."""
    run_id: str = Field(..., description="Unique run identifier")
    task_id: str = Field(..., description="Task identifier")
    started_at: datetime = Field(..., description="Run start timestamp")
    finished_at: Optional[datetime] = Field(None, description="Run finish timestamp")
    status: str = Field(..., description="Run status (running, success, failed)")
    meta: RunMeta = Field(default_factory=RunMeta, description="Run metadata")


class EventPayload(BaseModel):
    """Schema for event payload data."""
    account_phone: Optional[str] = None
    post_id: Optional[int] = None
    message_link: Optional[str] = None
    action_type: Optional[str] = None
    error_type: Optional[str] = None
    retry_count: Optional[int] = None


class EventCreate(BaseModel):
    """Schema for creating new events."""
    run_id: str = Field(..., description="Run identifier")
    task_id: str = Field(..., description="Task identifier")
    level: EventLevel = Field(..., description="Event level")
    code: Optional[str] = Field(None, description="Event code for categorization")
    message: Optional[str] = Field(None, description="Human-readable event message")
    payload: Optional[EventPayload] = Field(default_factory=EventPayload, description="Event payload data")

    class Config:
        use_enum_values = True


class EventResponse(EventCreate):
    """Schema for event responses."""
    ts: datetime = Field(..., description="Event timestamp")


# ============= API RESPONSE SCHEMAS =============

class SuccessResponse(BaseModel):
    """Standard success response schema."""
    message: str = Field(..., description="Success message")


class ErrorResponse(BaseModel):
    """Standard error response schema."""
    detail: str = Field(..., description="Error details")


class BulkOperationResult(BaseModel):
    """Schema for bulk operation results."""
    successful: int = Field(0, description="Number of successful operations")
    failed: int = Field(0, description="Number of failed operations")
    total: int = Field(0, description="Total number of operations")
    errors: List[str] = Field(default_factory=list, description="List of error messages")
    results: List[Dict[str, Any]] = Field(default_factory=list, description="Detailed results")


class DatabaseStats(BaseModel):
    """Schema for database statistics."""
    accounts: Dict[str, Any] = Field(default_factory=dict, description="Account statistics")
    posts: Dict[str, Any] = Field(default_factory=dict, description="Post statistics")
    tasks: Dict[str, Any] = Field(default_factory=dict, description="Task statistics")
    runs: Dict[str, Any] = Field(default_factory=dict, description="Run statistics")
    events: Dict[str, Any] = Field(default_factory=dict, description="Event statistics")


class ValidationResult(BaseModel):
    """Schema for post validation results."""
    message: str = Field(..., description="Validation result message")
    chat_id: Optional[int] = Field(None, description="Extracted chat ID")
    message_id: Optional[int] = Field(None, description="Extracted message ID")
    is_validated: bool = Field(False, description="Whether validation was successful")


# ============= HELPER FUNCTIONS =============

def serialize_for_json(obj: Any) -> Any:
    """
    Convert non-JSON-serializable objects to serializable format.
    Use this function to ensure consistent serialization across the project.
    """
    if obj is None:
        return None
    
    # Handle ObjectId specifically (MongoDB)
    if hasattr(obj, 'binary') and hasattr(obj, '__str__'):
        return str(obj)
    
    # Handle numpy types
    if hasattr(obj, 'item'):
        return obj.item()
    
    # Handle Timestamp objects (pandas)
    if hasattr(obj, 'isoformat') and hasattr(obj, 'value'):  # pandas Timestamp
        return obj.isoformat()
    
    # Handle datetime objects
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    
    # Handle enums
    if isinstance(obj, Enum):
        return obj.value
    
    # Handle dictionaries
    if isinstance(obj, dict):
        return {k: serialize_for_json(v) for k, v in obj.items()}
    
    # Handle lists and tuples
    if isinstance(obj, (list, tuple)):
        return [serialize_for_json(item) for item in obj]
    
    # Return as-is for basic types
    return obj


def validate_phone_number(phone: str) -> str:
    """
    Validate and normalize phone number format.
    
    Args:
        phone: Phone number string
        
    Returns:
        Normalized phone number
        
    Raises:
        ValueError: If phone number format is invalid
    """
    if not isinstance(phone, str):
        raise ValueError("Phone number must be a string")
    
    phone = phone.strip()
    if not phone.startswith('+'):
        raise ValueError('Phone number must start with + and include country code')
    
    if len(phone) < 10:
        raise ValueError('Phone number must be at least 10 characters long')
    
    return phone


def validate_telegram_link(link: str) -> str:
    """
    Validate Telegram message link format.
    
    Args:
        link: Telegram message link
        
    Returns:
        Validated link
        
    Raises:
        ValueError: If link format is invalid
    """
    if not isinstance(link, str):
        raise ValueError("Message link must be a string")
    
    link = link.strip()
    if not link.startswith('https://t.me/'):
        raise ValueError('Message link must be a valid Telegram link starting with https://t.me/')
    
    return link


# ============= MIGRATION HELPER =============

class SchemaMigration:
    """
    Helper class to track where schemas are used and need updates.
    
    This class provides methods to identify all locations where schema changes
    need to be propagated manually.
    """
    
    USAGE_LOCATIONS = {
        'AccountStatus': [
            'agent.py:Account.AccountStatus',
            'schemas.py:AccountStatus'
        ],
        'TaskStatus': [
            'taskhandler.py:Task.TaskStatus', 
            'schemas.py:TaskStatus'
        ],
        'Account': [
            'agent.py:Account.__init__',
            'agent.py:Account.to_dict',
            'agent.py:Account.from_keys',
            'agent.py:Client.update_account_id_from_telegram',
            'database.py:*Storage.add_account',
            'database.py:*Storage.get_account',
            'database.py:*Storage.update_account',
            'main.py:AccountCreate',
            'main.py:AccountUpdate',
            'API_Documentation.md:Account model'
        ],
        'Post': [
            'taskhandler.py:Post.__init__',
            'taskhandler.py:Post.to_dict',
            'taskhandler.py:Post.from_keys',
            'database.py:*Storage.add_post',
            'database.py:*Storage.get_post',
            'database.py:*Storage.update_post',
            'main.py:PostCreate',
            'main.py:PostUpdate',
            'API_Documentation.md:Post model'
        ],
        'Task': [
            'taskhandler.py:Task.__init__',
            'taskhandler.py:Task.to_dict',
            'database.py:*Storage.add_task',
            'database.py:*Storage.get_task',
            'database.py:*Storage.update_task',
            'main.py:TaskCreate',
            'main.py:TaskUpdate',
            'API_Documentation.md:Task model'
        ],
        'TaskAction': [
            'taskhandler.py:Task.action',
            'taskhandler.py:Task.get_action*',
            'main.py:TaskCreate.action',
            'main.py:TaskUpdate.action',
            'API_Documentation.md:Action Types'
        ],
        'Reporter': [
            'reporter.py:RunEventManager',
            'reporter.py:Reporter.new_run',
            'reporter.py:Reporter.event',
            'taskhandler.py:Task._run'
        ]
    }
    
    @classmethod
    def get_locations_for_schema(cls, schema_name: str) -> List[str]:
        """Get all file locations that need updates for a given schema."""
        return cls.USAGE_LOCATIONS.get(schema_name, [])
    
    @classmethod
    def get_all_locations(cls) -> Dict[str, List[str]]:
        """Get all schema usage locations."""
        return cls.USAGE_LOCATIONS.copy()
    
    @classmethod
    def print_migration_guide(cls, schema_name: str = None):
        """Print a migration guide for schema changes."""
        if schema_name:
            locations = cls.get_locations_for_schema(schema_name)
            print(f"\n=== Migration Guide for {schema_name} ===")
            print("Update the following locations:")
            for location in locations:
                print(f"  - {location}")
        else:
            print("\n=== Complete Schema Migration Guide ===")
            for schema, locations in cls.USAGE_LOCATIONS.items():
                print(f"\n{schema}:")
                for location in locations:
                    print(f"  - {location}")
                    
        print("\nAfter making changes:")
        print("1. Run tests to ensure compatibility")
        print("2. Update API documentation")
        print("3. Update any frontend/client code")
        print("4. Consider backward compatibility for existing data")


# Example usage:
if __name__ == "__main__":
    # Print migration guide
    SchemaMigration.print_migration_guide()
    
    # Example of using schemas
    account_data = AccountCreate(
        phone_number="+1234567890",
        account_id="123456789",
        session_name="test_session"
    )
    print(f"\nValid account: {account_data}")
    
    task_action = ReactAction(palette=ReactionPalette.POSITIVE)
    print(f"Valid action: {task_action}")