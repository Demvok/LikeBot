"""
Proxy management utilities for Telegram client connections.

Provides functions to:
- Build Telethon-compatible proxy configurations from database records
- Generate multiple proxy candidates from a single DB record with multiple ports
- Select and validate proxy configurations
"""

from utils.logger import setup_logger

logger = setup_logger("proxy", "main.log")


def build_proxy_dict(proxy_data, logger_instance=None):
    """
    Build a telethon-compatible proxy configuration dictionary.
    
    Args:
        proxy_data: Proxy data from database (dict)
        logger_instance: Optional logger instance (defaults to module logger)
        
    Returns:
        Dictionary compatible with TelegramClient proxy parameter, or None if invalid
        
    Expected proxy_data fields:
        - type: 'socks5', 'socks4', or 'http' (default: 'socks5')
        - host/ip/addr: proxy hostname or IP
        - port/socks5_port/http_port: port number(s)
        - username/login: optional authentication username
        - password: optional authentication password
        - rdns: optional remote DNS resolution (default: True)
    """
    if not proxy_data:
        return None
    
    log = logger_instance or logger
    
    try:
        import socks  # PySocks, installed as dependency of telethon
    except ImportError:
        log.error("PySocks not installed. Install with: pip install PySocks")
        return None
    
    # Map proxy type string to socks constant
    proxy_type_map = {
        'socks5': socks.SOCKS5,
        'socks4': socks.SOCKS4,
        'http': socks.HTTP
    }
    
    proxy_type = proxy_data.get('type', 'socks5').lower()
    if proxy_type not in proxy_type_map:
        log.error(f"Unsupported proxy type: {proxy_type}")
        return None

    # Host can be stored under 'host', 'ip' or 'addr' in some DBs
    host = proxy_data.get('host') or proxy_data.get('ip') or proxy_data.get('addr')

    # Determine port: support protocol-specific fields like 'socks5_port' or 'http_port'
    port = None
    try:
        if proxy_type == 'socks5':
            for key in ('socks5_port', 'socks_port', 'port'):
                val = proxy_data.get(key)
                if val is not None:
                    port = int(val)
                    break
        elif proxy_type == 'http':
            for key in ('http_port', 'port'):
                val = proxy_data.get(key)
                if val is not None:
                    port = int(val)
                    break
        else:
            # generic fallback
            val = proxy_data.get('port')
            port = int(val) if val is not None else None
    except (ValueError, TypeError) as e:
        log.error(f"Invalid port value in proxy data: {e}")
        return None

    if port is None:
        log.error(f"No port found for proxy (type={proxy_type}). Data keys: {list(proxy_data.keys())}")
        return None

    proxy_dict = {
        'proxy_type': proxy_type_map[proxy_type],
        'addr': host,
        'port': port,
        'rdns': proxy_data.get('rdns', True)
    }

    # Add credentials if provided. Accept both 'username' and legacy 'login' field.
    username = proxy_data.get('username') or proxy_data.get('login')
    if username:
        proxy_dict['username'] = username
    if proxy_data.get('password'):
        proxy_dict['password'] = proxy_data.get('password')

    log.debug(f"Built proxy config: {proxy_type}://{proxy_dict['addr']}:{proxy_dict['port']}")
    return proxy_dict


def build_proxy_candidates(proxy_data, logger_instance=None):
    """
    Build multiple proxy candidate configurations from a single DB record.
    
    Supports records with multiple port fields (socks5_port, http_port, etc.)
    and returns candidates in preference order: socks5 -> http -> generic port.
    
    Args:
        proxy_data: Proxy data from database (dict)
        logger_instance: Optional logger instance (defaults to module logger)
        
    Returns:
        List of proxy dicts compatible with TelegramClient, or empty list if none valid
    """
    if not proxy_data:
        return []
    
    log = logger_instance or logger
    candidates = []

    # Prefer explicit socks5_port
    if proxy_data.get('socks5_port') is not None:
        p = dict(proxy_data)
        p['type'] = 'socks5'
        p['port'] = proxy_data.get('socks5_port')
        candidate = build_proxy_dict(p, log)
        if candidate:
            candidates.append(candidate)

    # Also accept 'socks_port' as an alternate name
    if proxy_data.get('socks_port') is not None:
        p = dict(proxy_data)
        p['type'] = 'socks5'
        p['port'] = proxy_data.get('socks_port')
        candidate = build_proxy_dict(p, log)
        if candidate:
            candidates.append(candidate)

    # HTTP candidate
    if proxy_data.get('http_port') is not None:
        p = dict(proxy_data)
        p['type'] = 'http'
        p['port'] = proxy_data.get('http_port')
        candidate = build_proxy_dict(p, log)
        if candidate:
            candidates.append(candidate)

    # Fallback to generic 'port' field (respecting proxy_data.type)
    if proxy_data.get('port') is not None:
        p = dict(proxy_data)
        p['port'] = proxy_data.get('port')
        candidate = build_proxy_dict(p, log)
        if candidate:
            candidates.append(candidate)

    return candidates


async def get_proxy_config(phone_number, logger_instance=None):
    """
    Get proxy configuration for a connection.
    
    Selects the least-used active proxy for load balancing and builds
    candidate proxy configurations.
    
    Args:
        phone_number: Phone number for logging context
        logger_instance: Optional logger instance (defaults to module logger)
        
    Returns:
        Tuple of (candidates_list, proxy_data_dict) where:
        - candidates_list: List of proxy dicts to try (ordered by preference)
        - proxy_data_dict: Raw proxy data from database
        Returns (None, None) if no proxies available
    """
    from main_logic.database import get_db
    
    log = logger_instance or logger
    db = get_db()
    
    # Get least used active proxy (balances on every connection)
    proxy_data = await db.get_least_used_proxy()
    if not proxy_data:
        log.info("No active proxies available, connecting without proxy")
        return None, None

    proxy_name = proxy_data.get('proxy_name')
    log.info(f"Selected proxy {proxy_name} for {phone_number} (current usage: {proxy_data.get('connected_accounts', 0)})")

    # Build candidate proxies from the single DB record
    candidates = build_proxy_candidates(proxy_data, log)

    if not candidates:
        log.info("No valid proxy candidates found in selected proxy record; connecting without proxy")
        return None, None

    return candidates, proxy_data
