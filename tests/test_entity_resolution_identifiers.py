import pytest
from unittest.mock import MagicMock

from main_logic.client_mixins.entity_resolution import EntityResolutionMixin


class DummyEntityResolver(EntityResolutionMixin):
    def __init__(self):
        self.logger = MagicMock()
        self.telegram_cache = None
        self.client = MagicMock()


def test_extract_identifier_preserves_case():
    resolver = DummyEntityResolver()

    identifier = resolver._extract_identifier_from_link("https://t.me/UmanMVG/123")

    assert identifier == "UmanMVG"


def test_url_alias_still_normalizes_to_lowercase():
    resolver = DummyEntityResolver()

    alias = resolver._get_url_alias_from_link("https://t.me/UmanMVG/123")

    assert alias == "umanmvg"


def test_sanitize_username_strips_prefix_without_lowering():
    resolver = DummyEntityResolver()

    sanitized = resolver._sanitize_username_identifier(" @MixedCase ")

    assert sanitized == "MixedCase"
    assert resolver._normalize_url_identifier(" @MixedCase ") == "mixedcase"
