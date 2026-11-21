"""Tests for the pure recurrence / streak functions (no database)."""

from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from app.services import recurrence
from app.services.recurrence import (
    RecurrenceError,
    current_streak,
    is_due,
    longest_streak,
    normalize_days_of_week,
    scheduled_dates,
    validate_recurrence,
)

# 2024-01-01 is a Monday, so 2024-01-01 + n is weekday n for n in 0..6.
MONDAY = date(2024, 1, 1)


@dataclass
class Rule:
    start_date: date
    recurrence_type: str
    days_of_week: list[int] | None = None
    day_of_month: int | None = None
    interval_days: int | None = None


def daily(start: date = date(2024, 1, 1)) -> Rule:
    return Rule(start, "daily")


def weekly(days: list[int], start: date = date(2024, 1, 1)) -> Rule:
    return Rule(start, "weekly", days_of_week=days)


def monthly(dom: int, start: date = date(2024, 1, 1)) -> Rule:
    return Rule(start, "monthly", day_of_month=dom)


def every(n: int, start: date = date(2024, 1, 1)) -> Rule:
    return Rule(start, "interval", interval_days=n)


def d(s: str) -> date:
    return date.fromisoformat(s)


class TestValidateRecurrence:
    @pytest.mark.parametrize(
        "args",
        [
            ("daily", None, None, None),
            ("weekly", [0, 6], None, None),
            ("monthly", None, 31, None),
            ("interval", None, None, 1),
        ],
    )
    def test_consistent_combinations_pass(self, args) -> None:
        assert validate_recurrence(*args) is None

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (("weekly", None, None, None), "non-empty"),
            (("weekly", [], None, None), "non-empty"),
            (("weekly", [7], None, None), "between 0"),
            (("weekly", [-1], None, None), "between 0"),
            (("weekly", [1], 5, None), "day_of_month must be null"),
            (("monthly", None, None, None), "day_of_month"),
            (("monthly", None, 0, None), "day_of_month"),
            (("monthly", None, 32, None), "day_of_month"),
            (("monthly", [1], 5, None), "days_of_week must be null"),
            (("interval", None, None, None), "interval_days"),
            (("interval", None, None, 0), "interval_days"),
            (("interval", None, 3, 2), "day_of_month must be null"),
            (("daily", [0], None, None), "days_of_week must be null"),
            (("daily", None, 1, None), "day_of_month must be null"),
            (("daily", None, None, 2), "interval_days must be null"),
        ],
    )
    def test_inconsistent_combinations_raise(self, args, message) -> None:
        with pytest.raises(RecurrenceError, match=message):
            validate_recurrence(*args)

    def test_unknown_type_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            validate_recurrence("yearly", None, None, None)

    def test_normalize_days_sorts_and_dedupes(self) -> None:
        assert normalize_days_of_week([4, 0, 4, 2]) == [0, 2, 4]
        assert normalize_days_of_week(None) is None


