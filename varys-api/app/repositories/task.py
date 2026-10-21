"""Task repository - data access layer for tasks."""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.task import Task, TaskStatus
from app.schemas.task import TaskCreate, TaskUpdate


class TaskRepository:
    """Repository for Task model - handles all database operations."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, task_create: TaskCreate) -> Task:
        """Create a new task in the database."""
        task = Task(
            title=task_create.title,
            description=task_create.description,
            status=TaskStatus(task_create.status) if task_create.status else TaskStatus.TODO,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def get_by_id(self, task_id: int) -> Optional[Task]:
        """Get a task by its ID."""
        return self.db.get(Task, task_id)

    def get_all(self, status: Optional[TaskStatus] = None) -> list[Task]:
        """Get all tasks, optionally filtered by status."""
        stmt = select(Task)
        if status:
            stmt = stmt.where(Task.status == status)
        stmt = stmt.order_by(Task.created_at.desc())
        return list(self.db.scalars(stmt).all())

    def update(self, task: Task, task_update: TaskUpdate) -> Task:
        """Update an existing task."""
        if task_update.title is not None:
            task.title = task_update.title
        if task_update.description is not None:
            task.description = task_update.description
        if task_update.status is not None:
            task.status = TaskStatus(task_update.status)
        self.db.commit()
        self.db.refresh(task)
        return task

    def delete(self, task: Task) -> None:
        """Delete a task from the database."""
        self.db.delete(task)
        self.db.commit()