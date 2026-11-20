"""Schedule block model: a recurring time block (e.g. weekdays 07:00-08:00)."""

from datetime import date, datetime, time

from sqlalchemy import JSON, CheckConstraint, Date, DateTime, Enum, Integer, String, Time
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.routine import RecurrenceType


class ScheduleBlock(Base, TimestampMixin):
    """A recurring time block on plain calendar dates; recurrence works like routines."""

    __tablename__ = "schedule_blocks"
    __table_args__ = (
        # SQLite stores Time as fixed-width 'HH:MM:SS.ffffff' text, so text comparison is correct.
        CheckConstraint("end_time > start_time", name="ck_schedule_blocks_end_after_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    # Same storage as Routine.recurrence_type and its recurrence columns.
    recurrence_type: Mapped[RecurrenceType] = mapped_column(
        Enum(
            RecurrenceType,
            native_enum=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    days_of_week: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
