"""Note repository - data access layer for notes and tags."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import Select

from app.models.note import Note, Tag, note_tags


class NoteRepository:
    """The only place that touches the DB for notes (commit/refresh).

    ``Note.tags`` is ``lazy="raise"``: every note query goes through ``_active()``, which
    loads tags with one extra SELECT for the whole result (no N+1).
    """

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _active() -> Select[tuple[Note]]:
        return (
            select(Note).options(selectinload(Note.tags)).where(Note.deleted_at.is_(None))
        )

    # --- tags ----------------------------------------------------------------

    def _existing_tags(self, names: list[str]) -> dict[str, Tag]:
        stmt = select(Tag).where(Tag.name.in_(names))
        return {tag.name: tag for tag in self.db.scalars(stmt)}

    def _create_tag(self, name: str) -> Tag:
        """Insert a tag; if a concurrent writer created it first, re-read that row.

        The INSERT runs in a SAVEPOINT so a UNIQUE violation only rolls back this tag.
        """
        try:
            with self.db.begin_nested():
                tag = Tag(name=name)
                self.db.add(tag)
            return tag
        except IntegrityError:
            return self.db.scalars(select(Tag).where(Tag.name == name)).one()

    def get_or_create_tags(self, names: list[str]) -> list[Tag]:
        """Tags for already-normalized, unique names, in the given order."""
        if not names:
            return []
        found = self._existing_tags(names)
        return [found[name] if name in found else self._create_tag(name) for name in names]

    def tag_counts(self) -> list[tuple[str, int]]:
        """(name, count of non-deleted notes) per tag, by name; unused tags are absent."""
        stmt = (
            select(Tag.name, func.count(Note.id))
            .join(note_tags, note_tags.c.tag_id == Tag.id)
            .join(Note, Note.id == note_tags.c.note_id)
            .where(Note.deleted_at.is_(None))
            .group_by(Tag.id, Tag.name)
            .order_by(Tag.name)
        )
        return [(name, count) for name, count in self.db.execute(stmt)]

    # --- notes ---------------------------------------------------------------

    def get_by_id(self, note_id: int) -> Note | None:
        """Active (not soft-deleted) note by id, with tags loaded."""
        return self.db.scalars(self._active().where(Note.id == note_id)).first()

    def get_all(self, favorite: bool | None = None, tag: str | None = None) -> list[Note]:
        """Non-deleted notes, most recently updated first (id breaks second-level ties)."""
        stmt = self._active()
        if favorite is not None:
            stmt = stmt.where(Note.is_favorite == favorite)
        if tag is not None:
            stmt = stmt.where(Note.tags.any(Tag.name == tag))
        stmt = stmt.order_by(Note.updated_at.desc(), Note.id.desc())
        return list(self.db.scalars(stmt).all())

    def count_active(self) -> int:
        stmt = select(func.count(Note.id)).where(Note.deleted_at.is_(None))
        return self.db.scalar(stmt) or 0

    def get_nth_by_id(self, index: int) -> Note | None:
        """The index-th (0-based) non-deleted note in id order."""
        stmt = self._active().order_by(Note.id).offset(index).limit(1)
        return self.db.scalars(stmt).first()

    def create(self, fields: dict[str, Any], tags: list[Tag]) -> Note:
        note = Note(**fields, tags=tags)
        self.db.add(note)
        self.db.commit()
        return self._reload(note.id)

    def update(self, note: Note, changes: dict[str, Any], tags: list[Tag] | None) -> Note:
        """Apply column changes; ``tags`` (when not None) replaces the tag list.

        A tag-only change does not touch the notes row, so ``onupdate`` would not fire;
        ``updated_at`` is then set explicitly.
        """
        for name, value in changes.items():
            setattr(note, name, value)
        if tags is not None and {t.name for t in tags} != {t.name for t in note.tags}:
            note.tags = tags
            note.updated_at = func.now()
        self.db.commit()
        return self._reload(note.id)

    def soft_delete(self, note: Note) -> None:
        """Soft delete; note_tags links and tags are kept."""
        note.deleted_at = datetime.now(UTC)
        self.db.commit()

    def _reload(self, note_id: int) -> Note:
        note = self.get_by_id(note_id)
        assert note is not None
        return note
