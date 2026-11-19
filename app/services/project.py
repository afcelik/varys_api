"""Project service - business logic for projects, the active limit, progress and events."""

from app.core.config import settings
from app.models.project import Project, ProjectEventType, ProjectStatus
from app.repositories.project import ProjectRepository
from app.schemas.project import (
    ProjectCreate,
    ProjectEventRead,
    ProjectLimits,
    ProjectRead,
    ProjectUpdate,
    dates_consistent,
)

# Fields whose change produces an EDITED event (status has its own event).
_EDIT_FIELDS = ("title", "description", "start_date", "target_date")


class ProjectNotFoundError(Exception):
    """Project does not exist or is soft-deleted."""


class ProjectValidationError(Exception):
    """The merged project state is invalid (e.g. target_date before start_date)."""


class ActiveProjectLimitError(Exception):
    """Activating the project would exceed MAX_ACTIVE_PROJECTS."""


def progress_percent(total: int, done: int) -> int | None:
    """Share of done tasks in percent; None when there are no tasks.

    Deliberately uses Python's round(), i.e. banker's rounding (round half to even):
    1/3 -> 33, 2/3 -> 67, 1/8 (12.5) -> 12.
    """
    if total == 0:
        return None
    return round(done * 100 / total)


class ProjectService:
    """Project operations.

    The active limit is read from settings at call time. The count-then-write check is
    not race-proof; that is accepted (single user, SQLite).
    """

    def __init__(self, repository: ProjectRepository):
        self.repository = repository

    @staticmethod
    def _max_active() -> int:
        return settings.MAX_ACTIVE_PROJECTS

    def _get_active(self, project_id: int) -> Project:
        project = self.repository.get_by_id(project_id)
        if project is None:
            raise ProjectNotFoundError(f"Project with id {project_id} not found")
        return project

    def _ensure_can_activate(self, exclude_id: int | None = None) -> None:
        if self.repository.count_active(exclude_id=exclude_id) >= self._max_active():
            raise ActiveProjectLimitError("active_project_limit_reached")

    def _read(self, project: Project, counts: dict[int, tuple[int, int]]) -> ProjectRead:
        total, done = counts.get(project.id, (0, 0))
        return ProjectRead(
            id=project.id,
            title=project.title,
            description=project.description,
            status=project.status,
            start_date=project.start_date,
            target_date=project.target_date,
            created_at=project.created_at,
            updated_at=project.updated_at,
            total_tasks=total,
            done_tasks=done,
            progress_percent=progress_percent(total, done),
        )

    def _read_one(self, project: Project) -> ProjectRead:
        return self._read(project, self.repository.progress_counts([project.id]))

    def create_project(self, data: ProjectCreate) -> ProjectRead:
        if data.status == ProjectStatus.ACTIVE:
            self._ensure_can_activate()
        project = self.repository.create(data)
        self.repository.create_event(
            project.id,
            ProjectEventType.CREATED,
            {"title": project.title, "status": project.status.value},
        )
        return self._read(project, {})

    def list_projects(self, status: ProjectStatus | None = None) -> list[ProjectRead]:
        """Non-deleted projects; progress for all of them comes from one grouped query."""
        projects = self.repository.get_all(status)
        counts = self.repository.progress_counts([p.id for p in projects])
        return [self._read(p, counts) for p in projects]

    def get_project(self, project_id: int) -> ProjectRead:
        return self._read_one(self._get_active(project_id))

    def get_limits(self) -> ProjectLimits:
        return ProjectLimits(
            max_active=self._max_active(), active_count=self.repository.count_active()
        )

    def update_project(self, project_id: int, update: ProjectUpdate) -> ProjectRead:
        """Apply only sent fields; events reflect real value changes only."""
        project = self._get_active(project_id)
        changes = update.changes()

        start = changes.get("start_date", project.start_date)
        target = changes.get("target_date", project.target_date)
        if not dates_consistent(start, target):
            raise ProjectValidationError("target_date cannot be before start_date")

        old = {name: getattr(project, name) for name in changes}
        changed = [name for name, value in changes.items() if value != old[name]]

        # Only a real transition into ACTIVE is checked; re-sending "active" is a no-op.
        if "status" in changed and changes["status"] == ProjectStatus.ACTIVE:
            self._ensure_can_activate(exclude_id=project.id)

        project = self.repository.update(project, changes)

        if "status" in changed:
            self.repository.create_event(
                project.id,
                ProjectEventType.STATUS_CHANGED,
                {"old_status": old["status"].value, "new_status": changes["status"].value},
            )
        edited = [name for name in changed if name in _EDIT_FIELDS]
        if edited:
            self.repository.create_event(
                project.id, ProjectEventType.EDITED, {"fields_changed": edited}
            )
        return self._read_one(project)

    def delete_project(self, project_id: int) -> None:
        """Soft delete; linked tasks are unlinked (not deleted) with an EDITED task event."""
        self.repository.soft_delete_with_tasks(self._get_active(project_id))

    def get_project_events(self, project_id: int) -> list[ProjectEventRead]:
        self._get_active(project_id)
        return [ProjectEventRead.model_validate(e) for e in self.repository.get_events(project_id)]
