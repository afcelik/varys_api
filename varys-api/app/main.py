"""Main FastAPI application entry point."""

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.api.health import router as health_router
from app.api.tasks import router as tasks_router
from app.core.config import settings
from app.core.database import get_db, init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Application lifespan manager."""
    # Startup: initialize database
    init_db()
    yield
    # Shutdown logic here (e.g., close connections)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=settings.APP_NAME,
        debug=settings.DEBUG,
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(health_router)
    app.include_router(tasks_router)

    # Dependency for database session
    @app.get("/db-test", include_in_schema=False)
    def test_db(db: Session = Depends(get_db)):
        """Test database connection."""
        return {"status": "connected"}

    return app


app = create_app()