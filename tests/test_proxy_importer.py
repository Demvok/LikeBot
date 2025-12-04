import pytest

from utils.proxy_importer import parse_proxy_lines, ProxyParseError


def test_parse_proxy_lines_with_header_and_unicode_prefix():
    lines = [
        "\u041f\u0440\u043e\u043a\u0441\u0456,socks5, format: Host:Port:Username:Password",
        "193.193.217.82:2832:57537:aOHR0OOo",
    ]

    result = parse_proxy_lines(lines, source_name="\u041f\u0440\u043e\u043a\u0441\u0456.csv")

    assert len(result) == 1
    record = result[0]
    assert record["host"] == "193.193.217.82"
    assert record["socks5_port"] == 2832
    assert record["username"] == "57537"
    assert record["password"] == "aOHR0OOo"
    assert record["proxy_name"].endswith("193-193-217-82-2832")
    assert record["notes"] == "Imported from \u041f\u0440\u043e\u043a\u0441\u0456.csv"


def test_parse_proxy_lines_rejects_short_rows():
    with pytest.raises(ProxyParseError):
        parse_proxy_lines([
            "proxies format: host:port:user:pass",
            "host-only:8080",
        ], source_name="broken.csv")
