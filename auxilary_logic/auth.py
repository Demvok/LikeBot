"""
Authentication and Authorization module for LikeBot API.

This module provides JWT-based authentication and authorization utilities
for protecting API endpoints and managing user sessions.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Annotated
import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError

from main_logic.database import get_db
from auxilary_logic.encryption import (
    hash_password, verify_password, create_access_token, 
    decode_access_token, JWT_ACCESS_TOKEN_EXPIRE_MINUTES
)
from main_logic.schemas import Token, TokenData, UserCreate, UserLogin, UserRole

# OAuth2 scheme for token authentication
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

logger = logging.getLogger("likebot.auth")


async def authenticate_user(username: str, password: str) -> dict | None:
    """
    Authenticate a user by username and password.
    
    Args:
        username: Username to authenticate
        password: Plain text password
        
    Returns:
        User dictionary if authentication successful, None otherwise
    """
    if len(password.encode('utf-8')) > 72:
        logger.warning("Password exceeds bcrypt limit for username '%s'", username)
        return None

    db = get_db()
    success, user_data = await db.verify_user_credentials(username, password)
    
    if not success or not user_data:
        return None
    
    return user_data


async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> dict:
    """
    Dependency to get the current authenticated user from JWT token.
    
    Args:
        token: JWT token from Authorization header
        
    Returns:
        User data dictionary
        
    Raises:
        HTTPException: If token is invalid or user not found
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = decode_access_token(token)
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        
        # Create TokenData for validation
        token_data = TokenData(
            sub=username,
            is_verified=payload.get("is_verified", False),
            role=payload.get("role", "user"),
            exp=payload.get("exp")
        )
    except JWTError:
        raise credentials_exception
    
    # Get user from database
    db = get_db()
    user = await db.get_user(username=token_data.sub)
    if user is None:
        raise credentials_exception
    
    return user


async def get_current_verified_user(
    current_user: Annotated[dict, Depends(get_current_user)]
) -> dict:
    """
    Dependency to ensure the current user is verified.
    
    Args:
        current_user: Current user from get_current_user dependency
        
    Returns:
        User data dictionary
        
    Raises:
        HTTPException: If user is not verified
    """
    if not current_user.get("is_verified", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not verified"
        )
    return current_user


async def get_current_admin_user(
    current_user: Annotated[dict, Depends(get_current_user)]
) -> dict:
    """
    Dependency to ensure the current user is an admin.
    
    Args:
        current_user: Current user from get_current_user dependency
        
    Returns:
        User data dictionary
        
    Raises:
        HTTPException: If user is not an admin
    """
    if current_user.get("role") != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required"
        )
    return current_user


async def create_user_account(user_data: UserCreate) -> dict:
    """
    Create a new user account with hashed password.
    
    Args:
        user_data: User creation data
        
    Returns:
        Created user data dictionary
        
    Raises:
        HTTPException: If user already exists or creation fails
    """
    db = get_db()
    
    # Check if user already exists
    existing_user = await db.get_user(user_data.username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    if len(user_data.password.encode('utf-8')) > 72:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password exceeds bcrypt's 72-byte limit. Please choose a shorter password."
        )

    # Hash password
    password_hash = hash_password(user_data.password)
    
    # Prepare user data
    user_dict = {
        "username": user_data.username.lower(),
        "password_hash": password_hash,
        "role": user_data.role.value,
        "is_verified": False,  # New users start unverified
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc)
    }
    
    # Create user
    success = await db.create_user(user_dict)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user"
        )
    
    return user_dict


def create_user_token(user_data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT token for a user.
    
    Args:
        user_data: User data dictionary
        expires_delta: Optional custom expiration time
        
    Returns:
        JWT token string
    """
    token_payload = {
        "sub": user_data["username"],
        "is_verified": user_data.get("is_verified", False),
        "role": user_data.get("role", UserRole.USER.value)
    }
    
    if expires_delta is None:
        expires_delta = timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    
    access_token = create_access_token(
        data=token_payload,
        expires_delta=expires_delta
    )
    
    return access_token
