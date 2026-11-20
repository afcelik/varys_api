"""Pydantic schemas for the month calendar summary."""

from datetime import date

from pydantic import BaseModel


class CalendarDayRead(BaseModel):
    """Counts for one calendar day (plain date, no time zone)."""

    day: date
    task_count: int
    due_count: int
    routine_count: int
    schedule_count: int


class CalendarMonthRead(BaseModel):
    """Every day of a month, in order; empty days have zero counts."""

    year: int
    month: int
    days: list[CalendarDayRead]
