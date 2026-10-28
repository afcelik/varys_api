"""Authentication API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.rate_limiter import rate_limiter
from app.core.security import (
    create_access_token,
    get_token_expiration_seconds,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    """Login request body."""

    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    """Login response body."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


def get_client_ip(request: Request) -> str:
    """Extract client IP from request.

    Args:
        request: FastAPI request object.

    Returns:
        Client IP address.
    """
    # Check for forwarded headers (when behind proxy)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip

    # Fallback to client host
    if request.client:
        return request.client.host

    return "unknown"


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Login with password",
    description="Authenticate with password and receive a JWT access token.",
)
async def login(
    request: Request,
    login_data: LoginRequest,
) -> LoginResponse:
    """Authenticate user and return JWT access token.

    Args:
        request: FastAPI request (for IP extraction).
        login_data: Login credentials.

    Returns:
        JWT access token with expiration info.

    Raises:
        HTTPException: 401 for invalid credentials, 429 for rate limiting.
    """
    client_ip = get_client_ip(request)

    # Check rate limiting
    is_blocked, seconds_remaining = rate_limiter.is_blocked(client_ip)
    if is_blocked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts. Try again in {seconds_remaining} seconds.",
            headers={"Retry-After": str(seconds_remaining)},
        )

    # Verify password
    if not verify_password(login_data.password, settings.PASSWORD_HASH):
        # Record failed attempt
        rate_limiter.record_failed_attempt(client_ip)

        # Check if now blocked
        is_blocked, seconds_remaining = rate_limiter.is_blocked(client_ip)
        if is_blocked:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many failed attempts. Try again in {seconds_remaining} seconds.",
                headers={"Retry-After": str(seconds_remaining)},
            )

        remaining = rate_limiter.get_remaining_attempts(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password",
            headers={"X-RateLimit-Remaining": str(remaining)},
        )

    # Successful login - clear rate limiter for this IP
    rate_limiter.clear_attempts(client_ip)

    # Create access token
    access_token = create_access_token({"sub": "user"})
    expires_in = get_token_expiration_seconds()

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in,
    )