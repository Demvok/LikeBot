"""
Telegram account login process management.
Handles interactive login flow with 2FA support, session encryption,
and database persistence integration.
"""

import os
import uuid
import asyncio
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from dotenv import load_dotenv

from utils.logger import setup_logger
from main_logic.schemas import AccountStatus, LoginStatus, LoginProcess
from auxilary_logic.encryption import (
    decrypt_secret,
    encrypt_secret,
    PURPOSE_PASSWORD,
    PURPOSE_STRING_SESSION,
)

load_dotenv()
api_id = os.getenv('api_id')
api_hash = os.getenv('api_hash')

# Global storage for active login processes
# Format: {login_session_id: LoginProcess}
pending_logins: dict[str, LoginProcess] = {}


async def start_login(
    phone_number: str, 
    password: str = None, 
    login_session_id: str = None,
    session_name: str = None,
    notes: str = None
) -> LoginProcess:
    """
    Start the login process for a Telegram account.
    
    This function:
    1. Creates a TelegramClient and connects
    2. Sends verification code to the phone
    3. Waits for user to provide the code via Future
    4. Signs in with the code
    5. If 2FA required, waits for password via Future
    6. Saves encrypted session to database
    
    Args:
        phone_number: Phone number with country code
        password: Encrypted password for 2FA (optional)
        login_session_id: UUID for this login session (auto-generated if not provided)
        session_name: Custom session name (optional, defaults to "session_{phone_number}")
        notes: Account notes (optional)
    
    Returns:
        LoginProcess object with final status
    """
    from main_logic.database import get_db
    
    # Generate login session ID if not provided
    if not login_session_id:
        login_session_id = str(uuid.uuid4())
    
    logger = setup_logger("login", "main.log")
    logger.info(f"Starting login process for {phone_number} with session ID {login_session_id}")
    
    # Create LoginProcess object
    login_process = LoginProcess(
        login_session_id=login_session_id,
        phone_number=phone_number,
        status=LoginStatus.PROCESSING,
        code_future=asyncio.Future(),
        password_future=asyncio.Future() if password else None,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)
    )
    
    # Store in global pending_logins
    pending_logins[login_session_id] = login_process
    
    try:
        # Create TelegramClient
        client = TelegramClient(StringSession(), api_id, api_hash)
        login_process.telethon_client = client
        
        await client.connect()
        logger.info(f"Client connected for {phone_number}")
        
        # Send verification code
        await client.send_code_request(phone_number)
        login_process.status = LoginStatus.WAIT_CODE
        logger.info(f"Verification code sent to {phone_number}")
        
        # Wait for verification code from user (via API endpoint)
        code = await login_process.code_future
        logger.info(f"Received verification code for {phone_number}")
        
        login_process.status = LoginStatus.PROCESSING
        
        try:
            # Try to sign in with the code
            me = await client.sign_in(phone_number, code)
            logger.info(f"Successfully signed in: {me.first_name} {me.last_name}")
            
            # Success - save session
            session_string = client.session.save()
            encrypted_session = encrypt_secret(session_string, PURPOSE_STRING_SESSION)
            
            login_process.status = LoginStatus.DONE
            login_process.session_string = encrypted_session
            
            # Update or create account in database
            db = get_db()
            account_data = {
                'phone_number': phone_number,
                'account_id': me.id,
                'session_name': session_name if session_name else f"session_{phone_number}",
                'session_encrypted': encrypted_session,
                'twofa': False,
                'notes': notes if notes else "",
                'status': AccountStatus.ACTIVE.name,
                'created_at': datetime.now(timezone.utc),
                'updated_at': datetime.now(timezone.utc)
            }
            
            existing_account = await db.get_account(phone_number)
            if existing_account:
                await db.update_account(phone_number, account_data)
                logger.info(f"Updated account {phone_number} in database")
            else:
                await db.add_account(account_data)
                logger.info(f"Created new account {phone_number} in database")
            
        except errors.SessionPasswordNeededError:
            # 2FA required
            logger.info(f"2FA required for {phone_number}")
            login_process.status = LoginStatus.WAIT_2FA
            
            # Wait for password from user
            if password:
                # Password already provided - decrypt and use it
                decrypted_password = decrypt_secret(password, PURPOSE_PASSWORD)
                password_to_use = decrypted_password
            else:
                # Wait for password via Future
                password_to_use = await login_process.password_future
                
            logger.info(f"Received 2FA password for {phone_number}")
            login_process.status = LoginStatus.PROCESSING
            
            # Sign in with password
            me = await client.sign_in(password=password_to_use)
            logger.info(f"Successfully signed in with 2FA: {me.first_name} {me.last_name}")
            
            # Success - save session
            session_string = client.session.save()
            encrypted_session = encrypt_secret(session_string, PURPOSE_STRING_SESSION)
            
            login_process.status = LoginStatus.DONE
            login_process.session_string = encrypted_session
            
            # Update or create account in database
            db = get_db()
            encrypted_password = encrypt_secret(password_to_use, PURPOSE_PASSWORD) if not password else password
            account_data = {
                'phone_number': phone_number,
                'account_id': me.id,
                'session_name': session_name if session_name else f"session_{phone_number}",
                'session_encrypted': encrypted_session,
                'twofa': True,
                'password_encrypted': encrypted_password,
                'notes': notes if notes else "",
                'status': AccountStatus.ACTIVE.name,
                'created_at': datetime.now(timezone.utc),
                'updated_at': datetime.now(timezone.utc)
            }
            
            existing_account = await db.get_account(phone_number)
            if existing_account:
                await db.update_account(phone_number, account_data)
                logger.info(f"Updated account {phone_number} in database with 2FA")
            else:
                await db.add_account(account_data)
                logger.info(f"Created new account {phone_number} in database with 2FA")
        
        await client.disconnect()
        logger.info(f"Login process completed successfully for {phone_number}")
        
    except Exception as e:
        logger.error(f"Login failed for {phone_number}: {str(e)}")
        login_process.status = LoginStatus.FAILED
        login_process.error_message = str(e)
        
        # Disconnect client if connected
        if login_process.telethon_client and login_process.telethon_client.is_connected():
            await login_process.telethon_client.disconnect()
    
    return login_process


def cleanup_expired_logins():
    """Remove expired login processes from pending_logins."""
    now = datetime.now(timezone.utc)
    expired = [
        login_id for login_id, process in pending_logins.items()
        if process.expires_at and process.expires_at < now
    ]
    for login_id in expired:
        del pending_logins[login_id]
