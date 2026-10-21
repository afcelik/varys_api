"""Task service - business logic layer for tasks."""

from typing import Optional

from app.models.task import Task, TaskStatus, TaskEventType
from app.repositories.task import TaskRepository
from app.schemas.task import TaskCreate, TaskRead, TaskUpdate, TaskEventRead


class TaskNotFoundError(Exception):
    """Raised when a task is not found."""

    pass


class TaskService:
    """Service for Task operations - contains business logic."""

    def __init__(self, repository: TaskRepository):
        self.repository = repository

    def create_task(self, task_create: TaskCreate) -> TaskRead:
        """Create a new task."""
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

    def get_tasks(self, status: Optional[str] = None) -> list[TaskRead]:
        """Get all tasks, optionally filtered by status."""
        task_status = TaskStatus(status) if status else None
        tasks = self.repository.get_all(task_status)
        return [TaskRead.model_validate(task) for task in tasks]

    def update_task(self, task_id: int, task_update: TaskUpdate) -> TaskRead:
        """Update an existing task."""
        task = self.repository.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError(f"Task with id {task_id} not found")

        # Track old values for events
        old_status = task.status
        old_planned_date = task.planned_date
        old_title = task.title
        old_description = task.description
        old_due_date = task.due_date

        updated_task = self.repository.update(task, task_update)

        # Create events for changes
        if task_update.status is not None and task_update.status != old_status.value:
            self.repository.create_event(
                task_id=task_id,
                event_type=TaskEventType.STATUS_CHANGED,
                payload={"old_status": old_status.value, "new_status": task_update.status},
            )

        if task_update.planned_date is not None and task_update.planned_date != old_planned_date:
            self.repository.create_event(
                task_id=task_id,
                event_type=TaskEventType.RESCHEDULED,
                payload={
                    "old_planned_date": old_planned_date.isoformat() if old_planned_date else None,
                    "new_planned_date": task_update.planned_date.isoformat(),
                },
            )

        # If any other field changed, create edited event
        if (task_update.title is not None and task_update.title != old_title) or \
           (task_update.description is not None and task_update.description != old_description) or \
           (task_update.due_date is not None and task_update.due_date != old_due_date):
            self.repository.create_event(
                task_id=task_id,
                event_type=TaskEventType.EDITED,
                payload={
                    "fields_changed": [k for k, v in task_update.model_dump(exclude_unset=True).items() if v is not None]
                },
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