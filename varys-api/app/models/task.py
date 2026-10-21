"""Task model."""

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, String, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.task import TaskEvent


class TaskStatus(str, enum.Enum):
    """Task status enumeration."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class TaskEventType(str, enum.Enum):
    """Task event type enumeration."""

    CREATED = "created"
    STATUS_CHANGED = "status_changed"
    RESCHEDULED = "rescheduled"
    EDITED = "edited"
    DELETED = "deleted"


class Task(Base, TimestampMixin):
    """Task model representing a to-do item."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, native_enum=False),
        default=TaskStatus.TODO,
        nullable=False,
    )
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    planned_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationship to events
    events: Mapped[list["TaskEvent"]] = relationship(
        "TaskEvent", back_populates="task", cascade="all, delete-orphan"
    )


class TaskEvent(Base, TimestampMixin):
    """Task event log model for audit trail."""

    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[TaskEventType] = mapped_column(Enum(TaskEventType, native_enum=False), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Relationship to task
    task: Mapped["Task"] = relationship("Task", back_populates="events")