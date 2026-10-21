"""Task router - API endpoints for tasks."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories.task import TaskRepository
from app.schemas.task import TaskCreate, TaskRead, TaskUpdate
from app.services.task import TaskNotFoundError, TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])


def get_task_service(db: Session = Depends(get_db)) -> TaskService:
    """Dependency to get TaskService with repository."""
    repository = TaskRepository(db)
    return TaskService(repository)


@router.post(
    "",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new task",
)
def create_task(
    task_create: TaskCreate,
    service: TaskService = Depends(get_task_service),
) -> TaskRead:
    """Create a new task."""
    return service.create_task(task_create)


@router.get(
    "",
    response_model=list[TaskRead],
    summary="Get all tasks",
)
def get_tasks(
    status: str | None = Query(None, description="Filter by status (todo, in_progress, done)"),
    service: TaskService = Depends(get_task_service),
) -> list[TaskRead]:
    """Get all tasks, optionally filtered by status."""
    return service.get_tasks(status)


@router.get(
    "/{task_id}",
    response_model=TaskRead,
    summary="Get a task by ID",
)
def get_task(
    task_id: int,
    service: TaskService = Depends(get_task_service),
) -> TaskRead:
    """Get a task by its ID."""
    try:
        return service.get_task(task_id)
    except TaskNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.patch(
    "/{task_id}",
    response_model=TaskRead,
    summary="Update a task",
)
def update_task(
    task_id: int,
    task_update: TaskUpdate,
    service: TaskService = Depends(get_task_service),
) -> TaskRead:
    """Update an existing task."""
    try:
        return service.update_task(task_id, task_update)
    except TaskNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a task",
)
def delete_task(
    task_id: int,
    service: TaskService = Depends(get_task_service),
) -> None:
    """Delete a task by its ID."""
    try:
        service.delete_task(task_id)
    except TaskNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))