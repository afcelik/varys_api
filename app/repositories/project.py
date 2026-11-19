"""Project repository - data access layer for projects and their events."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.project import Project, ProjectEvent, ProjectEventType, ProjectStatus
from app.models.task import Task, TaskEvent, TaskEventType, TaskStatus
from app.schemas.project import ProjectCreate


class ProjectRepository:
    """The only place that touches the DB for projects (commit/refresh)."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, data: ProjectCreate) -> Project:
        project = Project(**data.model_dump())
        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)
        return project

    def get_by_id(self, project_id: int) -> Project | None:
        """Active (not soft-deleted) project by id."""
        stmt = select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
        return self.db.scalars(stmt).first()

    def get_all(self, status: ProjectStatus | None = None) -> list[Project]:
        """Non-deleted projects, oldest first, optionally filtered by status."""
        stmt = select(Project).where(Project.deleted_at.is_(None))
        if status is not None:
            stmt = stmt.where(Project.status == status)
        return list(self.db.scalars(stmt.order_by(Project.id)).all())

    def count_active(self, exclude_id: int | None = None) -> int:
        """Number of non-deleted ACTIVE projects, optionally excluding one project."""
        stmt = select(func.count(Project.id)).where(
            Project.deleted_at.is_(None), Project.status == ProjectStatus.ACTIVE
        )
        if exclude_id is not None:
            stmt = stmt.where(Project.id != exclude_id)
        return self.db.scalar(stmt) or 0

    def progress_counts(self, project_ids: list[int]) -> dict[int, tuple[int, int]]:
        """(total, done) over non-deleted tasks per project, in a single grouped query.

        Projects without tasks are absent from the result.
        """
        if not project_ids:
            return {}
        # Compare against the enum member: tasks.status stores enum NAMES ("DONE"), not values.
        done = func.sum(case((Task.status == TaskStatus.DONE, 1), else_=0))
        stmt = (
            select(Task.project_id, func.count(Task.id), done)
            .where(Task.deleted_at.is_(None), Task.project_id.in_(project_ids))
            .group_by(Task.project_id)
        )
        return {pid: (total, int(done_n or 0)) for pid, total, done_n in self.db.execute(stmt)}

    def update(self, project: Project, changes: dict[str, Any]) -> Project:
        for name, value in changes.items():
            setattr(project, name, value)
        self.db.commit()
        self.db.refresh(project)
        return project

    def soft_delete_with_tasks(self, project: Project) -> None:
        """Soft delete the project and unlink its non-deleted tasks, in one commit.

        The FK's ON DELETE SET NULL only fires on a physical delete, so unlinking is done
        here explicitly; each unlinked task gets an EDITED event. Tasks are not deleted.
        """
        tasks = self.db.scalars(
            select(Task).where(Task.project_id == project.id, Task.deleted_at.is_(None))
        ).all()
        for task in tasks:
            task.project_id = None
            self.db.add(
                TaskEvent(
                    task_id=task.id,
                    event_type=TaskEventType.EDITED,
                    payload={
                        "fields_changed": ["project_id"],
                        "old_project_id": project.id,
                        "new_project_id": None,
                        "reason": "project_deleted",
                    },
                )
            )
        project.deleted_at = datetime.now(UTC)
        self.db.commit()

    def create_event(
        self, project_id: int, event_type: ProjectEventType, payload: dict | None = None
    ) -> ProjectEvent:
        event = ProjectEvent(project_id=project_id, event_type=event_type, payload=payload)
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def get_events(self, project_id: int) -> list[ProjectEvent]:
        stmt = (
            select(ProjectEvent)
            .where(ProjectEvent.project_id == project_id)
            .order_by(ProjectEvent.created_at, ProjectEvent.id)
        )
        return list(self.db.scalars(stmt).all())
