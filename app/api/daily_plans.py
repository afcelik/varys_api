"""DailyPlan router - API endpoints for daily plans."""

from datetime import date
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories.daily_plan import DailyPlanRepository
from app.repositories.task import TaskRepository
from app.schemas.daily_plan import DailyPlanResponse, DailyPlanUpdate
from app.services.daily_plan import DailyPlanService, SameFocusError, TaskNotFoundError

router = APIRouter(prefix="/daily-plans", tags=["daily-plans"])


def get_daily_plan_service(db: Session = Depends(get_db)) -> DailyPlanService:
    """Dependency to get DailyPlanService with repositories."""
    daily_plan_repo = DailyPlanRepository(db)
    task_repo = TaskRepository(db)
    return DailyPlanService(daily_plan_repo, task_repo)


@router.get(
    "/{plan_date}",
    response_model=DailyPlanResponse,
    summary="Get daily plan for a date",
    description="Returns the daily plan for the given date along with all tasks that have a matching planned_date. If no plan record exists, returns an empty plan (with fields set to null) rather than a 404; does not create a record.",
)
def get_daily_plan(
    plan_date: date,
    service: DailyPlanService = Depends(get_daily_plan_service),
) -> DailyPlanResponse:
    """Get the daily plan for a given date."""
    return service.get_plan(plan_date)


@router.put(
    "/{plan_date}",
    response_model=DailyPlanResponse,
    summary="Upsert daily plan",
    description="Creates or updates the daily plan for the given date. Only fields explicitly provided in the request are updated. Sending null for a focus field clears that focus.",
)
def upsert_daily_plan(
    plan_date: date,
    plan_update: DailyPlanUpdate,
    service: DailyPlanService = Depends(get_daily_plan_service),
) -> DailyPlanResponse:
    """Create or update a daily plan."""
    try:
        # Use model_fields_set to track which fields were explicitly provided
        provided_fields = plan_update.model_fields_set
        return service.upsert_plan(
            plan_date=plan_date,
            main_focus_task_id=plan_update.main_focus_task_id,
            side_focus_task_id=plan_update.side_focus_task_id,
            say_no_text=plan_update.say_no_text,
            main_focus_explicitly_set="main_focus_task_id" in provided_fields,
            side_focus_explicitly_set="side_focus_task_id" in provided_fields,
            say_no_explicitly_set="say_no_text" in provided_fields,
        )
    except TaskNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except SameFocusError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))