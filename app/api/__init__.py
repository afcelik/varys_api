"""API routes package."""

from app.api.daily_plans import router as daily_plans_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.tasks import router as tasks_router

__all__ = ["auth_router", "health_router", "tasks_router", "daily_plans_router"]