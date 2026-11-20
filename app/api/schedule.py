"""Schedule router - API endpoints for recurring time blocks.

Times are 'HH:MM'. Days in the URL are plain calendar dates (YYYY-MM-DD) between
2000-01-01 and 2100-12-31; the server never computes "today" itself.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories.schedule import ScheduleRepository
from app.schemas.schedule import ScheduleCreate, ScheduleRead, ScheduleUpdate
from app.services.recurrence import MAX_DAY, MIN_DAY
from app.services.schedule import (
    ScheduleNotFoundError,
    ScheduleService,
    ScheduleValidationError,
)

router = APIRouter(prefix="/schedule", tags=["schedule"])

DayPath = Annotated[
    date,
    Path(
        ge=MIN_DAY,
        le=MAX_DAY,
        description="Plain calendar date YYYY-MM-DD (2000-01-01..2100-12-31)",
    ),
]


def get_schedule_service(db: Annotated[Session, Depends(get_db)]) -> ScheduleService:
    """Dependency to get ScheduleService with repository."""
    return ScheduleService(ScheduleRepository(db))


ServiceDep = Annotated[ScheduleService, Depends(get_schedule_service)]


def _not_found(e: ScheduleNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post(
    "", response_model=ScheduleRead, status_code=status.HTTP_201_CREATED, summary="Create a block"
)
def create_block(data: ScheduleCreate, service: ServiceDep) -> ScheduleRead:
    return service.create_block(data)


@router.get("", response_model=list[ScheduleRead], summary="List blocks (not deleted)")
def list_blocks(service: ServiceDep) -> list[ScheduleRead]:
    return service.list_blocks()


# Declared before /{block_id} so the literal "day" segment is matched first.
@router.get("/day/{day}", response_model=list[ScheduleRead], summary="Blocks scheduled on a day")
def get_blocks_for_day(day: DayPath, service: ServiceDep) -> list[ScheduleRead]:
    return service.get_blocks_for_day(day)


@router.get("/{block_id}", response_model=ScheduleRead, summary="Get a block")
def get_block(block_id: int, service: ServiceDep) -> ScheduleRead:
    try:
        return service.get_block(block_id)
    except ScheduleNotFoundError as e:
        raise _not_found(e) from e


@router.patch("/{block_id}", response_model=ScheduleRead, summary="Partially update a block")
def update_block(block_id: int, data: ScheduleUpdate, service: ServiceDep) -> ScheduleRead:
    try:
        return service.update_block(block_id, data)
    except ScheduleNotFoundError as e:
        raise _not_found(e) from e
    except ScheduleValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)
        ) from e


@router.delete(
    "/{block_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Soft delete a block"
)
def delete_block(block_id: int, service: ServiceDep) -> None:
    try:
        service.delete_block(block_id)
    except ScheduleNotFoundError as e:
        raise _not_found(e) from e
