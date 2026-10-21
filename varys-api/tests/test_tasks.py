"""Tests for the tasks API endpoints."""

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import create_app
from app.core.database import get_db
from app.models.base import Base
from app.models.task import Task, TaskStatus
from app.repositories.task import TaskRepository
from app.services.task import TaskService


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
    """Create an async test client with overridden database."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# Repository Tests
class TestTaskRepository:
    """Tests for TaskRepository."""

    def test_create_task(self, db_session: Session) -> None:
        """Test creating a task."""
        from app.schemas.task import TaskCreate

        repo = TaskRepository(db_session)
        task_create = TaskCreate(title="Test Task", description="Test Description", status="todo")
        task = repo.create(task_create)

        assert task.id is not None
        assert task.title == "Test Task"
        assert task.description == "Test Description"
        assert task.status == TaskStatus.TODO

    def test_get_task_by_id(self, db_session: Session) -> None:
        """Test getting a task by ID."""
        from app.schemas.task import TaskCreate

        repo = TaskRepository(db_session)
        task_create = TaskCreate(title="Test Task")
        created_task = repo.create(task_create)

        task = repo.get_by_id(created_task.id)
        assert task is not None
        assert task.id == created_task.id
        assert task.title == "Test Task"

    def test_get_task_not_found(self, db_session: Session) -> None:
        """Test getting a non-existent task returns None."""
        repo = TaskRepository(db_session)
        task = repo.get_by_id(999)
        assert task is None

    def test_get_all_tasks(self, db_session: Session) -> None:
        """Test getting all tasks."""
        from app.schemas.task import TaskCreate

        repo = TaskRepository(db_session)
        repo.create(TaskCreate(title="Task 1", status="todo"))
        repo.create(TaskCreate(title="Task 2", status="in_progress"))
        repo.create(TaskCreate(title="Task 3", status="done"))

        tasks = repo.get_all()
        assert len(tasks) == 3

    def test_get_tasks_filtered_by_status(self, db_session: Session) -> None:
        """Test getting tasks filtered by status."""
        from app.schemas.task import TaskCreate

        repo = TaskRepository(db_session)
        repo.create(TaskCreate(title="Task 1", status="todo"))
        repo.create(TaskCreate(title="Task 2", status="in_progress"))
        repo.create(TaskCreate(title="Task 3", status="done"))

        tasks = repo.get_all(TaskStatus.TODO)
        assert len(tasks) == 1
        assert tasks[0].title == "Task 1"

    def test_update_task(self, db_session: Session) -> None:
        """Test updating a task."""
        from app.schemas.task import TaskCreate, TaskUpdate

        repo = TaskRepository(db_session)
        task_create = TaskCreate(title="Original Title", status="todo")
        created_task = repo.create(task_create)

        task_update = TaskUpdate(title="Updated Title", status="done")
        updated_task = repo.update(created_task, task_update)

        assert updated_task.title == "Updated Title"
        assert updated_task.status == TaskStatus.DONE

    def test_soft_delete_task(self, db_session: Session) -> None:
        """Test soft deleting a task."""
        from app.schemas.task import TaskCreate

        repo = TaskRepository(db_session)
        task_create = TaskCreate(title="Test Task")
        created_task = repo.create(task_create)

        repo.soft_delete(created_task)

        # Task should not be found by default (soft deleted)
        task = repo.get_by_id(created_task.id)
        assert task is None

        # But should be found with include_deleted=True
        task = repo.get_by_id(created_task.id, include_deleted=True)
        assert task is not None
        assert task.deleted_at is not None

    def test_create_event(self, db_session: Session) -> None:
        """Test creating a task event."""
        from app.schemas.task import TaskCreate
        from app.models.task import TaskEventType

        repo = TaskRepository(db_session)
        task_create = TaskCreate(title="Test Task")
        created_task = repo.create(task_create)

        event = repo.create_event(
            task_id=created_task.id,
            event_type=TaskEventType.CREATED,
            payload={"title": "Test Task"},
        )

        assert event.id is not None
        assert event.task_id == created_task.id
        assert event.event_type == TaskEventType.CREATED
        assert event.payload == {"title": "Test Task"}

    def test_get_task_events(self, db_session: Session) -> None:
        """Test getting task events."""
        from app.schemas.task import TaskCreate
        from app.models.task import TaskEventType

        repo = TaskRepository(db_session)
        task_create = TaskCreate(title="Test Task")
        created_task = repo.create(task_create)

        repo.create_event(task_id=created_task.id, event_type=TaskEventType.CREATED, payload={})
        repo.create_event(task_id=created_task.id, event_type=TaskEventType.STATUS_CHANGED, payload={})

        events = repo.get_task_events(created_task.id)
        assert len(events) == 2
        assert events[0].event_type == TaskEventType.CREATED
        assert events[1].event_type == TaskEventType.STATUS_CHANGED


# Service Tests
class TestTaskService:
    """Tests for TaskService."""

    def test_create_task(self, db_session: Session) -> None:
        """Test creating a task through service."""
        from app.schemas.task import TaskCreate

        repo = TaskRepository(db_session)
        service = TaskService(repo)
        task_create = TaskCreate(title="Test Task", description="Test Description")

        task_read = service.create_task(task_create)

        assert task_read.id is not None
        assert task_read.title == "Test Task"
        assert task_read.description == "Test Description"
        assert task_read.status == "todo"

    def test_get_task(self, db_session: Session) -> None:
        """Test getting a task through service."""
        from app.schemas.task import TaskCreate

        repo = TaskRepository(db_session)
        service = TaskService(repo)
        task_create = TaskCreate(title="Test Task")
        created = service.create_task(task_create)

        task_read = service.get_task(created.id)

        assert task_read.id == created.id
        assert task_read.title == "Test Task"

    def test_get_task_not_found(self, db_session: Session) -> None:
        """Test getting a non-existent task raises TaskNotFoundError."""
        from app.services.task import TaskNotFoundError

        repo = TaskRepository(db_session)
        service = TaskService(repo)

        with pytest.raises(TaskNotFoundError):
            service.get_task(999)

    def test_get_tasks(self, db_session: Session) -> None:
        """Test getting all tasks through service."""
        from app.schemas.task import TaskCreate

        repo = TaskRepository(db_session)
        service = TaskService(repo)
        service.create_task(TaskCreate(title="Task 1"))
        service.create_task(TaskCreate(title="Task 2"))

        tasks = service.get_tasks()

        assert len(tasks) == 2

    def test_get_tasks_filtered_by_status(self, db_session: Session) -> None:
        """Test getting tasks filtered by status through service."""
        from app.schemas.task import TaskCreate

        repo = TaskRepository(db_session)
        service = TaskService(repo)
        service.create_task(TaskCreate(title="Task 1", status="todo"))
        service.create_task(TaskCreate(title="Task 2", status="done"))

        tasks = service.get_tasks(status="todo")

        assert len(tasks) == 1
        assert tasks[0].title == "Task 1"

    def test_update_task(self, db_session: Session) -> None:
        """Test updating a task through service."""
        from app.schemas.task import TaskCreate, TaskUpdate

        repo = TaskRepository(db_session)
        service = TaskService(repo)
        created = service.create_task(TaskCreate(title="Original", status="todo"))

        updated = service.update_task(created.id, TaskUpdate(title="Updated", status="done"))

        assert updated.title == "Updated"
        assert updated.status == "done"

    def test_update_task_not_found(self, db_session: Session) -> None:
        """Test updating a non-existent task raises TaskNotFoundError."""
        from app.services.task import TaskNotFoundError
        from app.schemas.task import TaskUpdate

        repo = TaskRepository(db_session)
        service = TaskService(repo)

        with pytest.raises(TaskNotFoundError):
            service.update_task(999, TaskUpdate(title="Updated"))

    def test_delete_task(self, db_session: Session) -> None:
        """Test deleting a task through service."""
        from app.schemas.task import TaskCreate
        from app.services.task import TaskNotFoundError

        repo = TaskRepository(db_session)
        service = TaskService(repo)
        created = service.create_task(TaskCreate(title="Test Task"))

        service.delete_task(created.id)

        with pytest.raises(TaskNotFoundError):
            service.get_task(created.id)

    def test_delete_task_not_found(self, db_session: Session) -> None:
        """Test deleting a non-existent task raises TaskNotFoundError."""
        from app.services.task import TaskNotFoundError

        repo = TaskRepository(db_session)
        service = TaskService(repo)

        with pytest.raises(TaskNotFoundError):
            service.delete_task(999)

    def test_get_task_events(self, db_session: Session) -> None:
        """Test getting task events through service."""
        from app.schemas.task import TaskCreate, TaskUpdate

        repo = TaskRepository(db_session)
        service = TaskService(repo)
        created = service.create_task(TaskCreate(title="Test Task", status="todo"))

        # Update status to create event
        service.update_task(created.id, TaskUpdate(status="done"))

        events = service.get_task_events(created.id)
        assert len(events) == 2
        assert events[0].event_type == "created"
        assert events[1].event_type == "status_changed"

    def test_get_task_events_includes_deleted(self, db_session: Session) -> None:
        """Test getting events for a soft-deleted task."""
        from app.schemas.task import TaskCreate

        repo = TaskRepository(db_session)
        service = TaskService(repo)
        created = service.create_task(TaskCreate(title="Test Task"))

        service.delete_task(created.id)

        # Should still be able to get events for deleted task
        events = service.get_task_events(created.id)
        assert len(events) == 2
        assert events[0].event_type == "created"
        assert events[1].event_type == "deleted"


# API Endpoint Tests
class TestTasksAPI:
    """Tests for tasks API endpoints."""

    @pytest.mark.asyncio
    async def test_create_task(self, client: AsyncClient) -> None:
        """Test POST /tasks endpoint."""
        response = await client.post(
            "/tasks",
            json={"title": "New Task", "description": "Task description", "status": "todo"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "New Task"
        assert data["description"] == "Task description"
        assert data["status"] == "todo"
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

    @pytest.mark.asyncio
    async def test_create_task_minimal(self, client: AsyncClient) -> None:
        """Test POST /tasks with minimal data."""
        response = await client.post("/tasks", json={"title": "Minimal Task"})

        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Minimal Task"
        assert data["description"] is None
        assert data["status"] == "todo"

    @pytest.mark.asyncio
    async def test_create_task_invalid_status(self, client: AsyncClient) -> None:
        """Test POST /tasks with invalid status returns 422."""
        response = await client.post("/tasks", json={"title": "Test", "status": "invalid"})

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_get_tasks(self, client: AsyncClient) -> None:
        """Test GET /tasks endpoint."""
        # Create some tasks first
        await client.post("/tasks", json={"title": "Task 1", "status": "todo"})
        await client.post("/tasks", json={"title": "Task 2", "status": "done"})

        response = await client.get("/tasks")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_get_tasks_filtered_by_status(self, client: AsyncClient) -> None:
        """Test GET /tasks with status filter."""
        await client.post("/tasks", json={"title": "Task 1", "status": "todo"})
        await client.post("/tasks", json={"title": "Task 2", "status": "in_progress"})
        await client.post("/tasks", json={"title": "Task 3", "status": "done"})

        response = await client.get("/tasks?status=todo")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Task 1"
        assert data[0]["status"] == "todo"

    @pytest.mark.asyncio
    async def test_get_task_by_id(self, client: AsyncClient) -> None:
        """Test GET /tasks/{id} endpoint."""
        create_response = await client.post("/tasks", json={"title": "Test Task"})
        task_id = create_response.json()["id"]

        response = await client.get(f"/tasks/{task_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == task_id
        assert data["title"] == "Test Task"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, client: AsyncClient) -> None:
        """Test GET /tasks/{id} returns 404 for non-existent task."""
        response = await client.get("/tasks/999")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_update_task(self, client: AsyncClient) -> None:
        """Test PATCH /tasks/{id} endpoint."""
        create_response = await client.post("/tasks", json={"title": "Original", "status": "todo"})
        task_id = create_response.json()["id"]

        response = await client.patch(
            f"/tasks/{task_id}",
            json={"title": "Updated", "status": "done"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == task_id
        assert data["title"] == "Updated"
        assert data["status"] == "done"

    @pytest.mark.asyncio
    async def test_update_task_partial(self, client: AsyncClient) -> None:
        """Test PATCH /tasks/{id} with partial update."""
        create_response = await client.post("/tasks", json={"title": "Original", "status": "todo"})
        task_id = create_response.json()["id"]

        response = await client.patch(f"/tasks/{task_id}", json={"title": "Updated"})

        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Updated"
        assert data["status"] == "todo"  # unchanged

    @pytest.mark.asyncio
    async def test_update_task_not_found(self, client: AsyncClient) -> None:
        """Test PATCH /tasks/{id} returns 404 for non-existent task."""
        response = await client.patch("/tasks/999", json={"title": "Updated"})

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_delete_task(self, client: AsyncClient) -> None:
        """Test DELETE /tasks/{id} endpoint."""
        create_response = await client.post("/tasks", json={"title": "To Delete"})
        task_id = create_response.json()["id"]

        response = await client.delete(f"/tasks/{task_id}")

        assert response.status_code == 204

        # Verify task is deleted
        get_response = await client.get(f"/tasks/{task_id}")
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_task_not_found(self, client: AsyncClient) -> None:
        """Test DELETE /tasks/{id} returns 404 for non-existent task."""
        response = await client.delete("/tasks/999")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_task_events(self, client: AsyncClient) -> None:
        """Test GET /tasks/{id}/events endpoint."""
        # Create a task
        create_response = await client.post("/tasks", json={"title": "Test Task", "status": "todo"})
        task_id = create_response.json()["id"]

        # Get events - should have created event
        response = await client.get(f"/tasks/{task_id}/events")
        assert response.status_code == 200
        events = response.json()
        assert len(events) == 1
        assert events[0]["event_type"] == "created"

        # Update status - should have status_changed event
        await client.patch(f"/tasks/{task_id}", json={"status": "done"})
        response = await client.get(f"/tasks/{task_id}/events")
        assert response.status_code == 200
        events = response.json()
        assert len(events) == 2
        assert events[1]["event_type"] == "status_changed"
        assert events[1]["payload"]["old_status"] == "todo"
        assert events[1]["payload"]["new_status"] == "done"

        # Update planned_date - should have rescheduled event
        from datetime import datetime, timezone
        new_date = datetime(2026, 12, 31, 12, 0, 0, tzinfo=timezone.utc).isoformat()
        await client.patch(f"/tasks/{task_id}", json={"planned_date": new_date})
        response = await client.get(f"/tasks/{task_id}/events")
        assert response.status_code == 200
        events = response.json()
        assert len(events) == 3
        assert events[2]["event_type"] == "rescheduled"

        # Update title - should have edited event
        await client.patch(f"/tasks/{task_id}", json={"title": "Updated Title"})
        response = await client.get(f"/tasks/{task_id}/events")
        assert response.status_code == 200
        events = response.json()
        assert len(events) == 4
        assert events[3]["event_type"] == "edited"

        # Delete task - should have deleted event
        await client.delete(f"/tasks/{task_id}")
        response = await client.get(f"/tasks/{task_id}/events")
        assert response.status_code == 200
        events = response.json()
        assert len(events) == 5
        assert events[4]["event_type"] == "deleted"

    @pytest.mark.asyncio
    async def test_get_task_events_not_found(self, client: AsyncClient) -> None:
        """Test GET /tasks/{id}/events returns 404 for non-existent task."""
        response = await client.get("/tasks/999/events")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_create_task_with_due_date_and_planned_date(self, client: AsyncClient) -> None:
        """Test POST /tasks with due_date and planned_date."""
        from datetime import datetime, timezone
        due_date = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc).isoformat()
        planned_date = datetime(2026, 12, 15, 10, 0, 0, tzinfo=timezone.utc).isoformat()

        response = await client.post(
            "/tasks",
            json={
                "title": "Task with dates",
                "due_date": due_date,
                "planned_date": planned_date,
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Task with dates"
        assert data["due_date"] is not None
        assert data["planned_date"] is not None

    @pytest.mark.asyncio
    async def test_update_task_due_date_and_planned_date(self, client: AsyncClient) -> None:
        """Test PATCH /tasks/{id} with due_date and planned_date."""
        from datetime import datetime, timezone
        create_response = await client.post("/tasks", json={"title": "Original"})
        task_id = create_response.json()["id"]

        due_date = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc).isoformat()
        planned_date = datetime(2026, 12, 15, 10, 0, 0, tzinfo=timezone.utc).isoformat()

        response = await client.patch(
            f"/tasks/{task_id}",
            json={"due_date": due_date, "planned_date": planned_date},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["due_date"] is not None
        assert data["planned_date"] is not None

    @pytest.mark.asyncio
    async def test_soft_delete_excludes_from_list(self, client: AsyncClient) -> None:
        """Test that soft-deleted tasks are excluded from task list."""
        # Create tasks
        await client.post("/tasks", json={"title": "Task 1"})
        create_response = await client.post("/tasks", json={"title": "Task 2"})
        task_id = create_response.json()["id"]

        # Delete one task
        await client.delete(f"/tasks/{task_id}")

        # List tasks - should only have 1
        response = await client.get("/tasks")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Task 1"