"""Pydantic schemas for Project API."""

from datetime import date, datetime
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.project import ProjectEventType, ProjectStatus
from app.services.recurrence import MAX_DAY, MIN_DAY

# Plain calendar date (YYYY-MM-DD), no time zone; same bounds as routine days.
ProjectDay = Annotated[date, Field(ge=MIN_DAY, le=MAX_DAY)]


def dates_consistent(start_date: date | None, target_date: date | None) -> bool:
    """A target date may not be before the start date (either may be absent)."""
    return start_date is None or target_date is None or target_date >= start_date


class ProjectCreate(BaseModel):
    """Schema for creating a project."""

    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    status: ProjectStatus = ProjectStatus.ACTIVE
    start_date: ProjectDay | None = None
    target_date: ProjectDay | None = None

    @model_validator(mode="after")
    def _check_dates(self) -> "ProjectCreate":
        if not dates_consistent(self.start_date, self.target_date):
            raise ValueError("target_date cannot be before start_date")
        return self


class ProjectUpdate(BaseModel):
    """Partial update.

    Field status is explicit via ``model_fields_set``:
    - not sent  -> unchanged
    - sent null -> cleared (description/start_date/target_date; title/status -> 422)
    - sent value -> set
    Date consistency is checked by the service on the merged (stored + sent) state.
    """

    NON_NULLABLE: ClassVar[tuple[str, ...]] = ("title", "status")

    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    status: ProjectStatus | None = None
    start_date: ProjectDay | None = None
    target_date: ProjectDay | None = None

    @model_validator(mode="after")
    def _reject_null_for_required(self) -> "ProjectUpdate":
        for name in self.NON_NULLABLE:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self

    def changes(self) -> dict[str, Any]:
        """Only the fields the client actually sent (including explicit nulls), in field order."""
        sent = self.model_fields_set
        return {name: getattr(self, name) for name in type(self).model_fields if name in sent}


class ProjectRead(BaseModel):
    """Schema for reading a project, with progress over its non-deleted tasks."""

    id: int
    title: str
    description: str | None
    status: ProjectStatus
    start_date: date | None
    target_date: date | None
    created_at: datetime
    updated_at: datetime
    total_tasks: int
    done_tasks: int
    progress_percent: int | None  # None when the project has no tasks


class ProjectLimits(BaseModel):
    """Active project limit and current usage."""

    max_active: int
    active_count: int


class ProjectEventRead(BaseModel):
    """Schema for reading a project event."""

    id: int
    project_id: int
    event_type: ProjectEventType
    payload: dict[str, Any] | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
