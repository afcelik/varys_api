"""Pydantic schemas for Task API."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.task import TaskStatus, TaskEventType


class TaskBase(BaseModel):
    """Base schema with common fields."""

    title: str = Field(..., min_length=1, max_length=255, description="Task title")
    description: Optional[str] = Field(None, max_length=2000, description="Task description")
    status: Optional[str] = Field(None, description="Task status")
    due_date: Optional[datetime] = Field(None, description="Task due date")
    planned_date: Optional[datetime] = Field(None, description="Task planned date")

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        """Validate that status is a valid TaskStatus value."""
        if v is not None:
            valid_statuses = [s.value for s in TaskStatus]
            if v not in valid_statuses:
                raise ValueError(f"Invalid status. Must be one of: {', '.join(valid_statuses)}")
        return v


class TaskCreate(TaskBase):
    """Schema for creating a new task."""

    pass


class TaskUpdate(BaseModel):
    """Schema for updating an existing task (all fields optional)."""

    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    status: Optional[str] = Field(None)
    due_date: Optional[datetime] = Field(None)
    planned_date: Optional[datetime] = Field(None)

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        """Validate that status is a valid TaskStatus value."""
        if v is not None:
            valid_statuses = [s.value for s in TaskStatus]
            if v not in valid_statuses:
                raise ValueError(f"Invalid status. Must be one of: {', '.join(valid_statuses)}")
        return v


class TaskRead(TaskBase):
    """Schema for reading a task."""

    id: int
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class TaskEventRead(BaseModel):
    """Schema for reading a task event."""

    id: int
    task_id: int
    event_type: str
    payload: Optional[dict[str, Any]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)