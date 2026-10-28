"""Tests for the authentication API endpoints."""

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import create_app
from app.core.rate_limiter import rate_limiter


@pytest.fixture
async def client() -> AsyncClient:
    """Create an async test client."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def clear_rate_limiter() -> None:
    """Clear rate limiter before each test."""
    # Clear the default test client IP (127.0.0.1 for ASGITransport)
    rate_limiter.clear_attempts("127.0.0.1")
    yield
    rate_limiter.clear_attempts("127.0.0.1")


class TestAuthLogin:
    """Tests for POST /auth/login endpoint."""

    @pytest.mark.asyncio
    async def test_login_correct_password(self, client: AsyncClient) -> None:
        """Test login with correct password returns access token."""
        response = await client.post(
            "/auth/login",
            json={"password": "123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] == 30 * 24 * 60 * 60  # 30 days in seconds

    @pytest.mark.asyncio
    async def test_login_incorrect_password(self, client: AsyncClient) -> None:
        """Test login with incorrect password returns 401."""
        response = await client.post(
            "/auth/login",
            json={"password": "wrong"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid password"

    @pytest.mark.asyncio
    async def test_login_empty_password(self, client: AsyncClient) -> None:
        """Test login with empty password returns 422."""
        response = await client.post(
            "/auth/login",
            json={"password": ""},
        )

        assert response.status_code == 422


class TestAuthRateLimiting:
    """Tests for brute-force protection (rate limiting)."""

    @pytest.mark.asyncio
    async def test_rate_limiting_after_5_failed_attempts(self, client: AsyncClient) -> None:
        """Test that 5 failed attempts from same IP triggers rate limit (429)."""
        # Clear any existing attempts
        rate_limiter.clear_attempts("127.0.0.1")

        # Make 4 failed attempts (should all return 401)
        for i in range(4):
            response = await client.post(
                "/auth/login",
                json={"password": f"wrong{i}"},
            )
            assert response.status_code == 401

        # 5th attempt should be rate limited (429)
        response = await client.post(
            "/auth/login",
            json={"password": "wrong5"},
        )

        assert response.status_code == 429
        assert "Too many failed attempts" in response.json()["detail"]
        assert "Retry-After" in response.headers

    @pytest.mark.asyncio
    async def test_rate_limit_resets_after_successful_login(self, client: AsyncClient) -> None:
        """Test that successful login clears rate limit for that IP."""
        rate_limiter.clear_attempts("127.0.0.1")

        # Make 3 failed attempts
        for i in range(3):
            response = await client.post(
                "/auth/login",
                json={"password": f"wrong{i}"},
            )
            assert response.status_code == 401

        # Successful login
        response = await client.post(
            "/auth/login",
            json={"password": "123"},
        )
        assert response.status_code == 200

        # Should be able to login again immediately (rate limit cleared)
        response = await client.post(
            "/auth/login",
            json={"password": "123"},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_rate_limit_includes_remaining_header(self, client: AsyncClient) -> None:
        """Test that failed attempts include X-RateLimit-Remaining header."""
        rate_limiter.clear_attempts("127.0.0.1")

        response = await client.post(
            "/auth/login",
            json={"password": "wrong1"},
        )

        assert response.status_code == 401
        assert "X-RateLimit-Remaining" in response.headers
        remaining = int(response.headers["X-RateLimit-Remaining"])
        assert remaining == 4  # 5 max - 1 attempt


class TestProtectedEndpoints:
    """Tests for JWT-protected endpoints."""

    @pytest.mark.asyncio
    async def test_access_with_valid_token(self, client: AsyncClient) -> None:
        """Test accessing protected endpoint with valid token."""
        # Login first
        login_response = await client.post(
            "/auth/login",
            json={"password": "123"},
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]

        # Use token to access protected endpoint
        response = await client.get(
            "/tasks",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_access_without_token(self, client: AsyncClient) -> None:
        """Test accessing protected endpoint without token returns 401."""
        response = await client.get("/tasks")
        assert response.status_code == 401
        assert response.json()["detail"] == "Missing authentication token"

    @pytest.mark.asyncio
    async def test_access_with_invalid_token(self, client: AsyncClient) -> None:
        """Test accessing protected endpoint with invalid token returns 401."""
        response = await client.get(
            "/tasks",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid or expired authentication token"

    @pytest.mark.asyncio
    async def test_access_with_expired_token(self, client: AsyncClient) -> None:
        """Test accessing protected endpoint with expired token returns 401."""
        import jwt
        from datetime import UTC, datetime, timedelta
        from app.core.config import settings

        # Create expired token
        expire = datetime.now(UTC) + timedelta(seconds=-10)
        payload = {"sub": "user", "exp": expire, "iat": datetime.now(UTC)}
        expired_token = jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")

        response = await client.get(
            "/tasks",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid or expired authentication token"