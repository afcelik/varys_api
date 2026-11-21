"""Routine and routine log models."""

import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class RecurrenceType(enum.StrEnum):
    """How a routine repeats."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    INTERVAL = "interval"
    ONCE = "once"


class Routine(Base, TimestampMixin):
    """A recurring routine scheduled on plain calendar dates."""

    __tablename__ = "routines"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    recurrence_type: Mapped[RecurrenceType] = mapped_column(
        Enum(
            RecurrenceType,
            native_enum=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    # Weekly only. JSON array of weekday ints (Monday=0 ... Sunday=6), stored sorted and unique.
    # SQLite has no array type; JSON maps 1:1 to list[int] without custom parsing
    # and the values are never filtered in SQL.
    days_of_week: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RoutineLog(Base):
    """A completion record: a row means the routine was done on log_date."""

    __tablename__ = "routine_logs"
    __table_args__ = (
        UniqueConstraint("routine_id", "log_date", name="uq_routine_logs_routine_id_log_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    routine_id: Mapped[int] = mapped_column(
        ForeignKey("routines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    log_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
