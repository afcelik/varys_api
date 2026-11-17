"""Pydantic schemas for Routine API."""

from datetime import date, datetime
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.routine import RecurrenceType
from app.services.recurrence import MAX_DAY, MIN_DAY, normalize_days_of_week, validate_recurrence

# Plain calendar date (YYYY-MM-DD), no time zone; bounded to keep input sane.
RoutineDay = Annotated[date, Field(ge=MIN_DAY, le=MAX_DAY)]
Weekday = Annotated[int, Field(ge=0, le=6, description="Monday=0 ... Sunday=6")]


class RoutineCreate(BaseModel):
    """Schema for creating a routine. Recurrence fields must match recurrence_type."""

    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
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
    def _check_recurrence(self) -> "RoutineCreate":
        validate_recurrence(
            self.recurrence_type, self.days_of_week, self.day_of_month, self.interval_days
        )
        return self


class RoutineUpdate(BaseModel):
    """Partial update.

    Field status is explicit via ``model_fields_set``:
    - not sent  -> unchanged
    - sent null -> cleared (only for nullable fields; title/recurrence_type/start_date -> 422)
    - sent value -> set
    Recurrence consistency is checked by the service on the merged state.
    """

    NON_NULLABLE: ClassVar[tuple[str, ...]] = ("title", "recurrence_type", "start_date")

    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    recurrence_type: RecurrenceType | None = None
    days_of_week: list[Weekday] | None = None
    day_of_month: int | None = Field(None, ge=1, le=31)
    interval_days: int | None = Field(None, ge=1)
    start_date: RoutineDay | None = None

    @field_validator("days_of_week")
    @classmethod
    def _normalize_days(cls, v: list[int] | None) -> list[int] | None:
        return normalize_days_of_week(v)

    @model_validator(mode="after")
    def _reject_null_for_required(self) -> "RoutineUpdate":
        for name in self.NON_NULLABLE:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self

    def changes(self) -> dict[str, Any]:
        """Only the fields the client actually sent (including explicit nulls)."""
        return {name: getattr(self, name) for name in self.model_fields_set}


class RoutineRead(BaseModel):
    """Schema for reading a routine."""

    id: int
    title: str
    description: str | None
    recurrence_type: RecurrenceType
    days_of_week: list[int] | None
    day_of_month: int | None
    interval_days: int | None
    start_date: date
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RoutineDayItem(RoutineRead):
    """A routine scheduled on the requested day, with its completion and streaks."""

    completed: bool
    current_streak: int
    longest_streak: int


class RoutineLogRead(BaseModel):
    """Schema for reading a routine completion log."""

    id: int
    routine_id: int
    log_date: date
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
