"""Pydantic schemas for DailyPlan API."""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.task import TaskStatus
from app.schemas.task import TaskRead


class DailyPlanBase(BaseModel):
    """Base schema with common fields."""

    plan_date: date = Field(..., description="Plan date")
    main_focus_task_id: Optional[int] = Field(None, description="Main focus task ID")
    side_focus_task_id: Optional[int] = Field(None, description="Side focus task ID")
    say_no_text: Optional[str] = Field(None, max_length=2000, description="What to say no to today")


class DailyPlanUpdate(BaseModel):
    """Schema for updating a daily plan (all fields optional)."""

    main_focus_task_id: Optional[int] = Field(None, description="Main focus task ID")
    side_focus_task_id: Optional[int] = Field(None, description="Side focus task ID")
    say_no_text: Optional[str] = Field(None, max_length=2000, description="What to say no to today")


class DailyPlanRead(DailyPlanBase):
    """Schema for reading a daily plan."""

    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DailyPlanResponse(BaseModel):
    """Complete response schema for GET /daily-plans/{plan_date}."""

    id: Optional[int] = None
    plan_date: date
    main_focus_task_id: Optional[int] = None
    side_focus_task_id: Optional[int] = None
    say_no_text: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    main_focus: Optional[TaskRead] = None
    side_focus: Optional[TaskRead] = None
    tasks: list[TaskRead] = []

    model_config = ConfigDict(from_attributes=True)