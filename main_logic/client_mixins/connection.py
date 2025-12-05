"""
Connection management mixin for Telegram client.

Orchestrates TelegramClient lifecycle using SessionMixin, ProxyMixin, and LockingMixin.
Handles connection, disconnection, session validation, and error recovery.
"""

import os
from telethon import TelegramClient, errors
from dotenv import load_dotenv

from utils.logger import load_config
from main_logic.schemas import AccountStatus
from auxilary_logic.telethon_error_handler import map_telethon_exception
from utils.retry import RetryContext
from .session import SessionMixin
from .proxy import ProxyMixin
from .locking import LockingMixin

load_dotenv()
api_id = os.getenv('api_id')
api_hash = os.getenv('api_hash')

config = load_config()


class ConnectionMixin(SessionMixin, ProxyMixin, LockingMixin):
    """
    Orchestrates connection lifecycle using SessionMixin, ProxyMixin, LockingMixin.
    Inherits from all three to compose their functionality.
    """
    
    async def connect(self, task_id: int = None):
        """
        Connect the client to Telegram.
        
        Args:
            task_id: Optional task ID for account locking. If provided, acquires
                     a lock on this account. If the account is already locked by
                     another task, logs a warning but continues (best-effort locking).
        """
        # Attempt to acquire lock if task_id provided (from LockingMixin)
        await self._acquire_lock(task_id)
        
        # Proxy configuration
        proxy_mode = (config.get('proxy', {}).get('mode', 'soft') or 'soft').lower()  # 'strict' or 'soft'
        
        session_created = False
        force_new_session = False
        proxy_failed = False
        
        async with RetryContext(
            retries_key='connection_retries',
            delay_key='reconnect_delay',
            logger=self.logger
        ) as ctx:
            while ctx.should_retry():
                try:    
                    # Try to get session only once per connect() call, unless we need to force a new one
                    if not session_created or force_new_session:
                        try:
                            session = await self._get_session(force_new=force_new_session)  # from SessionMixin
                            session_created = True
                            force_new_session = False  # Reset the flag
                        except Exception as e:
                            self.logger.error(f"Session creation failed: {e}")
                            # Don't retry session creation here - it has its own retry logic
                            raise
                    
                    # Get proxy configuration (skip if proxy already failed in strict mode)
                    if proxy_mode == 'strict' and proxy_failed:
                        raise ConnectionError("Strict proxy mode: Proxy connection failed, cannot proceed")
                    
                    # Get proxy (or None if soft mode and proxy previously failed)
                    if proxy_failed and proxy_mode == 'soft':
                        proxy_candidates, proxy_data = None, None
                        self.logger.info(f"Connecting without proxy (fallback after proxy failure)")
                    else:
                        proxy_candidates, proxy_data = await self._get_proxy_config(proxy_mode)  # from ProxyMixin
                        if proxy_candidates:
                            self.logger.info(f"Connecting with proxy record: {proxy_data.get('proxy_name')}")
                        else:
                            self.logger.info(f"Connecting without proxy")

                        selected_candidate = None
                        # If proxy_candidates is a list, try them in order until one connects
                        if proxy_candidates:
                            from main_logic.database import get_db
                            db = get_db()
                            for candidate in proxy_candidates:
                                try:
                                    self.logger.debug(f"Attempting connection via proxy candidate {candidate.get('addr')}:{candidate.get('port')}")
                                    self.client = TelegramClient(
                                        session=session,
                                        api_id=api_id,
                                        api_hash=api_hash,
                                        proxy=candidate
                                    )
                                    if not self.client:
                                        raise ValueError("TelegramClient is not initialized.")

                                    await self.client.connect()
                                    # success for this candidate
                                    selected_candidate = candidate
                                    # Clear proxy error on success
                                    await db.clear_proxy_error(proxy_data.get('proxy_name'))
                                    break
                                except (OSError, TimeoutError, ConnectionError) as proxy_error:
                                    # Candidate failed - record the error and try next candidate
                                    error_msg = f"Proxy candidate {candidate.get('addr')}:{candidate.get('port')} failed: {type(proxy_error).__name__}: {str(proxy_error)}"
                                    self.logger.warning(error_msg)
                                    await db.set_proxy_error(proxy_data.get('proxy_name'), error_msg)
                                    # try next candidate
                                    continue
                            # If none succeeded, raise a ConnectionError to be handled below
                            if selected_candidate is None:
                                proxy_failed = True
                                if proxy_mode == 'strict':
                                    raise ConnectionError(f"Strict mode - all proxy candidates failed for {proxy_data.get('proxy_name')}")
                                else:
                                    self.logger.warning("Soft proxy mode: all proxy candidates failed, will retry without proxy")
                                    # Increment attempt and continue to retry without proxy
                                    await ctx.failed(ConnectionError("All proxy candidates failed"), delay=False)
                                    continue
                        else:
                            # No proxy - normal client creation
                            self.client = TelegramClient(
                                session=session,
                                api_id=api_id,
                                api_hash=api_hash
                            )
                            if not self.client:
                                raise ValueError("TelegramClient is not initialized.")

                            self.logger.debug(f"Starting client for {self.phone_number}...")
                            await self.client.connect()
                    
                    # Verify the session is still valid
                    try:
                        await self.client.get_me()
                        self.logger.debug(f"Client for {self.phone_number} started successfully.")
                        # Update status to ACTIVE on successful connection
                        await self.account.update_status(AccountStatus.ACTIVE, success=True)
                    except errors.AuthKeyUnregisteredError as auth_error:
                        # Centralized handling for auth-key invalid / revoked sessions
                        self.logger.warning(f"Session for {self.phone_number} is invalid/expired. Creating new session...")
                        self.session_encrypted = None
                        force_new_session = True
                        session_created = False

                        mapping = map_telethon_exception(auth_error)
                        try:
                            if mapping.get('status'):
                                await self.account.update_status(mapping['status'], error=auth_error)
                        except Exception as _u:
                            self.logger.warning(f"Failed to update account status for {self.phone_number}: {_u}")

                        # Update database to clear invalid session
                        from main_logic.database import get_db
                        db = get_db()
                        await db.update_account(self.phone_number, {
                            'session_encrypted': None
                        })

                        await self.client.disconnect()

                        # Decrement proxy usage if it was incremented
                        await ctx.failed(auth_error, delay=False)  # Retry with new session immediately
                        continue
                    except (errors.AuthKeyInvalidError, errors.UserDeactivatedError) as auth_error:
                        self.logger.error(f"Account {self.phone_number} authentication failed: {auth_error}")
                        mapping = map_telethon_exception(auth_error)
                        try:
                            if mapping.get('status'):
                                await self.account.update_status(mapping['status'], error=auth_error)
                        except Exception as _u:
                            self.logger.warning(f"Failed to update account status for {self.phone_number}: {_u}")
                        raise
                    except errors.UserDeactivatedBanError as ban_error:
                        self.logger.error(f"Account {self.phone_number} has been banned.")
                        mapping = map_telethon_exception(ban_error)
                        try:
                            if mapping.get('status'):
                                await self.account.update_status(mapping['status'], error=ban_error)
                        except Exception as _u:
                            self.logger.warning(f"Failed to update account status for {self.phone_number}: {_u}")
                        raise
                    
                    # Update account_id if needed (delegated to ChannelDataMixin later)
                    if not self.account_id:
                        await self.update_account_id_from_telegram()
                
                    ctx.success()
                    return self
                    
                except (errors.AuthKeyUnregisteredError, errors.AuthKeyInvalidError, errors.UserDeactivatedError, errors.UserDeactivatedBanError):
                    # Don't retry on these errors - status already updated above
                    raise
                except ValueError as e:
                    # Session creation errors - don't retry connection
                    self.logger.error(f"Failed to create session for {self.phone_number}: {e}")
                    await self.account.update_status(AccountStatus.ERROR, error=e)
                    raise
                except Exception as e:
                    await ctx.failed(e)
            
            # All retries exhausted
            self.logger.critical(f"All connection attempts failed for {self.phone_number}")
            await self.account.update_status(AccountStatus.ERROR, error=ctx.last_error)
            ctx.raise_if_exhausted()

    async def disconnect(self):
        """Disconnect the client and release account lock if held."""
        async with RetryContext(
            retries_key='connection_retries',
            delay_key='reconnect_delay',
            logger=self.logger
        ) as ctx:
            while ctx.should_retry():
                try:
                    await self.client.disconnect()
                    self.logger.info(f"Client for {self.phone_number} disconnected.")
                    
                    # Release account lock if we hold one (from LockingMixin)
                    await self._release_lock()
                    
                    ctx.success()
                    return
                except Exception as e:
                    await ctx.failed(e)
            
            # All retries exhausted - ensure cleanup happens anyway
            await self._release_lock()
            
            ctx.raise_if_exhausted()

    async def ensure_connected(self):
        """Ensure client is connected, reconnect if needed."""
        if not self.client or not self.is_connected:
            self.logger.info(f"Client for {self.phone_number} is not connected. Reconnecting...")
            await self.connect()
