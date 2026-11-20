"""Schedule repository - data access layer for schedule blocks."""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.schedule import ScheduleBlock
from app.schemas.schedule import ScheduleCreate

# Single source of list ordering: start time, then id for equal start times.
_ORDER = (ScheduleBlock.start_time, ScheduleBlock.id)


class ScheduleRepository:
    """The only place that touches the DB for schedule blocks (commit/refresh)."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, data: ScheduleCreate) -> ScheduleBlock:
        block = ScheduleBlock(**data.model_dump())
        self.db.add(block)
        self.db.commit()
        self.db.refresh(block)
        return block

    def get_by_id(self, block_id: int) -> ScheduleBlock | None:
        """Active (not soft-deleted) block by id."""
        stmt = select(ScheduleBlock).where(
            ScheduleBlock.id == block_id, ScheduleBlock.deleted_at.is_(None)
        )
        return self.db.scalars(stmt).first()

    def get_all(self) -> list[ScheduleBlock]:
        """All active blocks ordered by start_time, then id."""
        stmt = select(ScheduleBlock).where(ScheduleBlock.deleted_at.is_(None)).order_by(*_ORDER)
        return list(self.db.scalars(stmt).all())

    def get_started_by(self, day: date) -> list[ScheduleBlock]:
        """Active blocks with start_date <= day, ordered (is_due filters the rest)."""
        stmt = (
            select(ScheduleBlock)
            .where(ScheduleBlock.deleted_at.is_(None), ScheduleBlock.start_date <= day)
            .order_by(*_ORDER)
        )
        return list(self.db.scalars(stmt).all())

    def update(self, block: ScheduleBlock, changes: dict[str, Any]) -> ScheduleBlock:
        for name, value in changes.items():
            setattr(block, name, value)
        self.db.commit()
        self.db.refresh(block)
        return block

    def soft_delete(self, block: ScheduleBlock) -> None:
        block.deleted_at = datetime.now(UTC)
        self.db.commit()
