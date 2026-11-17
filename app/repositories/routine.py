"""Routine repository - data access layer for routines and their logs."""

from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.routine import Routine, RoutineLog
from app.schemas.routine import RoutineCreate


class RoutineRepository:
    """The only place that touches the DB for routines (commit/refresh/rollback)."""

    def __init__(self, db: Session):
        self.db = db

    # --- routines -------------------------------------------------------------

    def create(self, data: RoutineCreate) -> Routine:
        routine = Routine(**data.model_dump())
        self.db.add(routine)
        self.db.commit()
        self.db.refresh(routine)
        return routine

    def get_by_id(self, routine_id: int) -> Routine | None:
        """Active (not soft-deleted) routine by id."""
        stmt = select(Routine).where(Routine.id == routine_id, Routine.deleted_at.is_(None))
        return self.db.scalars(stmt).first()

    def get_all(self) -> list[Routine]:
        """All active routines, oldest first."""
        stmt = select(Routine).where(Routine.deleted_at.is_(None)).order_by(Routine.id)
        return list(self.db.scalars(stmt).all())

    def get_started_by(self, day: date) -> list[Routine]:
        """Active routines whose start_date is on or before ``day`` (is_due filters the rest)."""
        stmt = (
            select(Routine)
            .where(Routine.deleted_at.is_(None), Routine.start_date <= day)
            .order_by(Routine.id)
        )
        return list(self.db.scalars(stmt).all())

    def update(self, routine: Routine, changes: dict[str, Any]) -> Routine:
        for name, value in changes.items():
            setattr(routine, name, value)
        self.db.commit()
        self.db.refresh(routine)
        return routine

    def soft_delete(self, routine: Routine) -> None:
        routine.deleted_at = datetime.now(UTC)
        self.db.commit()

    # --- logs -----------------------------------------------------------------

    def get_log(self, routine_id: int, day: date) -> RoutineLog | None:
        stmt = select(RoutineLog).where(
            RoutineLog.routine_id == routine_id, RoutineLog.log_date == day
        )
        return self.db.scalars(stmt).first()

    def create_log(self, routine_id: int, day: date) -> RoutineLog:
        """Insert a log. Rolls back and re-raises IntegrityError (e.g. a concurrent insert)."""
        log = RoutineLog(routine_id=routine_id, log_date=day)
        self.db.add(log)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise
        self.db.refresh(log)
        return log

    def rollback(self) -> None:
        self.db.rollback()

    def delete_log(self, routine_id: int, day: date) -> bool:
        """Delete the log if present. Returns whether a row was removed."""
        result = self.db.execute(
            delete(RoutineLog).where(
                RoutineLog.routine_id == routine_id, RoutineLog.log_date == day
            )
        )
        self.db.commit()
        return result.rowcount > 0

    def get_log_dates(self, routine_ids: list[int]) -> dict[int, set[date]]:
        """All completion dates for the given routines, in a single query."""
        dates: dict[int, set[date]] = defaultdict(set)
        if not routine_ids:
            return dates
        stmt = select(RoutineLog.routine_id, RoutineLog.log_date).where(
            RoutineLog.routine_id.in_(routine_ids)
        )
        for routine_id, log_date in self.db.execute(stmt):
            dates[routine_id].add(log_date)
        return dates
