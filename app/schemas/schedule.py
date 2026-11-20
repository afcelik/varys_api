"""Pydantic schemas for the schedule (recurring time blocks) API."""

from datetime import date, datetime
from typing import Annotated, Any, ClassVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.models.routine import RecurrenceType
from app.schemas.routine import RoutineDay, Weekday
from app.services.recurrence import normalize_days_of_week, validate_recurrence
from app.services.schedule_time import HHMM, HHMMOut, validate_time_range

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class ScheduleCreate(BaseModel):
    """Create a block. Times are 'HH:MM'; end_time > start_time; recurrence as for routines."""

    title: Title
    description: str | None = Field(None, max_length=2000)
    start_time: HHMM
    end_time: HHMM
    recurrence_type: RecurrenceType
    days_of_week: list[Weekday] | None = None
    day_of_month: int | None = Field(None, ge=1, le=31)
    interval_days: int | None = Field(None, ge=1)
    start_date: RoutineDay

    @field_validator("days_of_week")
    @classmethod
    def _normalize_days(cls, v: list[int] | None) -> list[int] | None:
        return normalize_days_of_week(v)

    @model_validator(mode="after")
    def _check_state(self) -> "ScheduleCreate":
        validate_time_range(self.start_time, self.end_time)
        validate_recurrence(
            self.recurrence_type, self.days_of_week, self.day_of_month, self.interval_days
        )
        return self


class ScheduleUpdate(BaseModel):
    """Partial update.

    Field status is explicit via ``model_fields_set``:
    - not sent  -> unchanged
    - sent null -> cleared (description and recurrence detail fields only;
      title/start_time/end_time/recurrence_type -> 422)
    - sent value -> set
    start_date cannot be changed (422 if sent). Time range and recurrence consistency are
    checked by the service on the merged (stored + sent) state.
    """

    NON_NULLABLE: ClassVar[tuple[str, ...]] = (
        "title",
        "start_time",
        "end_time",
        "recurrence_type",
    )

    title: Title | None = None
    description: str | None = Field(None, max_length=2000)
    start_time: HHMM | None = None
    end_time: HHMM | None = None
    recurrence_type: RecurrenceType | None = None
    days_of_week: list[Weekday] | None = None
    day_of_month: int | None = Field(None, ge=1, le=31)
    interval_days: int | None = Field(None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _reject_start_date(cls, data: Any) -> Any:
        if isinstance(data, dict) and "start_date" in data:
            raise ValueError("start_date cannot be changed")
        return data

    @field_validator("days_of_week")
    @classmethod
    def _normalize_days(cls, v: list[int] | None) -> list[int] | None:
        return normalize_days_of_week(v)

    @model_validator(mode="after")
    def _reject_null_for_required(self) -> "ScheduleUpdate":
        for name in self.NON_NULLABLE:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self

    def changes(self) -> dict[str, Any]:
        """Only the fields the client actually sent (including explicit nulls)."""
        return {name: getattr(self, name) for name in self.model_fields_set}


class ScheduleRead(BaseModel):
    """A schedule block; times are 'HH:MM' in JSON."""

    id: int
    title: str
    description: str | None
    start_time: HHMMOut
    end_time: HHMMOut
    recurrence_type: RecurrenceType
    days_of_week: list[int] | None
    day_of_month: int | None
    interval_days: int | None
    start_date: date
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
