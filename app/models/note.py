"""Note, tag, note-tag link and note-of-the-day models."""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

# Link table; rows survive a note soft delete (only a physical delete cascades).
note_tags = Table(
    "note_tags",
    Base.metadata,
    Column("note_id", Integer, ForeignKey("notes.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    Index("ix_note_tags_tag_id", "tag_id"),
)


class Tag(Base):
    """A free-form tag; ``name`` is always stored normalized (see tag_normalization)."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)


class Note(Base, TimestampMixin):
    """A note with an optional body, a favorite flag and tags."""

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_favorite: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # lazy="raise": tags must be loaded explicitly (selectinload), so a hidden
    # per-note lazy load (N+1) fails loudly instead of silently.
    tags: Mapped[list[Tag]] = relationship(secondary=note_tags, lazy="raise")


class NoteOfDay(Base):
    """The note shown on a calendar day; one row per day, written once and then reused.

    ``cycle`` numbers the rounds: within a cycle every note is shown at most once. A
    physical note delete cascades the row; a soft delete keeps it (counts as shown).
    """

    __tablename__ = "note_of_day"
    __table_args__ = (CheckConstraint("cycle >= 1", name="ck_note_of_day_cycle_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    note_id: Mapped[int] = mapped_column(
        ForeignKey("notes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
