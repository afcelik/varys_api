"""Calendar repository - read-only task date lookups for a month summary."""

from datetime import date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.task import Task, TaskStatus

TaskDates = tuple[datetime | None, datetime | None, TaskStatus]


class CalendarRepository:
    """Read-only queries; never commits."""

    def __init__(self, db: Session):
        self.db = db

    def get_task_dates_in_range(self, first: date, last: date) -> list[TaskDates]:
        """(planned_date, due_date, status) of non-deleted tasks whose planned or due
        day falls in [first, last]. One query; only the day part is compared."""
        planned_day = func.date(Task.planned_date)
        due_day = func.date(Task.due_date)
        stmt = select(Task.planned_date, Task.due_date, Task.status).where(
            Task.deleted_at.is_(None),
            or_(planned_day.between(first, last), due_day.between(first, last)),
        )
        return [tuple(row) for row in self.db.execute(stmt)]