class TestIsDue:
    def test_daily_due_every_day_from_start(self) -> None:
        rule = daily(d("2024-01-10"))
        assert all(is_due(rule, d("2024-01-10") + timedelta(days=i)) for i in range(400))

    @pytest.mark.parametrize(
        "rule",
        [daily(d("2024-03-10")), weekly([0, 1, 2, 3, 4, 5, 6], d("2024-03-10")),
         monthly(9, d("2024-03-10")), every(1, d("2024-03-10"))],
        ids=["daily", "weekly", "monthly", "interval"],
    )
    def test_never_due_before_start_date(self, rule: Rule) -> None:
        assert is_due(rule, d("2024-03-09")) is False
        assert is_due(rule, d("2023-03-10")) is False

    @pytest.mark.parametrize("weekday", range(7))
    def test_weekly_each_weekday(self, weekday: int) -> None:
        rule = weekly([weekday])
        week = [MONDAY + timedelta(days=i) for i in range(7)]
        assert [day for day in week if is_due(rule, day)] == [MONDAY + timedelta(days=weekday)]

    def test_weekly_multiple_days(self) -> None:
        rule = weekly([0, 2, 4])  # Mon, Wed, Fri
        two_weeks = [MONDAY + timedelta(days=i) for i in range(14)]
        due = [day.isoformat() for day in two_weeks if is_due(rule, day)]
        assert due == ["2024-01-01", "2024-01-03", "2024-01-05",
                       "2024-01-08", "2024-01-10", "2024-01-12"]

    def test_monthly_regular_day(self) -> None:
        rule = monthly(15)
        assert is_due(rule, d("2024-05-15"))
        assert not is_due(rule, d("2024-05-14"))
        assert not is_due(rule, d("2024-04-30"))  # no clamp when the 15th exists

    def test_monthly_31_in_february_non_leap_is_28th(self) -> None:
        rule = monthly(31)
        assert is_due(rule, d("2027-02-28"))
        assert not is_due(rule, d("2027-02-27"))

    def test_monthly_31_in_february_leap_year_is_29th(self) -> None:
        rule = monthly(31)
        assert is_due(rule, d("2028-02-29"))
        assert not is_due(rule, d("2028-02-28"))

    def test_monthly_29_and_30_in_february(self) -> None:
        assert is_due(monthly(29), d("2027-02-28"))  # non-leap: clamped
        assert is_due(monthly(29), d("2028-02-29"))  # leap: exact
        assert not is_due(monthly(29), d("2028-02-28"))
        assert is_due(monthly(30), d("2028-02-29"))

    def test_monthly_31_in_30_day_month_is_30th(self) -> None:
        rule = monthly(31)
        for month in (4, 6, 9, 11):
            assert is_due(rule, date(2026, month, 30))
            assert not is_due(rule, date(2026, month, 29))

    def test_monthly_31_in_31_day_month_is_31st_only(self) -> None:
        rule = monthly(31)
        assert is_due(rule, d("2026-01-31"))
        assert not is_due(rule, d("2026-01-30"))

    def test_monthly_happens_exactly_once_per_month(self) -> None:
        rule = monthly(31)
        dates = scheduled_dates(rule, d("2026-01-01"), d("2026-12-31"))
        assert [x.month for x in dates] == list(range(1, 13))

    def test_interval_boundaries(self) -> None:
        start = d("2024-01-10")
        rule = every(3, start)
        assert is_due(rule, start)
        assert not is_due(rule, start + timedelta(days=1))
        assert not is_due(rule, start + timedelta(days=2))
        assert is_due(rule, start + timedelta(days=3))
        assert is_due(rule, start + timedelta(days=300))
        assert not is_due(rule, start + timedelta(days=301))

    def test_interval_one_is_every_day(self) -> None:
        rule = every(1, d("2024-01-10"))
        assert all(is_due(rule, d("2024-01-10") + timedelta(days=i)) for i in range(60))

    def test_interval_counts_from_start_date_across_month_and_year(self) -> None:
        rule = every(7, d("2024-12-28"))
        assert is_due(rule, d("2025-01-04"))
        assert not is_due(rule, d("2025-01-03"))


class TestScheduledDates:
    def test_clamps_range_to_start_date(self) -> None:
        rule = daily(d("2024-01-05"))
        assert scheduled_dates(rule, d("2024-01-01"), d("2024-01-07")) == [
            d("2024-01-05"), d("2024-01-06"), d("2024-01-07")
        ]

    def test_inclusive_range_and_empty_when_reversed(self) -> None:
        rule = every(2, d("2024-01-01"))
        assert scheduled_dates(rule, d("2024-01-01"), d("2024-01-05")) == [
            d("2024-01-01"), d("2024-01-03"), d("2024-01-05")
        ]
        assert scheduled_dates(rule, d("2024-01-05"), d("2024-01-01")) == []

    def test_monthly_end_of_month_series(self) -> None:
        assert scheduled_dates(monthly(31), d("2026-01-01"), d("2026-04-30")) == [
            d("2026-01-31"), d("2026-02-28"), d("2026-03-31"), d("2026-04-30")
        ]


