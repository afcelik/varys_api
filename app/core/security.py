"""Security utilities: password hashing and JWT token management."""

import time
from datetime import UTC, datetime, timedelta

import argon2
import jwt
from argon2 import PasswordHasher

from app.core.config import settings


# Argon2 password hasher
ph = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a password using Argon2.

    Args:
        password: Plain text password.

    Returns:
        Argon2 hash string.
    """
    return ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against an Argon2 hash.

    Args:
        password: Plain text password.
        password_hash: Argon2 hash string.

    Returns:
        True if password matches, False otherwise.
    """
    try:
        ph.verify(password_hash, password)
        return True
    except argon2.exceptions.VerifyMismatchError:
        return False
    except Exception:
        return False


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a JWT access token.

    Args:
        data: Data to encode in the token (typically user identity).
        expires_delta: Optional custom expiration timedelta.

    Returns:
        Encoded JWT string.
    """
    to_encode = data.copy()
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(days=settings.JWT_EXPIRE_DAYS)
    )
    to_encode.update({"exp": expire, "iat": datetime.now(UTC)})
    encoded_jwt = jwt.encode(
        to_encode, settings.JWT_SECRET, algorithm="HS256"
    )
    return encoded_jwt


def decode_access_token(token: str) -> dict | None:
    """Decode and validate a JWT access token.

    Args:
        token: JWT token string.

    Returns:
        Decoded payload dict if valid, None otherwise.
    """
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=["HS256"]
        )
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def get_token_expiration_seconds() -> int:
    """Get token expiration in seconds.

    Returns:
        Expiration time in seconds.
    """
    return settings.JWT_EXPIRE_DAYS * 24 * 60 * 60