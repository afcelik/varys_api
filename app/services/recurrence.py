"""Pure recurrence and streak functions for routines.

No database access and no clock: every date comes from the caller.
A routine day is a plain calendar date (``datetime.date``) with no time zone
conversion. Streaks count consecutive *scheduled* occurrences, not calendar days.
"""

import calendar
from collections.abc import Collection, Iterator
from datetime import date, timedelta
from typing import Protocol

from app.models.routine import RecurrenceType

# Accepted range for any routine day (path params, start_date).
MIN_DAY = date(2000, 1, 1)
MAX_DAY = date(2100, 12, 31)

_ONE_DAY = timedelta(days=1)


class RecurrenceRule(Protocol):
    """Anything carrying a routine's schedule (the ORM model, a schema, a test dataclass)."""

    start_date: date
    recurrence_type: RecurrenceType | str
    days_of_week: list[int] | None
    day_of_month: int | None
    interval_days: int | None


class RecurrenceError(ValueError):
    """The recurrence fields are inconsistent with the recurrence type."""


def validate_recurrence(
    recurrence_type: RecurrenceType | str,
    days_of_week: list[int] | None,
    day_of_month: int | None,
    interval_days: int | None,
) -> None:
    """Raise RecurrenceError unless exactly the fields the type needs are set.

    weekly   -> days_of_week non-empty (each 0..6), the others null
    monthly  -> day_of_month (1..31), the others null
    interval -> interval_days (>= 1), the others null
    daily    -> all three null
    once     -> all three null
    """
    rtype = RecurrenceType(recurrence_type)
    required = {
        RecurrenceType.DAILY: None,
        RecurrenceType.WEEKLY: "days_of_week",
        RecurrenceType.MONTHLY: "day_of_month",
        RecurrenceType.INTERVAL: "interval_days",
        RecurrenceType.ONCE: None,
    }[rtype]
    values = {
        "days_of_week": days_of_week,
        "day_of_month": day_of_month,
        "interval_days": interval_days,
    }

    for name, value in values.items():
        if name != required and value is not None:
            raise RecurrenceError(f"{name} must be null for recurrence_type '{rtype.value}'")

    if rtype is RecurrenceType.WEEKLY:
        if not days_of_week:
            raise RecurrenceError("days_of_week must be a non-empty list for weekly routines")
        if any(d < 0 or d > 6 for d in days_of_week):
            raise RecurrenceError("days_of_week values must be between 0 (Monday) and 6 (Sunday)")
    elif rtype is RecurrenceType.MONTHLY:
        if day_of_month is None or not 1 <= day_of_month <= 31:
            raise RecurrenceError("day_of_month must be between 1 and 31 for monthly routines")
    elif rtype is RecurrenceType.INTERVAL:
        if interval_days is None or interval_days < 1:
            raise RecurrenceError("interval_days must be >= 1 for interval routines")


def normalize_days_of_week(days: list[int] | None) -> list[int] | None:
    """Sorted, de-duplicated weekdays (None stays None)."""
    return None if days is None else sorted(set(days))


def is_due(rule: RecurrenceRule, day: date) -> bool:
    """Whether the routine is scheduled on ``day``.

    Never due before start_date. Monthly routines whose day_of_month does not
    exist in a month (e.g. 31 in April, 30/31 in February) are due on that
    month's last day, so they happen exactly once every month. Once routines are
    due only on start_date.
    """
    if day < rule.start_date:
        return False

    rtype = RecurrenceType(rule.recurrence_type)
    if rtype is RecurrenceType.DAILY:
        return True
    if rtype is RecurrenceType.WEEKLY:
        return day.weekday() in (rule.days_of_week or ())
    if rtype is RecurrenceType.MONTHLY:
        last_day = calendar.monthrange(day.year, day.month)[1]
        return day.day == min(rule.day_of_month, last_day)
    if rtype is RecurrenceType.ONCE:
        return day == rule.start_date
    # INTERVAL
    return (day - rule.start_date).days % rule.interval_days == 0


def scheduled_dates(rule: RecurrenceRule, start: date, end: date) -> list[date]:
    """All scheduled days in [start, end] (inclusive), ascending."""
    day = max(start, rule.start_date)
    result: list[date] = []
    while day <= end:
        if is_due(rule, day):
            result.append(day)
        day += _ONE_DAY
    return result


def _scheduled_backwards(rule: RecurrenceRule, reference: date) -> Iterator[date]:
    """Scheduled days from ``reference`` back to start_date, newest first (lazy)."""
    day = reference
    while day >= rule.start_date:
        if is_due(rule, day):
            yield day
        day -= _ONE_DAY


def current_streak(rule: RecurrenceRule, reference: date, completed: Collection[date]) -> int:
    """Consecutive completed scheduled days ending at ``reference``.

    Walks scheduled days from ``reference`` (inclusive) backwards:
    - a completed scheduled day adds 1;
    - the reference day itself, if scheduled but not completed, is skipped and does
      NOT break the streak. This holds for any reference the caller passes, including
      a past date: the server never knows "today", so the reference day is always
      treated as possibly still open;
    - the first incomplete scheduled day before the reference ends the streak;
    - unscheduled days are never looked at, and logs on unscheduled days are ignored.
    """
    done = completed if isinstance(completed, (set, frozenset)) else set(completed)
    streak = 0
    for day in _scheduled_backwards(rule, reference):
        if day in done:
            streak += 1
        elif day == reference:
            continue
        else:
            break
    return streak


def longest_streak(rule: RecurrenceRule, completed: Collection[date]) -> int:
    """Longest run of consecutive completed scheduled days in the routine's history.

    History is start_date .. the latest completion; nothing later can extend a run.
    An incomplete scheduled day resets the run; logs on unscheduled days are ignored.
    """
    if not completed:
        return 0
    done = completed if isinstance(completed, (set, frozenset)) else set(completed)
    best = run = 0
    for day in scheduled_dates(rule, rule.start_date, max(done)):
        if day in done:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best
