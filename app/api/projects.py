"""Project router - API endpoints for projects, the active limit and project events."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.project import ProjectStatus
from app.repositories.project import ProjectRepository
from app.schemas.project import (
    ProjectCreate,
    ProjectEventRead,
    ProjectLimits,
    ProjectRead,
    ProjectUpdate,
)
from app.services.project import (
    ActiveProjectLimitError,
    ProjectNotFoundError,
    ProjectService,
    ProjectValidationError,
)

router = APIRouter(prefix="/projects", tags=["projects"])

# Fixed, machine-readable detail; the client maps it to its own text and never shows it.
ACTIVE_LIMIT_DETAIL = "active_project_limit_reached"


def get_project_service(db: Annotated[Session, Depends(get_db)]) -> ProjectService:
    """Dependency to get ProjectService with repository."""
    return ProjectService(ProjectRepository(db))


ServiceDep = Annotated[ProjectService, Depends(get_project_service)]


def _not_found(e: ProjectNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


def _limit_reached() -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ACTIVE_LIMIT_DETAIL)


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED, summary="Create a project")
def create_project(data: ProjectCreate, service: ServiceDep) -> ProjectRead:
    try:
        return service.create_project(data)
    except ActiveProjectLimitError as e:
        raise _limit_reached() from e


@router.get("", response_model=list[ProjectRead], summary="List projects (not deleted), with progress")
def list_projects(
    service: ServiceDep,
    status_filter: Annotated[ProjectStatus | None, Query(alias="status")] = None,
) -> list[ProjectRead]:
    return service.list_projects(status_filter)


# Declared before /{project_id} so the literal "limits" segment is matched first.
@router.get("/limits", response_model=ProjectLimits, summary="Active project limit and usage")
def get_limits(service: ServiceDep) -> ProjectLimits:
    return service.get_limits()


@router.get("/{project_id}", response_model=ProjectRead, summary="Get a project")
def get_project(project_id: int, service: ServiceDep) -> ProjectRead:
    try:
        return service.get_project(project_id)
    except ProjectNotFoundError as e:
        raise _not_found(e) from e


@router.patch("/{project_id}", response_model=ProjectRead, summary="Partially update a project")
def update_project(project_id: int, data: ProjectUpdate, service: ServiceDep) -> ProjectRead:
    try:
        return service.update_project(project_id, data)
    except ProjectNotFoundError as e:
        raise _not_found(e) from e
    except ProjectValidationError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e
    except ActiveProjectLimitError as e:
        raise _limit_reached() from e


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft delete a project (its tasks are unlinked, not deleted)",
)
def delete_project(project_id: int, service: ServiceDep) -> None:
    try:
        service.delete_project(project_id)
    except ProjectNotFoundError as e:
        raise _not_found(e) from e


@router.get("/{project_id}/events", response_model=list[ProjectEventRead], summary="Project event log")
def get_project_events(project_id: int, service: ServiceDep) -> list[ProjectEventRead]:
    try:
        return service.get_project_events(project_id)
    except ProjectNotFoundError as e:
        raise _not_found(e) from e
