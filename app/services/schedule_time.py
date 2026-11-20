"""Pure time-of-day helpers for schedule blocks.

Times travel as strict two-digit 'HH:MM' strings (00:00..23:59). Seconds, '24:00'
and short forms like '7:5' are rejected. No clock is read here.
"""

import re
from datetime import time
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer

TIME_FORMAT_MESSAGE = "time must be HH:MM (00:00-23:59)"
TIME_RANGE_MESSAGE = "end_time must be after start_time"

_HHMM = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")  # [0-9], not \d: \d matches non-ASCII digits


class ScheduleTimeError(ValueError):
    """end_time is not after start_time."""


def parse_hhmm(value: Any) -> time:
    """Parse a strict 'HH:MM' string; anything else raises ValueError(TIME_FORMAT_MESSAGE)."""
    if not isinstance(value, str) or not _HHMM.fullmatch(value):
        raise ValueError(TIME_FORMAT_MESSAGE)
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def format_hhmm(value: time) -> str:
    """Format a time as 'HH:MM'."""
    return value.strftime("%H:%M")


def validate_time_range(start: time, end: time) -> None:
    """Raise ScheduleTimeError unless end is strictly after start."""
    if end <= start:
        raise ScheduleTimeError(TIME_RANGE_MESSAGE)


# Pydantic input type: accepts only 'HH:MM'. JSON output is 'HH:MM'; python-mode dumps keep
# datetime.time so the ORM receives a real time.
HHMM = Annotated[
    time,
    BeforeValidator(parse_hhmm),
    PlainSerializer(format_hhmm, return_type=str, when_used="json"),
]
# Pydantic output type: built from the ORM's datetime.time, serialized as 'HH:MM' in JSON.
HHMMOut = Annotated[time, PlainSerializer(format_hhmm, return_type=str, when_used="json")]
