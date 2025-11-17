import unittest
from types import SimpleNamespace

import auxilary_logic.telethon_error_handler as telethon_error_handler
from main_logic.schemas import AccountStatus


class FakeErrors:
    class AuthKeyUnregisteredError(Exception):
        pass

    class FloodWaitError(Exception):
        def __init__(self, seconds=None):
            super().__init__(f"Flood wait {seconds}")
            self.seconds = seconds

    class PhoneNumberBannedError(Exception):
        pass


class TestTelethonErrorHandler(unittest.TestCase):

    def setUp(self):
        # Monkeypatch the errors namespace inside telethon_error_handler
        self._orig_errors = telethon_error_handler.errors
        self._orig_local = {
            'AuthKeyUnregisteredError': getattr(telethon_error_handler, 'AuthKeyUnregisteredError', None),
            'AuthKeyInvalidError': getattr(telethon_error_handler, 'AuthKeyInvalidError', None),
            'FloodWaitError': getattr(telethon_error_handler, 'FloodWaitError', None),
            'PhoneNumberBannedError': getattr(telethon_error_handler, 'PhoneNumberBannedError', None),
        }
        telethon_error_handler.errors = FakeErrors
        # Patch local defensive names as well
        telethon_error_handler.AuthKeyUnregisteredError = FakeErrors.AuthKeyUnregisteredError
        telethon_error_handler.AuthKeyInvalidError = FakeErrors.AuthKeyUnregisteredError
        telethon_error_handler.FloodWaitError = FakeErrors.FloodWaitError
        telethon_error_handler.PhoneNumberBannedError = FakeErrors.PhoneNumberBannedError

    def tearDown(self):
        telethon_error_handler.errors = self._orig_errors
        # Restore local defensive names
        for k, v in self._orig_local.items():
            if v is not None:
                setattr(telethon_error_handler, k, v)

    def test_auth_key_unregistered_maps_to_auth_key_invalid(self):
        exc = FakeErrors.AuthKeyUnregisteredError("expired")
        mapping = telethon_error_handler.map_telethon_exception(exc)
        self.assertEqual(mapping['status'], AccountStatus.AUTH_KEY_INVALID)
        self.assertEqual(mapping['action'], 'mark_status')
        self.assertEqual(mapping['event_code'], 'error.session_invalid')

    def test_flood_wait_maps_to_flood_wait(self):
        exc = FakeErrors.FloodWaitError(seconds=42)
        mapping = telethon_error_handler.map_telethon_exception(exc)
        self.assertEqual(mapping['action'], 'set_flood_wait')
        # FLOOD_WAIT was removed from AccountStatus; flood-wait maps to ERROR
        self.assertEqual(mapping['status'], AccountStatus.ERROR)
        self.assertEqual(mapping['flood_seconds'], 42)
        self.assertEqual(mapping['event_code'], 'error.flood_wait')

    def test_phone_banned_maps_to_banned(self):
        exc = FakeErrors.PhoneNumberBannedError("banned")
        mapping = telethon_error_handler.map_telethon_exception(exc)
        self.assertEqual(mapping['status'], AccountStatus.BANNED)
        self.assertEqual(mapping['action'], 'mark_status')
        self.assertEqual(mapping['event_code'], 'error.phone_banned')

    def test_reporter_payload_contains_expected_fields(self):
        mapping = {
            'event_code': 'error.foo',
            'message': 'An error',
            'details': 'detailed'
        }
        payload = telethon_error_handler.reporter_payload_from_mapping(mapping, exc=None, extra={'client': '+123'})
        self.assertIn('message_code', payload)
        self.assertIn('message', payload)
        self.assertIn('details', payload)
        self.assertEqual(payload['message_code'], 'error.foo')
        self.assertEqual(payload['client'], '+123')


if __name__ == '__main__':
    unittest.main()
