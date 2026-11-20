"""Schedule service - business logic for recurring time blocks.

Recurrence (validation and is_due) is shared with routines via app.services.recurrence.
Never reads the clock; every day comes from the caller.
"""

from datetime import date

from app.models.schedule import ScheduleBlock
from app.repositories.schedule import ScheduleRepository
from app.schemas.schedule import ScheduleCreate, ScheduleRead, ScheduleUpdate
from app.services.recurrence import RecurrenceError, is_due, validate_recurrence
from app.services.schedule_time import ScheduleTimeError, validate_time_range

_RECURRENCE_FIELDS = ("recurrence_type", "days_of_week", "day_of_month", "interval_days")


class ScheduleNotFoundError(Exception):
    """Block does not exist or is soft-deleted."""


class ScheduleValidationError(Exception):
    """The merged block state is invalid (time range or recurrence)."""


class ScheduleService:
    """Schedule block operations."""

    def __init__(self, repository: ScheduleRepository):
        self.repository = repository

    def _get_active(self, block_id: int) -> ScheduleBlock:
        block = self.repository.get_by_id(block_id)
        if block is None:
            raise ScheduleNotFoundError(f"Schedule block with id {block_id} not found")
        return block

    def create_block(self, data: ScheduleCreate) -> ScheduleRead:
        return ScheduleRead.model_validate(self.repository.create(data))

    def list_blocks(self) -> list[ScheduleRead]:
        return [ScheduleRead.model_validate(b) for b in self.repository.get_all()]

    def get_block(self, block_id: int) -> ScheduleRead:
        return ScheduleRead.model_validate(self._get_active(block_id))

    def update_block(self, block_id: int, update: ScheduleUpdate) -> ScheduleRead:
        """Apply only sent fields; validate time range and recurrence on the merged state."""
        block = self._get_active(block_id)
        changes = update.changes()

        def merged(name: str):
            return changes.get(name, getattr(block, name))

        try:
            validate_time_range(merged("start_time"), merged("end_time"))
            validate_recurrence(**{name: merged(name) for name in _RECURRENCE_FIELDS})
        except (ScheduleTimeError, RecurrenceError) as e:
            raise ScheduleValidationError(str(e)) from e
        return ScheduleRead.model_validate(self.repository.update(block, changes))

    def delete_block(self, block_id: int) -> None:
        """Soft delete; the row is kept."""
        self.repository.soft_delete(self._get_active(block_id))

    def get_blocks_for_day(self, day: date) -> list[ScheduleRead]:
        """Blocks scheduled on ``day``, ordered by start_time then id (one query)."""
        return [
            ScheduleRead.model_validate(b)
            for b in self.repository.get_started_by(day)
            if is_due(b, day)
        ]
