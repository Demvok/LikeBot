"""Utilities for verifying proxy connectivity via public HTTP endpoints."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

import requests

DEFAULT_TEST_URL = "https://2ip.ua/api/index.php?type=json"
_HEADERS = {
    "Accept": "application/json, text/plain;q=0.8, */*;q=0.5",
    "User-Agent": "LikeBot/ProxyTester (+https://github.com/Demvok/LikeBot)",
}


class ProxyTestResponse:
    """Structured payload returned by the proxy tester."""

    def __init__(self, *, proxy_name: Optional[str], endpoint: str, target_url: str, latency_ms: float,
                 status_code: int, details: Dict[str, Any]):
        self.proxy_name = proxy_name
        self.endpoint = endpoint
        self.target_url = target_url
        self.latency_ms = latency_ms
        self.status_code = status_code
        self.details = details

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proxy_name": self.proxy_name,
            "endpoint": self.endpoint,
            "target_url": self.target_url,
            "latency_ms": round(self.latency_ms, 2),
            "status_code": self.status_code,
            "details": self.details,
        }


def _normalize_host(proxy_data: Dict[str, Any]) -> str:
    host = proxy_data.get('host') or proxy_data.get('ip') or proxy_data.get('addr')
    if not host:
        raise ValueError("Proxy entry is missing host/ip field")
    return host


def _build_endpoint_strings(proxy_data: Dict[str, Any]) -> List[str]:
    host = _normalize_host(proxy_data)
    username = proxy_data.get('username') or proxy_data.get('login')
    password = proxy_data.get('password')
    rdns_enabled = proxy_data.get('rdns', True)

    def build_auth() -> str:
        if not username:
            return ''
        user = quote(str(username), safe='')
        if password is None:
            return f"{user}@"
        return f"{user}:{quote(str(password), safe='')}@"

    def normalize_scheme(scheme: str) -> str:
        lowered = scheme.lower()
        if lowered in ('socks5', 'socks5h'):
            return 'socks5h' if rdns_enabled else 'socks5'
        if lowered in ('socks4', 'socks4a'):
            return 'socks4a' if rdns_enabled else 'socks4'
        return lowered

    endpoints: List[str] = []
    seen = set()

    def add_endpoint(scheme: str, port_value: Any):
        if port_value is None:
            return
        try:
            port = int(port_value)
        except (TypeError, ValueError):
            return
        normalized_scheme = normalize_scheme(scheme)
        key = (normalized_scheme, port)
        if key in seen:
            return
        seen.add(key)
        auth = build_auth()
        endpoints.append(f"{normalized_scheme}://{auth}{host}:{port}")

    add_endpoint('socks5', proxy_data.get('socks5_port'))
    add_endpoint('socks5', proxy_data.get('socks_port'))
    add_endpoint('http', proxy_data.get('http_port'))

    fallback_scheme = (proxy_data.get('type') or 'socks5').lower()
    fallback_scheme = fallback_scheme if fallback_scheme in ('socks4', 'socks5', 'http', 'socks4a', 'socks5h') else 'socks5'
    add_endpoint(fallback_scheme, proxy_data.get('port'))

    return endpoints


def _parse_json_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    lowered = {k.lower(): v for k, v in payload.items()}
    city = lowered.get('city') or lowered.get('town')
    region = lowered.get('region')
    country = lowered.get('country') or lowered.get('country_rus') or lowered.get('country_ua')
    location_parts = [part for part in (city, region, country) if part]
    location = ", ".join(location_parts) if location_parts else lowered.get('location')

    return {
        'ip': lowered.get('ip') or lowered.get('ip_address'),
        'hostname': lowered.get('hostname') or lowered.get('host'),
        'provider': lowered.get('provider') or lowered.get('organization') or lowered.get('org'),
        'location': location,
        'raw': payload,
    }


def _parse_text_payload(text: str) -> Dict[str, Any]:
    lines = {}
    for line in text.splitlines():
        if ':' not in line:
            continue
        key, value = line.split(':', 1)
        lines[key.strip().lower()] = value.strip()
    location = lines.get('location')
    if not location:
        location = ", ".join([value for key, value in lines.items() if key in ('city', 'region', 'country') and value])

    return {
        'ip': lines.get('ip'),
        'hostname': lines.get('hostname') or lines.get('host'),
        'provider': lines.get('provider'),
        'location': location,
        'raw': lines,
    }


def _parse_probe_response(response: requests.Response) -> Dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict) and payload:
        return _parse_json_payload(payload)

    if response.text:
        parsed = _parse_text_payload(response.text)
        if any(parsed.get(field) for field in ('ip', 'hostname', 'provider', 'location')):
            return parsed

    raise RuntimeError("Probe response did not contain recognizable IP metadata")


def _request_via_proxy(endpoint: str, url: str, timeout_seconds: float) -> requests.Response:
    proxies = {'http': endpoint, 'https': endpoint}
    response = requests.get(url, proxies=proxies, timeout=timeout_seconds, headers=_HEADERS)
    response.raise_for_status()
    return response


def run_proxy_probe(proxy_data: Dict[str, Any], test_url: Optional[str] = None, timeout_seconds: float = 15,
                    request_func=None) -> ProxyTestResponse:
    if not proxy_data:
        raise ValueError("proxy_data is required")
    if not proxy_data.get('active', True):
        raise ValueError("Proxy is inactive and cannot be tested")

    url = test_url or DEFAULT_TEST_URL
    endpoints = _build_endpoint_strings(proxy_data)
    if not endpoints:
        raise ValueError("Proxy entry does not have usable host/port combinations")

    request = request_func or _request_via_proxy
    attempts: List[Dict[str, Any]] = []

    for endpoint in endpoints:
        start = time.perf_counter()
        try:
            response = request(endpoint, url, timeout_seconds)
            details = _parse_probe_response(response)
            latency_ms = (time.perf_counter() - start) * 1000.0
            return ProxyTestResponse(
                proxy_name=proxy_data.get('proxy_name'),
                endpoint=endpoint,
                target_url=url,
                latency_ms=latency_ms,
                status_code=response.status_code,
                details=details,
            )
        except Exception as exc:  # pragma: no cover - aggregated for error reporting
            attempts.append({'endpoint': endpoint, 'error': str(exc)})
            continue

    raise RuntimeError(f"Failed to reach {url} through proxy '{proxy_data.get('proxy_name')}'. Attempts: {attempts}")


async def test_proxy_connectivity(proxy_data: Dict[str, Any], test_url: Optional[str] = None,
                                  timeout_seconds: float = 15) -> Dict[str, Any]:
    response = await asyncio.to_thread(run_proxy_probe, proxy_data, test_url, timeout_seconds, None)
    return response.to_dict()
