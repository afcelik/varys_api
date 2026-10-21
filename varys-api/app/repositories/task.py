"""Task repository - data access layer for tasks."""

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.task import Task, TaskStatus, TaskEvent, TaskEventType
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
            due_date=task_create.due_date,
            planned_date=task_create.planned_date,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def get_by_id(self, task_id: int, include_deleted: bool = False) -> Optional[Task]:
        """Get a task by its ID."""
        stmt = select(Task).where(Task.id == task_id)
        if not include_deleted:
            stmt = stmt.where(Task.deleted_at.is_(None))
        return self.db.scalars(stmt).first()

    def get_all(self, status: Optional[TaskStatus] = None, include_deleted: bool = False) -> list[Task]:
        """Get all tasks, optionally filtered by status."""
        stmt = select(Task)
        if not include_deleted:
            stmt = stmt.where(Task.deleted_at.is_(None))
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
        if task_update.due_date is not None:
            task.due_date = task_update.due_date
        if task_update.planned_date is not None:
            task.planned_date = task_update.planned_date
        self.db.commit()
        self.db.refresh(task)
        return task

    def soft_delete(self, task: Task) -> None:
        """Soft delete a task by setting deleted_at timestamp."""
        task.deleted_at = datetime.now(task.deleted_at.tzinfo if task.deleted_at else None)
        self.db.commit()
        self.db.refresh(task)

    def create_event(
        self,
        task_id: int,
        event_type: TaskEventType,
        payload: Optional[dict] = None,
    ) -> TaskEvent:
        """Create a task event for audit trail."""
        event = TaskEvent(task_id=task_id, event_type=event_type, payload=payload)
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def get_task_events(self, task_id: int) -> list[TaskEvent]:
        """Get all events for a task."""
        stmt = select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.created_at.asc())
        return list(self.db.scalars(stmt).all())