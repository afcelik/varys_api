"""Tests for the daily plans API endpoints."""

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import jwt
from datetime import UTC, datetime, timedelta, date

from app.main import create_app
from app.core.config import settings
from app.core.database import get_db
from app.models.base import Base
from app.models.task import Task, TaskStatus
from app.models.daily_plan import DailyPlan
from app.repositories.daily_plan import DailyPlanRepository
from app.repositories.task import TaskRepository
from app.services.daily_plan import DailyPlanService

# Create an in-memory SQLite database for testing
TEST_DATABASE_URL = "sqlite:///:memory:"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

# Configure SQLite for testing
def _configure_test_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()

event.listen(test_engine, "connect", _configure_test_connection)

TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db() -> Session:
    """Override database dependency for testing."""
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


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


@pytest.fixture(scope="function")
def db_session() -> Session:
    """Create a fresh database session for each test."""
    Base.metadata.create_all(bind=test_engine)
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
async def client(db_session: Session) -> AsyncClient:
    """Create an async test client with overridden database and auth headers."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    transport = ASGITransport(app=app)
    token = create_test_token()
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
async def client_no_auth(db_session: Session) -> AsyncClient:
    """Create an async test client without authentication headers."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


# Repository Tests
class TestDailyPlanRepository:
    """Tests for DailyPlanRepository."""

    def test_get_by_date_not_found(self, db_session: Session) -> None:
        """Test getting a non-existent daily plan returns None."""
        repo = DailyPlanRepository(db_session)
        plan = repo.get_by_date(date(2026, 1, 15))
        assert plan is None

    def test_upsert_create_new(self, db_session: Session) -> None:
        """Test creating a new daily plan."""
        from app.schemas.task import TaskCreate
        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1"))
        task2 = task_repo.create(TaskCreate(title="Task 2"))

        repo = DailyPlanRepository(db_session)
        plan = repo.upsert(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=task1.id,
            side_focus_task_id=task2.id,
            say_no_text="Say no to distractions",
        )

        assert plan.id is not None
        assert plan.plan_date == date(2026, 1, 15)
        assert plan.main_focus_task_id == task1.id
        assert plan.side_focus_task_id == task2.id
        assert plan.say_no_text == "Say no to distractions"

    def test_upsert_update_existing(self, db_session: Session) -> None:
        """Test updating an existing daily plan."""
        from app.schemas.task import TaskCreate
        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1"))
        task2 = task_repo.create(TaskCreate(title="Task 2"))
        task3 = task_repo.create(TaskCreate(title="Task 3"))
        task4 = task_repo.create(TaskCreate(title="Task 4"))

        repo = DailyPlanRepository(db_session)
        repo.upsert(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=task1.id,
            side_focus_task_id=task2.id,
            say_no_text="Original text",
        )

        plan = repo.upsert(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=task3.id,
            side_focus_task_id=task4.id,
            say_no_text="Updated text",
        )

        assert plan.id is not None
        assert plan.main_focus_task_id == task3.id
        assert plan.side_focus_task_id == task4.id
        assert plan.say_no_text == "Updated text"

    def test_upsert_clear_fields(self, db_session: Session) -> None:
        """Test clearing fields by sending None."""
        from app.schemas.task import TaskCreate
        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1"))
        task2 = task_repo.create(TaskCreate(title="Task 2"))

        repo = DailyPlanRepository(db_session)
        repo.upsert(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=task1.id,
            side_focus_task_id=task2.id,
            say_no_text="Original text",
        )

        plan = repo.upsert(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=None,
            side_focus_task_id=None,
            say_no_text=None,
        )

        assert plan.main_focus_task_id is None
        assert plan.side_focus_task_id is None
        assert plan.say_no_text is None

    def test_get_tasks_by_planned_date(self, db_session: Session) -> None:
        """Test getting tasks by planned date."""
        from app.schemas.task import TaskCreate
        task_repo = TaskRepository(db_session)
        task_repo.create(TaskCreate(title="Task 1", planned_date=datetime(2026, 1, 15, 10, 0, 0)))
        task_repo.create(TaskCreate(title="Task 2", planned_date=datetime(2026, 1, 15, 14, 0, 0)))
        task_repo.create(TaskCreate(title="Task 3", planned_date=datetime(2026, 1, 16, 10, 0, 0)))

        plan_repo = DailyPlanRepository(db_session)
        tasks = plan_repo.get_tasks_by_planned_date(date(2026, 1, 15))

        assert len(tasks) == 2
        assert all(t.planned_date is not None for t in tasks)
        assert all(t.planned_date.date() == date(2026, 1, 15) for t in tasks)

    def test_get_tasks_by_planned_date_excludes_deleted(self, db_session: Session) -> None:
        """Test that soft-deleted tasks are excluded from tasks by planned date."""
        from app.schemas.task import TaskCreate
        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1", planned_date=datetime(2026, 1, 15, 10, 0, 0)))
        task2 = task_repo.create(TaskCreate(title="Task 2", planned_date=datetime(2026, 1, 15, 14, 0, 0)))

        # Soft delete task1
        task_repo.soft_delete(task1)

        plan_repo = DailyPlanRepository(db_session)
        tasks = plan_repo.get_tasks_by_planned_date(date(2026, 1, 15))

        assert len(tasks) == 1
        assert tasks[0].title == "Task 2"


