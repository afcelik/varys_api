"""Project and project event models."""

import enum
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ProjectStatus(enum.StrEnum):
    """Project lifecycle status. Only ACTIVE counts toward MAX_ACTIVE_PROJECTS."""

    ACTIVE = "active"
    ON_HOLD = "on_hold"
    SOMEDAY = "someday"
    COMPLETED = "completed"


class ProjectEventType(enum.StrEnum):
    """Project event type enumeration."""

    CREATED = "created"
    STATUS_CHANGED = "status_changed"
    EDITED = "edited"


class Project(Base, TimestampMixin):
    """A project grouping tasks; dates are plain calendar dates (no time zone)."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(
            ProjectStatus,
            native_enum=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        default=ProjectStatus.ACTIVE,
        nullable=False,
    )
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectEvent(Base):
    """Project event log (audit trail), mirroring TaskEvent."""

    __tablename__ = "project_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[ProjectEventType] = mapped_column(
        Enum(
            ProjectEventType,
            native_enum=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
