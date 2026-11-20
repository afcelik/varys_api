"""Calendar service - per-day counts for a month.

Never reads the clock; the caller chooses the month. Task days use only the
date part of planned_date/due_date (no time zone conversion).
"""

import calendar
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta

from app.models.task import TaskStatus
from app.repositories.calendar import CalendarRepository
from app.repositories.routine import RoutineRepository
from app.repositories.schedule import ScheduleRepository
from app.schemas.calendar import CalendarDayRead, CalendarMonthRead
from app.services.recurrence import RecurrenceRule, is_due


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """First and last day of the month."""
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _day_counts(values: Iterable[datetime | None]) -> Counter[date]:
    """Count per calendar day (date part only). Days outside the month are never read:
    month_summary looks up only the month's own days."""
    return Counter(v.date() for v in values if v is not None)


def month_summary(
    year: int,
    month: int,
    tasks: Iterable[tuple[datetime | None, datetime | None, TaskStatus | str]],
    routines: Sequence[RecurrenceRule],
    blocks: Sequence[RecurrenceRule],
) -> CalendarMonthRead:
    """Pure: count open tasks (planned/due), due routines and due blocks per day.

    Done tasks are not counted. Dates outside the month are ignored.
    """
    first, last = month_bounds(year, month)
    open_tasks = [t for t in tasks if TaskStatus(t[2]) is not TaskStatus.DONE]
    planned = _day_counts(t[0] for t in open_tasks)
    due = _day_counts(t[1] for t in open_tasks)

    days = []
    day = first
    while day <= last:
        days.append(
            CalendarDayRead(
                day=day,
                task_count=planned[day],
                due_count=due[day],
                routine_count=sum(1 for r in routines if is_due(r, day)),
                schedule_count=sum(1 for b in blocks if is_due(b, day)),
            )
        )
        day += timedelta(days=1)
    return CalendarMonthRead(year=year, month=month, days=days)


class CalendarService:
    """Month summary from three reads (tasks, routines, blocks); no writes."""

    def __init__(
        self,
        calendar_repo: CalendarRepository,
        routine_repo: RoutineRepository,
        schedule_repo: ScheduleRepository,
    ):
        self.calendar_repo = calendar_repo
        self.routine_repo = routine_repo
        self.schedule_repo = schedule_repo

    def get_month(self, year: int, month: int) -> CalendarMonthRead:
        first, last = month_bounds(year, month)
        tasks = self.calendar_repo.get_task_dates_in_range(first, last)
        routines = self.routine_repo.get_started_by(last)
        blocks = self.schedule_repo.get_started_by(last)
        return month_summary(year, month, tasks, routines, blocks)
