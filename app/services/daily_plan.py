"""DailyPlan service - business logic layer for daily plans."""

from datetime import date
from typing import Optional

from app.models.task import Task
from app.repositories.daily_plan import DailyPlanRepository
from app.repositories.task import TaskRepository
from app.schemas.daily_plan import DailyPlanResponse
from app.schemas.task import TaskRead


class DailyPlanError(Exception):
    """Base exception for daily plan errors."""
    pass


class TaskNotFoundError(DailyPlanError):
    """Raised when a task is not found."""
    pass


class SameFocusError(DailyPlanError):
    """Raised when main and side focus are the same task."""
    pass


class DailyPlanService:
    """Service for DailyPlan operations - contains business logic."""

    def __init__(self, daily_plan_repo: DailyPlanRepository, task_repo: TaskRepository):
        self.daily_plan_repo = daily_plan_repo
        self.task_repo = task_repo

    def get_plan(self, plan_date: date) -> DailyPlanResponse:
        """Get the daily plan for a date with tasks for that date.

        Returns an empty plan if no record exists, never 404.
        Does not create a record.
        """
        plan = self.daily_plan_repo.get_by_date(plan_date)
        tasks = self.daily_plan_repo.get_tasks_by_planned_date(plan_date)

        # Build response
        main_focus = None
        side_focus = None

        if plan:
            # Check if main_focus task exists and is not deleted
            if plan.main_focus_task_id:
                main_focus_task = self.task_repo.get_by_id(plan.main_focus_task_id)
                if main_focus_task:
                    main_focus = TaskRead.model_validate(main_focus_task)
                # If deleted or not found, main_focus stays None

            # Check if side_focus task exists and is not deleted
            if plan.side_focus_task_id:
                side_focus_task = self.task_repo.get_by_id(plan.side_focus_task_id)
                if side_focus_task:
                    side_focus = TaskRead.model_validate(side_focus_task)
                # If deleted or not found, side_focus stays None

        return DailyPlanResponse(
            id=plan.id if plan else None,
            plan_date=plan_date,
            main_focus_task_id=plan.main_focus_task_id if plan else None,
            side_focus_task_id=plan.side_focus_task_id if plan else None,
            say_no_text=plan.say_no_text if plan else None,
            created_at=plan.created_at if plan else None,
            updated_at=plan.updated_at if plan else None,
            main_focus=main_focus,
            side_focus=side_focus,
            tasks=[TaskRead.model_validate(t) for t in tasks],
        )

    def _validate_focus_task(self, task_id: Optional[int]) -> Optional[Task]:
        """Validate that a focus task exists and is not deleted.

        Returns the task if valid, None if task_id is None.
        Raises TaskNotFoundError if task doesn't exist or is deleted.
        """
        if task_id is None:
            return None
        task = self.task_repo.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError(f"Task with id {task_id} not found")
        return task

    def upsert_plan(
        self,
        plan_date: date,
        main_focus_task_id: Optional[int],
        side_focus_task_id: Optional[int],
        say_no_text: Optional[str],
        # Track which fields were explicitly set (including null)
        main_focus_explicitly_set: bool = False,
        side_focus_explicitly_set: bool = False,
        say_no_explicitly_set: bool = False,
    ) -> DailyPlanResponse:
        """Upsert a daily plan with validation.

        Args:
            plan_date: The date for the plan
            main_focus_task_id: Main focus task ID (None to clear)
            side_focus_task_id: Side focus task ID (None to clear)
            say_no_text: Say no text (None to clear)
            main_focus_explicitly_set: Whether main_focus_task_id was explicitly provided
            side_focus_explicitly_set: Whether side_focus_task_id was explicitly provided
            say_no_explicitly_set: Whether say_no_text was explicitly provided

        Returns:
            Updated DailyPlanResponse

        Raises:
            TaskNotFoundError: If a focus task doesn't exist or is deleted
            SameFocusError: If main and side focus are the same task
        """
        # Get existing plan to check current values
        existing_plan = self.daily_plan_repo.get_by_date(plan_date)

        # Determine the effective focus task IDs after update
        effective_main = (
            main_focus_task_id if main_focus_explicitly_set
            else (existing_plan.main_focus_task_id if existing_plan else None)
        )
        effective_side = (
            side_focus_task_id if side_focus_explicitly_set
            else (existing_plan.side_focus_task_id if existing_plan else None)
        )

        # Validate: main and side focus cannot be the same task
        if effective_main is not None and effective_main == effective_side:
            raise SameFocusError("Main focus and side focus cannot be the same task")

        # Validate focus tasks exist and are not deleted
        if main_focus_explicitly_set:
            self._validate_focus_task(main_focus_task_id)
        if side_focus_explicitly_set:
            self._validate_focus_task(side_focus_task_id)

        # Perform upsert - use existing values for fields not explicitly set
        plan = self.daily_plan_repo.upsert(
            plan_date=plan_date,
            main_focus_task_id=main_focus_task_id if main_focus_explicitly_set else (existing_plan.main_focus_task_id if existing_plan else None),
            side_focus_task_id=side_focus_task_id if side_focus_explicitly_set else (existing_plan.side_focus_task_id if existing_plan else None),
            say_no_text=say_no_text if say_no_explicitly_set else (existing_plan.say_no_text if existing_plan else None),
        )

        # Return updated plan with tasks
        return self.get_plan(plan_date)