class TestCurrentStreak:
    def test_unbroken_daily_streak(self) -> None:
        rule = daily(d("2024-01-01"))
        logs = {d("2024-01-01") + timedelta(days=i) for i in range(5)}
        assert current_streak(rule, d("2024-01-05"), logs) == 5

    def test_gap_in_the_middle_ends_streak(self) -> None:
        rule = daily(d("2024-01-01"))
        logs = {d("2024-01-01"), d("2024-01-02"), d("2024-01-04"), d("2024-01-05")}
        assert current_streak(rule, d("2024-01-05"), logs) == 2

    def test_reference_day_not_done_does_not_break(self) -> None:
        rule = daily(d("2024-01-01"))
        logs = {d("2024-01-01"), d("2024-01-02"), d("2024-01-03")}
        assert current_streak(rule, d("2024-01-04"), logs) == 3

    def test_reference_day_done_is_counted(self) -> None:
        rule = daily(d("2024-01-01"))
        logs = {d("2024-01-01"), d("2024-01-02"), d("2024-01-03"), d("2024-01-04")}
        assert current_streak(rule, d("2024-01-04"), logs) == 4

    def test_past_reference_day_not_done_is_still_skipped(self) -> None:
        # The reference-day skip is not tied to "today": a past, never-completed
        # reference day is skipped too and the streak is counted from the day before.
        rule = daily(d("2024-01-01"))
        logs = {d("2024-01-01"), d("2024-01-02"), d("2024-01-05"), d("2024-01-06")}
        assert current_streak(rule, d("2024-01-03"), logs) == 2
        # ...but the day before the reference is a normal past day and does break it.
        assert current_streak(rule, d("2024-01-04"), logs) == 0

    def test_unscheduled_days_do_not_break_weekly(self) -> None:
        rule = weekly([0, 2, 4])  # Mon/Wed/Fri, Tue/Thu/weekends unscheduled
        logs = {d("2024-01-01"), d("2024-01-03"), d("2024-01-05"), d("2024-01-08")}
        assert current_streak(rule, d("2024-01-08"), logs) == 4
        # Tuesday reference: not scheduled, streak still reaches back through Monday.
        assert current_streak(rule, d("2024-01-09"), logs) == 4

    def test_unscheduled_days_do_not_break_interval(self) -> None:
        rule = every(3, d("2024-01-01"))
        logs = {d("2024-01-01"), d("2024-01-04"), d("2024-01-07")}
        assert current_streak(rule, d("2024-01-09"), logs) == 3

    def test_logs_on_unscheduled_days_are_ignored(self) -> None:
        rule = weekly([0])  # Mondays
        logs = {d("2024-01-01"), d("2024-01-02"), d("2024-01-03"), d("2024-01-15")}
        # Monday 2024-01-08 missed -> streak at 01-15 is 1; Tue/Wed logs don't help.
        assert current_streak(rule, d("2024-01-15"), logs) == 1

    def test_reference_before_start_date_is_zero(self) -> None:
        rule = daily(d("2024-01-10"))
        assert current_streak(rule, d("2024-01-05"), {d("2024-01-05")}) == 0

    def test_no_logs_is_zero(self) -> None:
        assert current_streak(daily(), d("2024-02-01"), set()) == 0

    def test_monthly_streak_across_short_months(self) -> None:
        rule = monthly(31, d("2026-01-01"))
        logs = {d("2026-01-31"), d("2026-02-28"), d("2026-03-31"), d("2026-04-30")}
        assert current_streak(rule, d("2026-05-15"), logs) == 4

    def test_accepts_any_collection(self) -> None:
        rule = daily(d("2024-01-01"))
        assert current_streak(rule, d("2024-01-02"), [d("2024-01-01"), d("2024-01-02")]) == 2


class TestLongestStreak:
    def test_picks_longest_run(self) -> None:
        rule = daily(d("2024-01-01"))
        logs = {d("2024-01-01"), d("2024-01-02"),                  # run of 2
                d("2024-01-04"), d("2024-01-05"), d("2024-01-06"),  # run of 3
                d("2024-01-08")}                                    # run of 1
        assert longest_streak(rule, logs) == 3

    def test_unscheduled_days_do_not_break_run(self) -> None:
        rule = weekly([0, 4])  # Mon/Fri
        logs = {d("2024-01-01"), d("2024-01-05"), d("2024-01-08"), d("2024-01-12")}
        assert longest_streak(rule, logs) == 4

    def test_logs_on_unscheduled_days_are_ignored(self) -> None:
        rule = every(2, d("2024-01-01"))
        logs = {d("2024-01-02"), d("2024-01-04"), d("2024-01-06")}
        assert longest_streak(rule, logs) == 0

    def test_no_logs_is_zero(self) -> None:
        assert longest_streak(daily(), set()) == 0

    def test_logs_before_start_date_are_ignored(self) -> None:
        rule = daily(d("2024-02-01"))
        assert longest_streak(rule, {d("2024-01-30"), d("2024-01-31")}) == 0


