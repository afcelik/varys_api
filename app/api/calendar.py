"""Calendar router - per-day counts for a month.

Read-only; the client chooses the month, the server never computes "today".
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories.calendar import CalendarRepository
from app.repositories.routine import RoutineRepository
from app.repositories.schedule import ScheduleRepository
from app.schemas.calendar import CalendarMonthRead
from app.services.calendar import CalendarService
from app.services.recurrence import MAX_DAY, MIN_DAY

router = APIRouter(prefix="/calendar", tags=["calendar"])


def get_calendar_service(db: Annotated[Session, Depends(get_db)]) -> CalendarService:
    """Dependency: CalendarService with repositories on one session."""
    return CalendarService(CalendarRepository(db), RoutineRepository(db), ScheduleRepository(db))


@router.get(
    "/month",
    response_model=CalendarMonthRead,
    summary="Per-day task, due, routine and schedule counts for a month",
)
def get_month(
    year: Annotated[int, Query(ge=MIN_DAY.year, le=MAX_DAY.year)],
    month: Annotated[int, Query(ge=1, le=12)],
    service: Annotated[CalendarService, Depends(get_calendar_service)],
) -> CalendarMonthRead:
    return service.get_month(year, month)
