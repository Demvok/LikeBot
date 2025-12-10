"""Utility helpers for importing proxy dumps into LikeBot-compatible records.

Example:
    from utils.proxy_importer import convert_proxy_file
    from main_logic.database import get_db

    proxies = convert_proxy_file("provider_dump.csv")
    db = get_db()
    for proxy in proxies:
        await db.add_proxy(proxy)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Union
import re
import unicodedata

SUPPORTED_PROXY_TYPES = ("socks5", "socks4", "http")
DEFAULT_FIELD_ORDER = ("host", "port", "username", "password")
FIELD_ALIASES = {
    "host": "host",
    "ip": "host",
    "addr": "host",
    "address": "host",
    "port": "port",
    "username": "username",
    "user": "username",
    "login": "username",
    "password": "password",
    "pass": "password",
    "pwd": "password",
}


class ProxyParseError(ValueError):
    """Raised when a proxy dump cannot be parsed."""


@dataclass(frozen=True)
class HeaderInfo:
    proxy_type: str
    field_order: Sequence[str]


def convert_proxy_file(
    file_path: Union[str, Path],
    *,
    proxy_type: str | None = None,
    base_name: str | None = None,
) -> List[dict]:
    """Read a proxy dump file and return LikeBot proxy records ready for DB insertion.

    Args:
        file_path: CSV-like file that stores `host:port:username:password` rows.
        proxy_type: Override proxy type if it cannot be inferred from the header.
        base_name: Optional prefix for generated `proxy_name` values.

    Returns:
        List of dictionaries that can be passed directly to `db.add_proxy`.
    """

    path = Path(file_path)
    text = path.read_text(encoding="utf-8-sig")
    return parse_proxy_lines(
        text.splitlines(),
        proxy_type=proxy_type,
        base_name=base_name or path.stem,
        source_name=path.name,
    )


def parse_proxy_lines(
    lines: Sequence[str],
    *,
    proxy_type: str | None = None,
    base_name: str | None = None,
    source_name: str | None = None,
) -> List[dict]:
    """Convert raw lines into LikeBot proxy records.

    Use this helper if the proxy data already lives in memory instead of disk.
    The input should exclude blank lines and must follow `host:port:user:pass` order.
    """

    cleaned = [line.strip() for line in lines if line.strip()]
    if not cleaned:
        return []

    header_line = cleaned[0]
    has_header = not _looks_like_data_line(header_line)

    data_lines = cleaned[1:] if has_header else cleaned
    if not data_lines:
        return []

    header_info = _build_header_info(header_line if has_header else None, proxy_type)
    prefix = base_name or source_name or "proxy"

    records: List[dict] = []
    used_names: set[str] = set()
    for idx, line in enumerate(data_lines, start=1 if has_header else 0):
        if not line or line.startswith("#"):
            continue
        row = _parse_data_line(line, header_info.field_order)
        record = _build_proxy_record(
            row,
            header_info.proxy_type,
            prefix,
            used_names,
            source_name,
            line_number=idx + (1 if has_header else 0),
        )
        records.append(record)

    return records


def _build_header_info(header_line: str | None, override_type: str | None) -> HeaderInfo:
    if header_line:
        detected_type = _extract_proxy_type(header_line)
        field_order = _extract_field_order(header_line)
    else:
        detected_type = None
        field_order = DEFAULT_FIELD_ORDER

    proxy_type = (override_type or detected_type or "socks5").lower()
    if proxy_type not in SUPPORTED_PROXY_TYPES:
        raise ProxyParseError(f"Unsupported proxy type '{proxy_type}'.")

    return HeaderInfo(proxy_type=proxy_type, field_order=field_order)


def _extract_proxy_type(header_line: str) -> str | None:
    lowered = header_line.lower()
    for candidate in SUPPORTED_PROXY_TYPES:
        if candidate in lowered:
            return candidate
    return None


def _extract_field_order(header_line: str) -> Sequence[str]:
    match = re.search(r"format\s*:\s*(.+)", header_line, flags=re.IGNORECASE)
    if not match:
        return DEFAULT_FIELD_ORDER

    raw = match.group(1)
    normalized = []
    for token in raw.split(":"):
        token = token.strip().lower()
        if not token:
            continue
        mapped = FIELD_ALIASES.get(token, token)
        normalized.append(mapped)

    if not normalized:
        return DEFAULT_FIELD_ORDER

    seen = set(normalized)
    for required in ("host", "port"):
        if required not in seen:
            normalized.append(required)
    return tuple(normalized)


def _looks_like_data_line(line: str) -> bool:
    # Data lines contain at least three separators and start with IPv4/hostname characters
    if line.count(":") < 3:
        return False
    first_segment = line.split(":", 1)[0]
    return bool(re.fullmatch(r"[0-9A-Za-z\.\-]+", first_segment))


def _parse_data_line(line: str, field_order: Sequence[str]) -> dict:
    parts = [segment.strip() for segment in line.split(":")]
    expected = len(field_order)
    if len(parts) < expected:
        raise ProxyParseError(
            f"Line '{line}' has {len(parts)} parts, expected at least {expected}."
        )

    if len(parts) == expected:
        values = parts
    else:
        # Merge surplus separators into the first field (usually host/IP)
        head = len(parts) - (expected - 1)
        host = ":".join(parts[:head])
        values = [host] + parts[head:]

    row = {}
    for field_name, raw_value in zip(field_order, values):
        row[field_name] = raw_value
    return row


def _build_proxy_record(
    row: dict,
    proxy_type: str,
    prefix: str,
    used_names: set[str],
    source_name: str | None,
    *,
    line_number: int,
) -> dict:
    host = (row.get("host") or row.get("ip") or row.get("addr") or "").strip()
    port_raw = (row.get("port") or "").strip()

    if not host:
        raise ProxyParseError(f"Missing host on line {line_number}.")

    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ProxyParseError(
            f"Invalid port '{port_raw}' on line {line_number}: {exc}"
        ) from exc

    username = (row.get("username") or row.get("login") or "").strip() or None
    password = (row.get("password") or row.get("pass") or "").strip() or None

    proxy_name = _unique_proxy_name(prefix, host, port, used_names)

    record: dict = {
        "proxy_name": proxy_name,
        "host": host,
        "port": port,
        "type": proxy_type,
        "rdns": True,
        "active": True,
    }

    if proxy_type == "socks5":
        record["socks5_port"] = port
    elif proxy_type == "socks4":
        record["socks_port"] = port
    elif proxy_type == "http":
        record["http_port"] = port

    if username:
        record["username"] = username
    if password:
        record["password"] = password

    if source_name:
        record["notes"] = f"Imported from {source_name}"

    used_names.add(proxy_name)
    return record


def _unique_proxy_name(prefix: str, host: str, port: int, used_names: set[str]) -> str:
    base_parts = [part for part in (_slugify(prefix), _slugify(host), str(port)) if part]
    base = "-".join(base_parts) or f"proxy-{port}"
    candidate = base
    counter = 2
    while candidate in used_names:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.lower()
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text)
    slug = ascii_text.strip("-")
    if slug:
        return slug

    escaped = text.encode("unicode_escape").decode("ascii").lower()
    escaped = re.sub(r"[^a-z0-9]+", "-", escaped)
    escaped = escaped.strip("-")
    return escaped or "proxy"


__all__ = [
    "convert_proxy_file",
    "parse_proxy_lines",
    "ProxyParseError",
]