class TestNoSystemClock:
    """Results depend only on the inputs: same logs, different references."""

    LOGS = {d("2024-01-01"), d("2024-01-02"), d("2024-01-03"), d("2024-01-05")}

    @pytest.mark.parametrize(
        ("reference", "expected"),
        [
            ("2023-12-31", 0),  # before start_date
            ("2024-01-01", 1),
            ("2024-01-03", 3),
            ("2024-01-04", 3),  # reference not done -> skipped
            ("2024-01-05", 1),  # 01-04 is a past miss
            ("2024-01-06", 1),  # reference not done -> skipped
            ("2024-01-07", 0),  # 01-06 is a past miss
            ("2099-12-31", 0),
        ],
    )
    def test_current_streak_is_function_of_reference_only(self, reference, expected) -> None:
        assert current_streak(daily(d("2024-01-01")), d(reference), self.LOGS) == expected

    def test_longest_streak_has_no_reference(self) -> None:
        assert longest_streak(daily(d("2024-01-01")), self.LOGS) == 3

    def test_module_never_reads_the_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class NoClockDate(date):
            @classmethod
            def today(cls):
                raise AssertionError("recurrence must not read the system clock")

        monkeypatch.setattr(recurrence, "date", NoClockDate)
        rule = daily(d("2024-01-01"))
        assert current_streak(rule, d("2024-01-04"), self.LOGS) == 3
        assert longest_streak(rule, self.LOGS) == 3
        assert len(scheduled_dates(rule, d("2024-01-01"), d("2024-01-31"))) == 31
        assert not hasattr(recurrence, "datetime")


# --- once (one-off) -----------------------------------------------------------------------


def once(start: date = date(2024, 1, 10)) -> Rule:
    return Rule(start, "once")


class TestOnce:
    def test_validate_all_null_passes(self) -> None:
        assert validate_recurrence("once", None, None, None) is None

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (("once", [0], None, None), "days_of_week must be null for recurrence_type 'once'"),
            (("once", None, 5, None), "day_of_month must be null for recurrence_type 'once'"),
            (("once", None, None, 1), "interval_days must be null for recurrence_type 'once'"),
        ],
    )
    def test_validate_unrelated_field_raises(self, args, message) -> None:
        with pytest.raises(RecurrenceError, match=message):
            validate_recurrence(*args)

    def test_due_only_on_start_date(self) -> None:
        rule = once(d("2024-01-10"))
        assert is_due(rule, d("2024-01-10")) is True
        assert is_due(rule, d("2024-01-09")) is False
        assert is_due(rule, d("2024-01-11")) is False

    def test_exactly_one_due_day_in_long_range(self) -> None:
        rule = once(d("2024-03-15"))
        due = [
            day
            for day in (d("2024-01-01") + timedelta(days=n) for n in range(400))
            if is_due(rule, day)
        ]
        assert due == [d("2024-03-15")]

    def test_month_boundary(self) -> None:
        rule = once(d("2024-01-31"))
        assert is_due(rule, d("2024-01-31")) is True
        assert is_due(rule, d("2024-02-01")) is False
        assert is_due(rule, d("2024-01-30")) is False

    def test_year_boundary(self) -> None:
        rule = once(d("2024-12-31"))
        assert is_due(rule, d("2024-12-31")) is True
        assert is_due(rule, d("2025-01-01")) is False
        assert is_due(rule, d("2024-12-30")) is False

    def test_interval_days_none_does_not_crash(self) -> None:
        # The once branch runs before the interval branch, so a None interval_days is
        # never used as a divisor (no ZeroDivisionError / TypeError on any day).
        rule = Rule(d("2024-01-10"), "once", interval_days=None)
        for n in range(-3, 40):
            is_due(rule, d("2024-01-10") + timedelta(days=n))
        assert scheduled_dates(rule, d("2024-01-01"), d("2024-02-28")) == [d("2024-01-10")]
        assert current_streak(rule, d("2024-02-28"), {d("2024-01-10")}) == 1
        assert longest_streak(rule, {d("2024-01-10")}) == 1

    def test_scheduled_dates_is_start_only(self) -> None:
        rule = once(d("2024-06-01"))
        assert scheduled_dates(rule, d("2024-01-01"), d("2024-12-31")) == [d("2024-06-01")]
        assert scheduled_dates(rule, d("2024-06-02"), d("2024-12-31")) == []

    def test_streak_completed_is_one(self) -> None:
        rule = once(d("2024-01-10"))
        assert current_streak(rule, d("2024-01-10"), {d("2024-01-10")}) == 1
        assert longest_streak(rule, {d("2024-01-10")}) == 1

    def test_streak_not_completed_is_zero(self) -> None:
        rule = once(d("2024-01-10"))
        assert current_streak(rule, d("2024-01-10"), set()) == 0
        assert longest_streak(rule, set()) == 0

    def test_streak_reference_after_start(self) -> None:
        rule = once(d("2024-01-10"))
        assert current_streak(rule, d("2024-01-15"), {d("2024-01-10")}) == 1
        assert current_streak(rule, d("2024-01-15"), set()) == 0

    def test_streak_ignores_logs_after_start(self) -> None:
        rule = once(d("2024-01-10"))
        logs = {d("2024-01-10"), d("2024-01-12")}
        assert current_streak(rule, d("2024-01-12"), logs) == 1
        assert longest_streak(rule, logs) == 1
