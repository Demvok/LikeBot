import argparse
import asyncio
import json
import sys
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

try:
    import websockets  # type: ignore
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(
        "websockets package is required. Install with 'pip install websockets'\n"
    )
    sys.exit(1)


def build_ws_url(
    base_url: str,
    log_file: Optional[str],
    tail: Optional[int],
    token: Optional[str],
) -> str:
    """Attach query parameters for log streaming to the websocket URL."""
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))

    if log_file:
        query["log_file"] = log_file
    if tail is not None:
        query["tail"] = str(tail)
    if token:
        query["token"] = token

    encoded_query = urlencode(query)
    return urlunparse(parsed._replace(query=encoded_query))


def derive_login_url(ws_url: str) -> str:
    """Translate the websocket endpoint to the corresponding HTTP login endpoint."""
    parsed = urlparse(ws_url)
    if parsed.scheme not in {"ws", "wss"}:
        raise ValueError("Websocket URL must start with ws:// or wss://")

    http_scheme = "https" if parsed.scheme == "wss" else "http"
    return urlunparse((http_scheme, parsed.netloc, "/auth/login", "", "", ""))


def fetch_access_token(ws_url: str, username: str, password: str) -> str:
    """Obtain a JWT access token using the admin credentials."""
    login_url = derive_login_url(ws_url)
    try:
        response = requests.post(
            login_url,
            data={
                "username": username,
                "password": password,
                "grant_type": "password",
                "scope": "",
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to connect to login endpoint: {exc}") from exc

    if response.status_code != 200:
        detail = response.text.strip()
        raise RuntimeError(
            f"Login failed with status code {response.status_code}: {detail or 'No response body'}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Login response was not valid JSON") from exc

    token = payload.get("access_token")
    if not token:
        raise RuntimeError("Login response did not include an access token")

    return token


async def stream_logs(url: str) -> None:
    """Connect to the websocket and print incoming log lines."""
    print(f"Connecting to {url} ...")
    try:
        async with websockets.connect(url) as websocket:  # type: ignore[attr-defined]
            print("Connected. Press Ctrl+C to stop.\n")
            async for message in websocket:
                timestamp = datetime.utcnow().strftime("%H:%M:%S")
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    print(f"[{timestamp}] {message}")
                    continue

                if isinstance(payload, dict) and payload.get("type") == "error":
                    print(f"[{timestamp}] ERROR: {payload.get('message', 'Unknown error')}")
                    break

                print(f"[{timestamp}] {payload}")
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    except Exception as exc:
        print(f"Connection failed: {exc}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stream LikeBot logs via websocket.")
    parser.add_argument(
        "--url",
        default="ws://127.0.0.1:8080/ws/logs",
        help="Websocket endpoint (default: ws://127.0.0.1:8080/ws/logs)",
    )
    parser.add_argument(
        "--log-file",
        default="main.log",
        help="Log file name to stream (default: main.log)",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=200,
        help="Number of trailing lines to send on connect (default: 200)",
    )
    parser.add_argument(
        "--username",
        default="admin",
        help="Username used to authenticate before opening the websocket (default: admin)",
    )
    parser.add_argument(
        "--password",
        default="LKgfst532!$sLL",
        help="Password used to authenticate before opening the websocket (default: admin123)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        token = fetch_access_token(args.url, args.username, args.password)
    except RuntimeError as exc:
        print(f"Authentication failed: {exc}")
        sys.exit(1)

    url = build_ws_url(args.url, args.log_file, args.tail, token)
    asyncio.run(stream_logs(url))


if __name__ == "__main__":
    main()

# python misc/ws_log_viewer.py --url ws://51.222.86.239:8080/ws/logs --log-file main.log --tail 100
# python misc/ws_log_viewer.py --url ws://127.0.0.1:8080/ws/logs --log-file main.log --tail 100