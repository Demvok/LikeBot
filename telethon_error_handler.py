from telethon import errors
from schemas import AccountStatus

# Defensive attribute access: some Telethon versions may not expose every exception class name.
# Use fallback dummy classes so isinstance checks don't raise AttributeError during tests.
def _dummy_exc(name):
    return type(name, (Exception,), {})

AuthKeyUnregisteredError = getattr(errors, 'AuthKeyUnregisteredError', _dummy_exc('AuthKeyUnregisteredError'))
AuthKeyInvalidError = getattr(errors, 'AuthKeyInvalidError', _dummy_exc('AuthKeyInvalidError'))
SessionRevokedError = getattr(errors, 'SessionRevokedError', None)
FloodWaitError = getattr(errors, 'FloodWaitError', _dummy_exc('FloodWaitError'))
UserDeactivatedBanError = getattr(errors, 'UserDeactivatedBanError', _dummy_exc('UserDeactivatedBanError'))
PhoneNumberBannedError = getattr(errors, 'PhoneNumberBannedError', _dummy_exc('PhoneNumberBannedError'))
PhoneNumberInvalidError = getattr(errors, 'PhoneNumberInvalidError', _dummy_exc('PhoneNumberInvalidError'))
SessionPasswordNeededError = getattr(errors, 'SessionPasswordNeededError', _dummy_exc('SessionPasswordNeededError'))
PhoneCodeInvalidError = getattr(errors, 'PhoneCodeInvalidError', _dummy_exc('PhoneCodeInvalidError'))
PhoneCodeExpiredError = getattr(errors, 'PhoneCodeExpiredError', _dummy_exc('PhoneCodeExpiredError'))
MessageIdInvalidError = getattr(errors, 'MessageIdInvalidError', _dummy_exc('MessageIdInvalidError'))
UserNotParticipantError = getattr(errors, 'UserNotParticipantError', _dummy_exc('UserNotParticipantError'))
ChatAdminRequiredError = getattr(errors, 'ChatAdminRequiredError', _dummy_exc('ChatAdminRequiredError'))
ChannelPrivateError = getattr(errors, 'ChannelPrivateError', _dummy_exc('ChannelPrivateError'))
RPCError = getattr(errors, 'RPCError', _dummy_exc('RPCError'))
ServerError = getattr(errors, 'ServerError', _dummy_exc('ServerError'))


def map_telethon_exception(exc):
    """Map a Telethon exception instance to an action/status/event mapping.

    Returns a dict with keys:
      - action: 'mark_status'|'set_flood_wait'|'retry'|'ignore'|'stop'|'unknown'
      - status: AccountStatus or None
      - event_code: reporter event code string
      - message: short human message
      - details: full exception string
      - retry: bool
      - flood_seconds: int or None
    """
    mapping = {
        'action': 'unknown',
        'status': None,
        'event_code': 'error.unknown',
        'message': str(exc),
        'details': repr(exc),
        'retry': False,
        'flood_seconds': None,
    }

    # Helper to safely check for newer exceptions
    SessionRevoked = SessionRevokedError

    if isinstance(exc, (AuthKeyUnregisteredError, AuthKeyInvalidError)) or (SessionRevoked and isinstance(exc, SessionRevoked)):
        mapping.update({'action': 'mark_status', 'status': AccountStatus.AUTH_KEY_INVALID, 'event_code': 'error.session_invalid', 'message': 'Session invalid/expired or revoked', 'retry': False})
    elif isinstance(exc, FloodWaitError):
        mapping.update({'action': 'set_flood_wait', 'status': AccountStatus.ERROR, 'event_code': 'error.flood_wait', 'message': 'Flood wait', 'retry': False, 'flood_seconds': getattr(exc, 'seconds', None)})
    elif isinstance(exc, UserDeactivatedBanError):
        mapping.update({'action': 'mark_status', 'status': AccountStatus.DEACTIVATED, 'event_code': 'error.user_deactivated', 'message': 'Account deactivated', 'retry': False})
    elif isinstance(exc, PhoneNumberBannedError):
        mapping.update({'action': 'mark_status', 'status': AccountStatus.BANNED, 'event_code': 'error.phone_banned', 'message': 'Phone number banned', 'retry': False})
    elif isinstance(exc, PhoneNumberInvalidError):
        mapping.update({'action': 'mark_status', 'status': AccountStatus.ERROR, 'event_code': 'error.phone_invalid', 'message': 'Phone number invalid', 'retry': False})
    elif isinstance(exc, SessionPasswordNeededError):
        mapping.update({'action': 'stop', 'status': AccountStatus.ERROR, 'event_code': 'error.2fa_required', 'message': '2FA required', 'retry': False})
    elif isinstance(exc, (PhoneCodeInvalidError, PhoneCodeExpiredError)):
        mapping.update({'action': 'stop', 'status': AccountStatus.ERROR, 'event_code': 'error.phone_code_invalid', 'message': 'Phone code invalid/expired', 'retry': False})
    elif isinstance(exc, MessageIdInvalidError):
        mapping.update({'action': 'ignore', 'status': None, 'event_code': 'error.message_id_invalid', 'message': 'MessageId invalid', 'retry': False})
    elif isinstance(exc, UserNotParticipantError):
        mapping.update({'action': 'ignore', 'status': None, 'event_code': 'error.not_participant', 'message': 'User not participant', 'retry': False})
    elif isinstance(exc, ChatAdminRequiredError):
        mapping.update({'action': 'ignore', 'status': None, 'event_code': 'error.admin_required', 'message': 'Admin privileges required', 'retry': False})
    elif isinstance(exc, ChannelPrivateError):
        mapping.update({'action': 'ignore', 'status': None, 'event_code': 'error.channel_private', 'message': 'Channel is private', 'retry': False})
    elif isinstance(exc, RPCError):
        mapping.update({'action': 'retry', 'status': None, 'event_code': 'error.rpc', 'message': 'RPC error', 'retry': True})
    elif isinstance(exc, ServerError):
        mapping.update({'action': 'retry', 'status': None, 'event_code': 'error.server', 'message': 'Server error', 'retry': True})
    elif isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError, ConnectionError)):
        mapping.update({'action': 'retry', 'status': None, 'event_code': 'error.network', 'message': 'Network error', 'retry': True})
    else:
        # Fallback: mark as ERROR and surface the event for manual inspection
        mapping.update({'action': 'mark_status', 'status': AccountStatus.ERROR, 'event_code': 'error.unknown', 'message': 'Unknown error', 'retry': False})

    return mapping


def reporter_payload_from_mapping(mapping, exc=None, extra=None):
    payload = {
        'message_code': mapping.get('event_code'),
        'message': mapping.get('message'),
        'details': mapping.get('details') if mapping.get('details') else (repr(exc) if exc else None)
    }
    if extra and isinstance(extra, dict):
        payload.update(extra)
    return payload
