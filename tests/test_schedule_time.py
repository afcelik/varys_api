"""Unit tests for the pure schedule time helpers."""

from datetime import time

import pytest
from pydantic import BaseModel, ValidationError

from app.services.schedule_time import (
    HHMM,
    TIME_FORMAT_MESSAGE,
    TIME_RANGE_MESSAGE,
    ScheduleTimeError,
    format_hhmm,
    parse_hhmm,
    validate_time_range,
)


class _Model(BaseModel):
    at: HHMM


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("00:00", time(0, 0)), ("07:05", time(7, 5)), ("12:30", time(12, 30)), ("23:59", time(23, 59))],
)
def test_parse_valid(raw: str, expected: time) -> None:
    assert parse_hhmm(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "07:00:00",  # seconds
        "24:00",
        "7:5",
        "07:5",
        "7:05",
        "12:60",
        "",
        "7",
        " 07:00",
        "07:00 ",
        "07-00",
        "0\u0667:00",  # Arabic-Indic seven: Python's \d would accept it
        None,
        700,
        time(7, 0),
    ],
)
def test_parse_rejects_with_fixed_message(raw) -> None:
    with pytest.raises(ValueError) as exc:
        parse_hhmm(raw)
    assert str(exc.value) == TIME_FORMAT_MESSAGE


def test_format_is_two_digit_hhmm() -> None:
    assert format_hhmm(time(0, 0)) == "00:00"
    assert format_hhmm(time(7, 5)) == "07:05"
    assert format_hhmm(time(23, 59)) == "23:59"


def test_format_parse_round_trip_for_every_minute() -> None:
    for minute_of_day in range(24 * 60):
        t = time(minute_of_day // 60, minute_of_day % 60)
        assert parse_hhmm(format_hhmm(t)) == t


@pytest.mark.parametrize(
    ("start", "end"),
    [(time(8, 0), time(8, 0)), (time(9, 0), time(8, 59)), (time(23, 59), time(0, 0))],
)
def test_range_rejects_end_not_after_start(start: time, end: time) -> None:
    with pytest.raises(ScheduleTimeError) as exc:
        validate_time_range(start, end)
    assert str(exc.value) == TIME_RANGE_MESSAGE


def test_range_accepts_end_after_start() -> None:
    validate_time_range(time(0, 0), time(0, 1))
    validate_time_range(time(0, 0), time(23, 59))


def test_pydantic_type_parses_and_serializes_hhmm() -> None:
    m = _Model(at="07:05")
    assert m.at == time(7, 5)
    assert m.model_dump(mode="json") == {"at": "07:05"}


@pytest.mark.parametrize("raw", ["07:00:00", "24:00", "7:5", None])
def test_pydantic_type_rejects_bad_input(raw) -> None:
    with pytest.raises(ValidationError) as exc:
        _Model(at=raw)
    assert TIME_FORMAT_MESSAGE in str(exc.value)
