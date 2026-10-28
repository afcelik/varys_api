"""Tests for the health check endpoint."""

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import create_app


@pytest.fixture
async def client() -> AsyncClient:
    """Create an async test client."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Authentication headers for protected endpoints."""
    # Use the actual API_TOKEN from settings
    from app.core.config import settings
    return {"Authorization": f"Bearer {settings.API_TOKEN}"}


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
    assert response.json()["detail"] == "Invalid authentication token"


@pytest.mark.asyncio
async def test_protected_endpoint_valid_token(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    """Test that protected endpoints work with valid token."""
    response = await client.get("/tasks", headers=auth_headers)
    assert response.status_code == 200