# Service Tests
class TestDailyPlanService:
    """Tests for DailyPlanService."""

    def test_get_plan_empty(self, db_session: Session) -> None:
        """Test getting plan for date with no record returns empty plan."""
        daily_plan_repo = DailyPlanRepository(db_session)
        task_repo = TaskRepository(db_session)
        service = DailyPlanService(daily_plan_repo, task_repo)

        response = service.get_plan(date(2026, 1, 15))

        assert response.id is None
        assert response.plan_date == date(2026, 1, 15)
        assert response.main_focus_task_id is None
        assert response.side_focus_task_id is None
        assert response.say_no_text is None
        assert response.created_at is None
        assert response.updated_at is None
        assert response.main_focus is None
        assert response.side_focus is None
        assert response.tasks == []

    def test_get_plan_with_tasks(self, db_session: Session) -> None:
        """Test getting plan with tasks for that date."""
        daily_plan_repo = DailyPlanRepository(db_session)
        task_repo = TaskRepository(db_session)
        service = DailyPlanService(daily_plan_repo, task_repo)

        # Create tasks with planned_date
        task1 = task_repo.create(TaskCreate(title="Task 1", planned_date=datetime(2026, 1, 15, 10, 0, 0)))
        task_repo.create(TaskCreate(title="Task 2", planned_date=datetime(2026, 1, 15, 14, 0, 0)))
        task_repo.create(TaskCreate(title="Task 3", planned_date=datetime(2026, 1, 16, 10, 0, 0)))

        # Create plan with focus tasks
        daily_plan_repo.upsert(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=task1.id,
            side_focus_task_id=None,
            say_no_text="Focus on what matters",
        )

        response = service.get_plan(date(2026, 1, 15))

        assert response.id is not None
        assert response.plan_date == date(2026, 1, 15)
        assert response.main_focus_task_id == task1.id
        assert response.main_focus is not None
        assert response.main_focus.id == task1.id
        assert response.main_focus.title == "Task 1"
        assert len(response.tasks) == 2
        assert response.tasks[0].title in ["Task 1", "Task 2"]

    def test_get_plan_deleted_focus_returns_null(self, db_session: Session) -> None:
        """Test that deleted focus task returns null in response."""
        daily_plan_repo = DailyPlanRepository(db_session)
        task_repo = TaskRepository(db_session)
        service = DailyPlanService(daily_plan_repo, task_repo)

        # Create task and plan with it as focus
        task1 = task_repo.create(TaskCreate(title="Task 1"))
        daily_plan_repo.upsert(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=task1.id,
            side_focus_task_id=None,
            say_no_text="Test",
        )

        # Soft delete the task
        task_repo.soft_delete(task1)

        response = service.get_plan(date(2026, 1, 15))

        # Plan record still has the task_id but focus should be null
        assert response.main_focus_task_id == task1.id
        assert response.main_focus is None

    def test_upsert_plan_create(self, db_session: Session) -> None:
        """Test creating a new plan via service."""
        from app.schemas.task import TaskCreate
        daily_plan_repo = DailyPlanRepository(db_session)
        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1"))
        task2 = task_repo.create(TaskCreate(title="Task 2"))
        service = DailyPlanService(daily_plan_repo, task_repo)

        response = service.upsert_plan(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=task1.id,
            side_focus_task_id=task2.id,
            say_no_text="Say no",
            main_focus_explicitly_set=True,
            side_focus_explicitly_set=True,
            say_no_explicitly_set=True,
        )

        assert response.id is not None
        assert response.main_focus_task_id == task1.id
        assert response.side_focus_task_id == task2.id
        assert response.say_no_text == "Say no"

    def test_upsert_plan_update_partial(self, db_session: Session) -> None:
        """Test updating only some fields (preserving others)."""
        from app.schemas.task import TaskCreate
        daily_plan_repo = DailyPlanRepository(db_session)
        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1"))
        task2 = task_repo.create(TaskCreate(title="Task 2"))
        service = DailyPlanService(daily_plan_repo, task_repo)

        # Create initial plan
        service.upsert_plan(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=task1.id,
            side_focus_task_id=task2.id,
            say_no_text="Original",
            main_focus_explicitly_set=True,
            side_focus_explicitly_set=True,
            say_no_explicitly_set=True,
        )

        # Update only say_no_text
        response = service.upsert_plan(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=None,
            side_focus_task_id=None,
            say_no_text="Updated",
            main_focus_explicitly_set=False,
            side_focus_explicitly_set=False,
            say_no_explicitly_set=True,
        )

        assert response.main_focus_task_id == task1.id  # preserved
        assert response.side_focus_task_id == task2.id  # preserved
        assert response.say_no_text == "Updated"  # updated

    def test_upsert_plan_clear_focus(self, db_session: Session) -> None:
        """Test explicitly clearing a focus by sending null."""
        from app.schemas.task import TaskCreate
        daily_plan_repo = DailyPlanRepository(db_session)
        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1"))
        task2 = task_repo.create(TaskCreate(title="Task 2"))
        service = DailyPlanService(daily_plan_repo, task_repo)

        # Create initial plan
        service.upsert_plan(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=task1.id,
            side_focus_task_id=task2.id,
            say_no_text="Original",
            main_focus_explicitly_set=True,
            side_focus_explicitly_set=True,
            say_no_explicitly_set=True,
        )

        # Clear main focus by explicitly sending null
        response = service.upsert_plan(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=None,
            side_focus_task_id=None,
            say_no_text=None,
            main_focus_explicitly_set=True,
            side_focus_explicitly_set=False,
            say_no_explicitly_set=False,
        )

        assert response.main_focus_task_id is None  # cleared
        assert response.side_focus_task_id == task2.id  # preserved
        assert response.say_no_text == "Original"  # preserved

    def test_same_focus_raises_error(self, db_session: Session) -> None:
        """Test that setting same task as both focuses raises error."""
        from app.schemas.task import TaskCreate
        daily_plan_repo = DailyPlanRepository(db_session)
        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1"))
        service = DailyPlanService(daily_plan_repo, task_repo)

        with pytest.raises(Exception) as exc_info:
            service.upsert_plan(
                plan_date=date(2026, 1, 15),
                main_focus_task_id=task1.id,
                side_focus_task_id=task1.id,
                say_no_text=None,
                main_focus_explicitly_set=True,
                side_focus_explicitly_set=True,
                say_no_explicitly_set=False,
            )

        assert "same" in str(exc_info.value).lower()

    def test_same_focus_with_existing_raises_error(self, db_session: Session) -> None:
        """Test that setting side focus to current main focus raises error."""
        from app.schemas.task import TaskCreate
        daily_plan_repo = DailyPlanRepository(db_session)
        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1"))
        task2 = task_repo.create(TaskCreate(title="Task 2"))
        service = DailyPlanService(daily_plan_repo, task_repo)

        # Create plan with main_focus = task1
        service.upsert_plan(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=task1.id,
            side_focus_task_id=task2.id,
            say_no_text=None,
            main_focus_explicitly_set=True,
            side_focus_explicitly_set=True,
            say_no_explicitly_set=False,
        )

        # Try to set side_focus to task1 (same as current main_focus)
        with pytest.raises(Exception) as exc_info:
            service.upsert_plan(
                plan_date=date(2026, 1, 15),
                main_focus_task_id=None,
                side_focus_task_id=task1.id,
                say_no_text=None,
                main_focus_explicitly_set=False,
                side_focus_explicitly_set=True,
                say_no_explicitly_set=False,
            )

        assert "same" in str(exc_info.value).lower()

    def test_nonexistent_task_raises_error(self, db_session: Session) -> None:
        """Test that non-existent task as focus raises error."""
        daily_plan_repo = DailyPlanRepository(db_session)
        task_repo = TaskRepository(db_session)
        service = DailyPlanService(daily_plan_repo, task_repo)

        with pytest.raises(Exception) as exc_info:
            service.upsert_plan(
                plan_date=date(2026, 1, 15),
                main_focus_task_id=999,
                side_focus_task_id=None,
                say_no_text=None,
                main_focus_explicitly_set=True,
                side_focus_explicitly_set=False,
                say_no_explicitly_set=False,
            )

        assert "not found" in str(exc_info.value).lower()

    def test_deleted_task_raises_error(self, db_session: Session) -> None:
        """Test that deleted task as focus raises error."""
        from app.schemas.task import TaskCreate
        daily_plan_repo = DailyPlanRepository(db_session)
        task_repo = TaskRepository(db_session)
        service = DailyPlanService(daily_plan_repo, task_repo)

        task1 = task_repo.create(TaskCreate(title="Task 1"))
        task_repo.soft_delete(task1)

        with pytest.raises(Exception) as exc_info:
            service.upsert_plan(
                plan_date=date(2026, 1, 15),
                main_focus_task_id=task1.id,
                side_focus_task_id=None,
                say_no_text=None,
                main_focus_explicitly_set=True,
                side_focus_explicitly_set=False,
                say_no_explicitly_set=False,
            )

        assert "not found" in str(exc_info.value).lower()


