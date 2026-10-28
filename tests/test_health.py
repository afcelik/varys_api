"""Tests for the health check endpoint."""

import pytest
from httpx import AsyncClient, ASGITransport
import jwt
from datetime import UTC, datetime, timedelta

from app.main import create_app
from app.core.config import settings


@pytest.fixture
async def client() -> AsyncClient:
    """Create an async test client."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def create_test_token(expired: bool = False) -> str:
    """Create a test JWT token.

    Args:
        expired: If True, create an expired token.

    Returns:
        JWT token string.
    """
    expire = datetime.now(UTC) + (timedelta(seconds=-10) if expired else timedelta(days=1))
    payload = {"sub": "user", "exp": expire, "iat": datetime.now(UTC)}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Authentication headers for protected endpoints."""
    token = create_test_token()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def expired_auth_headers() -> dict[str, str]:
    """Authentication headers with expired token."""
    token = create_test_token(expired=True)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient) -> None:
    """Test the health check endpoint returns 200 and correct response (no auth required)."""
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_check_no_auth(client: AsyncClient) -> None:
    """Test the health check endpoint works without authentication."""
    response = await client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_protected_endpoint_requires_auth(client: AsyncClient) -> None:
    """Test that protected endpoints return 401 without token."""
    response = await client.get("/tasks")
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing authentication token"


@pytest.mark.asyncio
async def test_protected_endpoint_invalid_token(client: AsyncClient) -> None:
    """Test that protected endpoints return 401 with invalid token."""
    response = await client.get("/tasks", headers={"Authorization": "Bearer invalid-token"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired authentication token"


@pytest.mark.asyncio
async def test_protected_endpoint_expired_token(client: AsyncClient, expired_auth_headers: dict[str, str]) -> None:
    """Test that protected endpoints return 401 with expired token."""
    response = await client.get("/tasks", headers=expired_auth_headers)
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired authentication token"


@pytest.mark.asyncio
async def test_protected_endpoint_valid_token(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    """Test that protected endpoints work with valid token."""
    response = await client.get("/tasks", headers=auth_headers)
    assert response.status_code == 200