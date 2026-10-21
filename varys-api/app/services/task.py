"""Task service - business logic layer for tasks."""

from typing import Optional

from app.models.task import Task, TaskStatus
from app.repositories.task import TaskRepository
from app.schemas.task import TaskCreate, TaskRead, TaskUpdate


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
        updated_task = self.repository.update(task, task_update)
        return TaskRead.model_validate(updated_task)

    def delete_task(self, task_id: int) -> None:
        """Delete a task."""
        task = self.repository.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError(f"Task with id {task_id} not found")
        self.repository.delete(task)