"""
Session management mixin for Telegram client.

Handles Telethon session string decryption and validation.
"""

from telethon.sessions import StringSession
from auxilary_logic.encryption import decrypt_secret, PURPOSE_STRING_SESSION


class SessionMixin:
    """Handles Telethon session creation and validation."""
    
    async def _get_session(self, force_new=False):
        """
        Get the Telethon session for this account.
        
        Args:
            force_new: If True, clears the current session (used when session becomes invalid)
            
        Returns:
            StringSession object
            
        Raises:
            ValueError: If no session exists (user must login via API first)
        """
        if self.session_encrypted and not force_new:
            self.logger.info(f"Using existing session for {self.phone_number}.")
            return StringSession(decrypt_secret(self.session_encrypted, PURPOSE_STRING_SESSION))
        else:
            # No session exists - user must login through the API endpoint
            error_msg = (
                f"No session found for {self.phone_number}. "
                "Please use the /accounts/create API endpoint to login this account first."
            )
            self.logger.error(error_msg)
            raise ValueError(error_msg)
