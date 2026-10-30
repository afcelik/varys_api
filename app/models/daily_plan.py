"""DailyPlan model."""

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.task import Task


class DailyPlan(Base, TimestampMixin):
    """DailyPlan model representing a day's plan with focus tasks."""

    __tablename__ = "daily_plans"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    main_focus_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id"), nullable=True
    )
    side_focus_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id"), nullable=True
    )
    say_no_text: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Relationships to tasks
    main_focus: Mapped["Task | None"] = relationship(
        "Task",
        foreign_keys=[main_focus_task_id],
        lazy="selectin",
    )
    side_focus: Mapped["Task | None"] = relationship(
        "Task",
        foreign_keys=[side_focus_task_id],
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("plan_date", name="uq_daily_plans_plan_date"),
    )