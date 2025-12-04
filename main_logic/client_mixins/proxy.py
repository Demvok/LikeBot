"""
Proxy configuration mixin for Telegram client.

Selects proxies from the account's assigned list and builds Telethon configurations.
Falls back according to proxy mode (strict vs soft).
"""


class ProxyMixin:
    """Handles proxy configuration and fallback logic."""

    async def _get_proxy_config(self, proxy_mode: str = 'soft'):
        """
        Select a proxy assigned to this account.

        Returns (proxy_candidates, proxy_data). When no proxy is available and
        the mode is non-strict, returns (None, None). Raises RuntimeError when
        strict mode is configured but no usable proxy exists.
        """
        from random import shuffle
        from auxilary_logic.proxy import build_proxy_candidates
        from main_logic.database import get_db

        assigned_proxies = getattr(self.account, 'assigned_proxies', []) or []
        proxy_mode_normalized = (proxy_mode or 'soft').lower()

        if not assigned_proxies:
            self.logger.warning(
                f"Account {self.phone_number} has no assigned proxies; proxy.mode={proxy_mode_normalized}"
            )
            if proxy_mode_normalized == 'strict':
                raise RuntimeError("Proxy required but none assigned to account")
            self.proxy_name = None
            return None, None

        db = get_db()
        shuffled = list(assigned_proxies)
        shuffle(shuffled)

        for proxy_name in shuffled:
            proxy_data = await db.get_proxy(proxy_name)
            if not proxy_data:
                self.logger.warning(f"Assigned proxy {proxy_name} not found in database")
                continue
            if not proxy_data.get('active', True):
                self.logger.warning(f"Assigned proxy {proxy_name} is inactive; skipping")
                continue

            candidates = build_proxy_candidates(proxy_data, self.logger)
            if not candidates:
                self.logger.warning(f"Assigned proxy {proxy_name} has no valid endpoints")
                continue

            self.proxy_name = proxy_data.get('proxy_name')
            self.logger.info(
                f"Selected proxy {self.proxy_name} for account {self.phone_number} from assigned list"
            )
            return candidates, proxy_data

        self.logger.warning(
            f"No usable proxies among assigned list for account {self.phone_number}; mode={proxy_mode_normalized}"
        )
        self.proxy_name = None
        if proxy_mode_normalized == 'strict':
            raise RuntimeError("Strict proxy mode requires a usable assigned proxy")
        return None, None
