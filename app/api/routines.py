"""Routine router - API endpoints for routines and their completion logs.

Days in the URL are plain calendar dates (YYYY-MM-DD) between 2000-01-01 and
2100-12-31; the server never computes "today" itself.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories.routine import RoutineRepository
from app.schemas.routine import (
    RoutineCreate,
    RoutineDayItem,
    RoutineLogRead,
    RoutineRead,
    RoutineUpdate,
)
from app.services.recurrence import MAX_DAY, MIN_DAY
from app.services.routine import (
    RoutineNotFoundError,
    RoutineNotScheduledError,
    RoutineService,
    RoutineValidationError,
)

router = APIRouter(prefix="/routines", tags=["routines"])

DayPath = Annotated[
    date,
    Path(ge=MIN_DAY, le=MAX_DAY, description="Plain calendar date YYYY-MM-DD (2000-01-01..2100-12-31)"),
]


def get_routine_service(db: Annotated[Session, Depends(get_db)]) -> RoutineService:
    """Dependency to get RoutineService with repository."""
    return RoutineService(RoutineRepository(db))


ServiceDep = Annotated[RoutineService, Depends(get_routine_service)]


def _not_found(e: RoutineNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


def _unprocessable(e: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))


@router.post("", response_model=RoutineRead, status_code=status.HTTP_201_CREATED, summary="Create a routine")
def create_routine(
    data: RoutineCreate,
    service: ServiceDep,
) -> RoutineRead:
    return service.create_routine(data)


@router.get("", response_model=list[RoutineRead], summary="List routines (not deleted)")
def list_routines(service: ServiceDep) -> list[RoutineRead]:
    return service.list_routines()


# Declared before /{routine_id} so the literal "day" segment is matched first.
@router.get(
    "/day/{day}",
    response_model=list[RoutineDayItem],
    summary="Routines scheduled on a day, with completion and streaks",
)
def get_routines_for_day(
    day: DayPath,
    service: ServiceDep,
) -> list[RoutineDayItem]:
    return service.get_routines_for_day(day)


@router.get("/{routine_id}", response_model=RoutineRead, summary="Get a routine")
def get_routine(
    routine_id: int,
    service: ServiceDep,
) -> RoutineRead:
    try:
        return service.get_routine(routine_id)
    except RoutineNotFoundError as e:
        raise _not_found(e) from e


@router.patch("/{routine_id}", response_model=RoutineRead, summary="Partially update a routine")
def update_routine(
    routine_id: int,
    data: RoutineUpdate,
    service: ServiceDep,
) -> RoutineRead:
    try:
        return service.update_routine(routine_id, data)
    except RoutineNotFoundError as e:
        raise _not_found(e) from e
    except RoutineValidationError as e:
        raise _unprocessable(e) from e


@router.delete("/{routine_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Soft delete a routine")
def delete_routine(
    routine_id: int,
    service: ServiceDep,
) -> None:
    try:
        service.delete_routine(routine_id)
    except RoutineNotFoundError as e:
        raise _not_found(e) from e


@router.put(
    "/{routine_id}/logs/{day}",
    response_model=RoutineLogRead,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "Already completed (idempotent)", "model": RoutineLogRead}},
    summary="Mark a routine completed on a day",
)
def mark_done(
    routine_id: int,
    day: DayPath,
    response: Response,
    service: ServiceDep,
) -> RoutineLogRead:
    try:
        log, created = service.mark_done(routine_id, day)
    except RoutineNotFoundError as e:
        raise _not_found(e) from e
    except RoutineNotScheduledError as e:
        raise _unprocessable(e) from e
    if not created:
        response.status_code = status.HTTP_200_OK
    return log


@router.delete(
    "/{routine_id}/logs/{day}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Undo a completion (idempotent)",
)
def undo(
    routine_id: int,
    day: DayPath,
    service: ServiceDep,
) -> None:
    try:
        service.undo(routine_id, day)
    except RoutineNotFoundError as e:
        raise _not_found(e) from e