# API Endpoint Tests
class TestDailyPlansAPI:
    """Tests for daily plans API endpoints."""

    @pytest.mark.asyncio
    async def test_get_daily_plan_empty(self, client: AsyncClient) -> None:
        """Test GET /daily-plans/{plan_date} returns empty plan when no record exists."""
        response = await client.get("/daily-plans/2026-01-15")

        assert response.status_code == 200
        data = response.json()
        assert data["plan_date"] == "2026-01-15"
        assert data["id"] is None
        assert data["main_focus_task_id"] is None
        assert data["side_focus_task_id"] is None
        assert data["say_no_text"] is None
        assert data["main_focus"] is None
        assert data["side_focus"] is None
        assert data["tasks"] == []

    @pytest.mark.asyncio
    async def test_get_daily_plan_does_not_create_record(self, client: AsyncClient, db_session: Session) -> None:
        """Test GET /daily-plans/{plan_date} does not write to database."""
        from app.repositories.daily_plan import DailyPlanRepository

        repo = DailyPlanRepository(db_session)
        assert repo.get_by_date(date(2026, 1, 15)) is None

        response = await client.get("/daily-plans/2026-01-15")
        assert response.status_code == 200

        # Verify no record was created
        assert repo.get_by_date(date(2026, 1, 15)) is None

    @pytest.mark.asyncio
    async def test_get_daily_plan_with_tasks(self, client: AsyncClient, db_session: Session) -> None:
        """Test GET /daily-plans/{plan_date} returns tasks with matching planned_date."""
        from app.repositories.task import TaskRepository
        from app.schemas.task import TaskCreate

        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1", planned_date=datetime(2026, 1, 15, 10, 0, 0)))
        task_repo.create(TaskCreate(title="Task 2", planned_date=datetime(2026, 1, 15, 14, 0, 0)))
        task_repo.create(TaskCreate(title="Task 3", planned_date=datetime(2026, 1, 16, 10, 0, 0)))

        daily_plan_repo = DailyPlanRepository(db_session)
        daily_plan_repo.upsert(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=task1.id,
            side_focus_task_id=None,
            say_no_text="Focus",
        )

        response = await client.get("/daily-plans/2026-01-15")

        assert response.status_code == 200
        data = response.json()
        assert data["plan_date"] == "2026-01-15"
        assert data["main_focus_task_id"] == task1.id
        assert data["main_focus"] is not None
        assert data["main_focus"]["id"] == task1.id
        assert data["main_focus"]["title"] == "Task 1"
        assert len(data["tasks"]) == 2
        task_titles = [t["title"] for t in data["tasks"]]
        assert "Task 1" in task_titles
        assert "Task 2" in task_titles
        assert "Task 3" not in task_titles

    @pytest.mark.asyncio
    async def test_get_daily_plan_excludes_deleted_tasks(self, client: AsyncClient, db_session: Session) -> None:
        """Test GET /daily-plans/{plan_date} excludes deleted tasks from tasks list."""
        from app.repositories.task import TaskRepository
        from app.schemas.task import TaskCreate

        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1", planned_date=datetime(2026, 1, 15, 10, 0, 0)))
        task2 = task_repo.create(TaskCreate(title="Task 2", planned_date=datetime(2026, 1, 15, 14, 0, 0)))

        # Soft delete task1
        task_repo.soft_delete(task1)

        response = await client.get("/daily-plans/2026-01-15")

        assert response.status_code == 200
        data = response.json()
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["title"] == "Task 2"

    @pytest.mark.asyncio
    async def test_get_daily_plan_deleted_focus_null(self, client: AsyncClient, db_session: Session) -> None:
        """Test GET returns null for deleted focus task."""
        from app.repositories.task import TaskRepository
        from app.repositories.daily_plan import DailyPlanRepository
        from app.schemas.task import TaskCreate

        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1"))

        daily_plan_repo = DailyPlanRepository(db_session)
        daily_plan_repo.upsert(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=task1.id,
            side_focus_task_id=None,
            say_no_text="Test",
        )

        # Soft delete the task
        task_repo.soft_delete(task1)

        response = await client.get("/daily-plans/2026-01-15")

        assert response.status_code == 200
        data = response.json()
        assert data["main_focus_task_id"] == task1.id
        assert data["main_focus"] is None

    @pytest.mark.asyncio
    async def test_upsert_daily_plan_create(self, client: AsyncClient, db_session: Session) -> None:
        """Test PUT /daily-plans/{plan_date} creates new plan."""
        from app.repositories.task import TaskRepository
        from app.schemas.task import TaskCreate

        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1"))
        task2 = task_repo.create(TaskCreate(title="Task 2"))

        response = await client.put(
            "/daily-plans/2026-01-15",
            json={
                "main_focus_task_id": task1.id,
                "side_focus_task_id": task2.id,
                "say_no_text": "Say no to distractions",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["plan_date"] == "2026-01-15"
        assert data["main_focus_task_id"] == task1.id
        assert data["side_focus_task_id"] == task2.id
        assert data["say_no_text"] == "Say no to distractions"

    @pytest.mark.asyncio
    async def test_upsert_daily_plan_update_partial(self, client: AsyncClient, db_session: Session) -> None:
        """Test PUT updates only provided fields."""
        from app.repositories.daily_plan import DailyPlanRepository
        from app.repositories.task import TaskRepository
        from app.schemas.task import TaskCreate

        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1"))
        task2 = task_repo.create(TaskCreate(title="Task 2"))

        daily_plan_repo = DailyPlanRepository(db_session)
        daily_plan_repo.upsert(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=task1.id,
            side_focus_task_id=task2.id,
            say_no_text="Original",
        )

        # Update only say_no_text
        response = await client.put(
            "/daily-plans/2026-01-15",
            json={"say_no_text": "Updated"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["main_focus_task_id"] == task1.id  # preserved
        assert data["side_focus_task_id"] == task2.id  # preserved
        assert data["say_no_text"] == "Updated"  # updated

    @pytest.mark.asyncio
    async def test_upsert_daily_plan_clear_main_focus(self, client: AsyncClient, db_session: Session) -> None:
        """Test PUT with null clears main_focus_task_id."""
        from app.repositories.daily_plan import DailyPlanRepository
        from app.repositories.task import TaskRepository
        from app.schemas.task import TaskCreate

        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1"))
        task2 = task_repo.create(TaskCreate(title="Task 2"))

        daily_plan_repo = DailyPlanRepository(db_session)
        daily_plan_repo.upsert(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=task1.id,
            side_focus_task_id=task2.id,
            say_no_text="Original",
        )

        # Clear main_focus explicitly
        response = await client.put(
            "/daily-plans/2026-01-15",
            json={"main_focus_task_id": None},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["main_focus_task_id"] is None
        assert data["side_focus_task_id"] == task2.id  # preserved
        assert data["say_no_text"] == "Original"  # preserved

    @pytest.mark.asyncio
    async def test_upsert_same_focus_returns_422(self, client: AsyncClient, db_session: Session) -> None:
        """Test PUT with same task as both focuses returns 422."""
        from app.repositories.task import TaskRepository
        from app.schemas.task import TaskCreate

        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1"))

        response = await client.put(
            "/daily-plans/2026-01-15",
            json={
                "main_focus_task_id": task1.id,
                "side_focus_task_id": task1.id,
            },
        )

        assert response.status_code == 422
        assert "same" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_upsert_same_as_existing_returns_422(self, client: AsyncClient, db_session: Session) -> None:
        """Test PUT setting side_focus to existing main_focus returns 422."""
        from app.repositories.daily_plan import DailyPlanRepository
        from app.repositories.task import TaskRepository
        from app.schemas.task import TaskCreate

        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1"))
        task2 = task_repo.create(TaskCreate(title="Task 2"))

        daily_plan_repo = DailyPlanRepository(db_session)
        daily_plan_repo.upsert(
            plan_date=date(2026, 1, 15),
            main_focus_task_id=task1.id,
            side_focus_task_id=task2.id,
            say_no_text=None,
        )

        # Try to set side_focus to task1 (same as current main_focus)
        response = await client.put(
            "/daily-plans/2026-01-15",
            json={"side_focus_task_id": task1.id},
        )

        assert response.status_code == 422
        assert "same" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_upsert_nonexistent_task_returns_404(self, client: AsyncClient) -> None:
        """Test PUT with non-existent task returns 404."""
        response = await client.put(
            "/daily-plans/2026-01-15",
            json={"main_focus_task_id": 999},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_upsert_deleted_task_returns_404(self, client: AsyncClient, db_session: Session) -> None:
        """Test PUT with deleted task returns 404."""
        from app.repositories.task import TaskRepository
        from app.schemas.task import TaskCreate

        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Task 1"))
        task_repo.soft_delete(task1)

        response = await client.put(
            "/daily-plans/2026-01-15",
            json={"main_focus_task_id": task1.id},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_invalid_date_format_returns_422(self, client: AsyncClient) -> None:
        """Test GET with invalid date format returns 422."""
        response = await client.get("/daily-plans/invalid-date")

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_get_without_token_returns_401(self, client_no_auth: AsyncClient) -> None:
        """Test GET without token returns 401."""
        response = await client_no_auth.get("/daily-plans/2026-01-15")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_put_without_token_returns_401(self, client_no_auth: AsyncClient) -> None:
        """Test PUT without token returns 401."""
        response = await client_no_auth.put("/daily-plans/2026-01-15", json={})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_completed_task_can_be_focus(self, client: AsyncClient, db_session: Session) -> None:
        """Test that a completed task can be assigned as focus."""
        from app.repositories.task import TaskRepository
        from app.schemas.task import TaskCreate
        from app.models.task import TaskStatus

        task_repo = TaskRepository(db_session)
        task1 = task_repo.create(TaskCreate(title="Done Task", status=TaskStatus.DONE))

        response = await client.put(
            "/daily-plans/2026-01-15",
            json={"main_focus_task_id": task1.id},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["main_focus_task_id"] == task1.id
        assert data["main_focus"] is not None
        assert data["main_focus"]["status"] == "done"


# Need to import TaskCreate for tests
from app.schemas.task import TaskCreate