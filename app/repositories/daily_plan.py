"""DailyPlan repository - data access layer for daily plans."""

from datetime import date
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.daily_plan import DailyPlan
from app.models.task import Task


class DailyPlanRepository:
    """Repository for DailyPlan model - handles all database operations."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_date(self, plan_date: date) -> Optional[DailyPlan]:
        """Get a daily plan by date."""
        stmt = select(DailyPlan).where(DailyPlan.plan_date == plan_date)
        return self.db.scalars(stmt).first()

    def upsert(
        self,
        plan_date: date,
        main_focus_task_id: Optional[int],
        side_focus_task_id: Optional[int],
        say_no_text: Optional[str],
    ) -> DailyPlan:
        """Create or update a daily plan.

        All parameters are required - the service layer determines which to pass.
        """
        plan = self.get_by_date(plan_date)
        if plan is None:
            plan = DailyPlan(
                plan_date=plan_date,
                main_focus_task_id=main_focus_task_id,
                side_focus_task_id=side_focus_task_id,
                say_no_text=say_no_text,
            )
            self.db.add(plan)
        else:
            plan.main_focus_task_id = main_focus_task_id
            plan.side_focus_task_id = side_focus_task_id
            plan.say_no_text = say_no_text
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def get_tasks_by_planned_date(self, plan_date: date) -> list[Task]:
        """Get all non-deleted tasks with matching planned_date."""
        stmt = select(Task).where(
            func.date(Task.planned_date) == plan_date,
            Task.deleted_at.is_(None),
        )
        return list(self.db.scalars(stmt).all())