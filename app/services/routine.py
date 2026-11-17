"""Routine service - business logic for routines, completion logs and streaks."""

from datetime import date

from sqlalchemy.exc import IntegrityError

from app.models.routine import Routine
from app.repositories.routine import RoutineRepository
from app.schemas.routine import (
    RoutineCreate,
    RoutineDayItem,
    RoutineLogRead,
    RoutineRead,
    RoutineUpdate,
)
from app.services.recurrence import (
    RecurrenceError,
    current_streak,
    is_due,
    longest_streak,
    validate_recurrence,
)

_RECURRENCE_FIELDS = ("recurrence_type", "days_of_week", "day_of_month", "interval_days")


class RoutineNotFoundError(Exception):
    """Routine does not exist or is soft-deleted."""


class RoutineValidationError(Exception):
    """The merged routine state is invalid (e.g. inconsistent recurrence)."""


class RoutineNotScheduledError(Exception):
    """The routine is not scheduled on the requested day."""


class RoutineService:
    """Routine operations. Never reads the clock; every day comes from the caller."""

    def __init__(self, repository: RoutineRepository):
        self.repository = repository

    def _get_active(self, routine_id: int) -> Routine:
        routine = self.repository.get_by_id(routine_id)
        if routine is None:
            raise RoutineNotFoundError(f"Routine with id {routine_id} not found")
        return routine

    def create_routine(self, data: RoutineCreate) -> RoutineRead:
        return RoutineRead.model_validate(self.repository.create(data))

    def list_routines(self) -> list[RoutineRead]:
        return [RoutineRead.model_validate(r) for r in self.repository.get_all()]

    def get_routine(self, routine_id: int) -> RoutineRead:
        return RoutineRead.model_validate(self._get_active(routine_id))

    def update_routine(self, routine_id: int, update: RoutineUpdate) -> RoutineRead:
        """Apply only sent fields; validate recurrence on the merged (stored + sent) state."""
        routine = self._get_active(routine_id)
        changes = update.changes()
        merged = {name: changes.get(name, getattr(routine, name)) for name in _RECURRENCE_FIELDS}
        try:
            validate_recurrence(**merged)
        except RecurrenceError as e:
            raise RoutineValidationError(str(e)) from e
        return RoutineRead.model_validate(self.repository.update(routine, changes))

    def delete_routine(self, routine_id: int) -> None:
        """Soft delete; the routine row and its logs are kept."""
        self.repository.soft_delete(self._get_active(routine_id))

    def get_routines_for_day(self, day: date) -> list[RoutineDayItem]:
        """Routines scheduled on ``day`` with completion and streaks (reference = day).

        Logs for all scheduled routines are loaded in one query; streaks are computed in memory.
        """
        due = [r for r in self.repository.get_started_by(day) if is_due(r, day)]
        log_dates = self.repository.get_log_dates([r.id for r in due])
        items = []
        for routine in due:
            done = log_dates.get(routine.id, set())
            items.append(
                RoutineDayItem(
                    **RoutineRead.model_validate(routine).model_dump(),
                    completed=day in done,
                    current_streak=current_streak(routine, day, done),
                    longest_streak=longest_streak(routine, done),
                )
            )
        return items

    def mark_done(self, routine_id: int, day: date) -> tuple[RoutineLogRead, bool]:
        """Idempotently record completion. Returns (log, created).

        A concurrent insert of the same (routine_id, day) surfaces as IntegrityError on
        the unique constraint; the existing row is then returned with created=False.
        """
        routine = self._get_active(routine_id)
        if not is_due(routine, day):
            raise RoutineNotScheduledError(
                f"Routine {routine_id} is not scheduled on {day.isoformat()}"
            )
        existing = self.repository.get_log(routine_id, day)
        if existing is not None:
            return RoutineLogRead.model_validate(existing), False
        try:
            log = self.repository.create_log(routine_id, day)
        except IntegrityError:
            self.repository.rollback()
            existing = self.repository.get_log(routine_id, day)
            if existing is None:  # not a duplicate -> a real integrity problem
                raise
            return RoutineLogRead.model_validate(existing), False
        return RoutineLogRead.model_validate(log), True

    def undo(self, routine_id: int, day: date) -> None:
        """Remove the completion for ``day`` if present (idempotent)."""
        self._get_active(routine_id)
        self.repository.delete_log(routine_id, day)
