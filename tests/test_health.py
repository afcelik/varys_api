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


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient) -> None:
    """Test the health check endpoint returns 200 and correct response."""
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}