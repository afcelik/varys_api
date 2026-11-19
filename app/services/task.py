"""Task service - business logic layer for tasks."""

from typing import Optional

from app.models.task import Task, TaskStatus, TaskEventType
from app.repositories.task import TaskRepository
from app.schemas.task import TaskCreate, TaskRead, TaskUpdate, TaskEventRead
from app.services.project import ProjectNotFoundError


# Fields whose change produces an EDITED event (status/planned_date have their own events).
_EDIT_FIELDS = ("title", "description", "due_date", "project_id")


class TaskNotFoundError(Exception):
    """Raised when a task is not found."""

    pass


class TaskService:
    """Service for Task operations - contains business logic."""

    def __init__(self, repository: TaskRepository):
        self.repository = repository

    def _ensure_project(self, project_id: int | None) -> None:
        """A linked project must exist and not be deleted (any status, incl. completed)."""
        if project_id is not None and not self.repository.project_exists(project_id):
            raise ProjectNotFoundError(f"Project with id {project_id} not found")

    def create_task(self, task_create: TaskCreate) -> TaskRead:
        """Create a new task."""
        self._ensure_project(task_create.project_id)
        task = self.repository.create(task_create)
        # Create event for task creation
        self.repository.create_event(
            task_id=task.id,
            event_type=TaskEventType.CREATED,
            payload={"title": task.title, "status": task.status.value},
        )
        return TaskRead.model_validate(task)

    def get_task(self, task_id: int) -> TaskRead:
        """Get a task by ID."""
        task = self.repository.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError(f"Task with id {task_id} not found")
        return TaskRead.model_validate(task)

    def get_tasks(
        self, status: Optional[str] = None, project_id: int | None = None
    ) -> list[TaskRead]:
        """Get all tasks, optionally filtered by status and/or project."""
        task_status = TaskStatus(status) if status else None
        tasks = self.repository.get_all(task_status, project_id=project_id)
        return [TaskRead.model_validate(task) for task in tasks]

    def update_task(self, task_id: int, task_update: TaskUpdate) -> TaskRead:
        """Update an existing task; events reflect real value changes only."""
        task = self.repository.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError(f"Task with id {task_id} not found")

        changes = task_update.changes()
        if "project_id" in changes:
            self._ensure_project(changes["project_id"])
        old = {name: getattr(task, name) for name in changes}
        changed = [
            name
            for name, value in changes.items()
            if (TaskStatus(value) if name == "status" else value) != old[name]
        ]

        updated_task = self.repository.update(task, task_update)

        if "status" in changed:
            self.repository.create_event(
                task_id=task_id,
                event_type=TaskEventType.STATUS_CHANGED,
                payload={"old_status": old["status"].value, "new_status": changes["status"]},
            )

        if "planned_date" in changed:
            old_planned = old["planned_date"]
            new_planned = changes["planned_date"]
            self.repository.create_event(
                task_id=task_id,
                event_type=TaskEventType.RESCHEDULED,
                payload={
                    "old_planned_date": old_planned.isoformat() if old_planned else None,
                    "new_planned_date": new_planned.isoformat() if new_planned else None,
                },
            )

        edited = [name for name in changed if name in _EDIT_FIELDS]
        if edited:
            payload = {"fields_changed": changed}
            if "project_id" in changed:
                payload["old_project_id"] = old["project_id"]
                payload["new_project_id"] = changes["project_id"]
            self.repository.create_event(
                task_id=task_id,
                event_type=TaskEventType.EDITED,
                payload=payload,
            )

        return TaskRead.model_validate(updated_task)

    def delete_task(self, task_id: int) -> None:
        """Soft delete a task."""
        task = self.repository.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError(f"Task with id {task_id} not found")

        # Create event before soft delete
        self.repository.create_event(
            task_id=task_id,
            event_type=TaskEventType.DELETED,
            payload={"title": task.title, "status": task.status.value},
        )

        self.repository.soft_delete(task)

    def get_task_events(self, task_id: int) -> list[TaskEventRead]:
        """Get all events for a task."""
        # Verify task exists (including deleted ones)
        task = self.repository.get_by_id(task_id, include_deleted=True)
        if not task:
            raise TaskNotFoundError(f"Task with id {task_id} not found")

        events = self.repository.get_task_events(task_id)
        return [TaskEventRead.model_validate(event) for event in events]