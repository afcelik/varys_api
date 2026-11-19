"""Note service - business logic for notes, tags and the note of the day."""

from datetime import date

from app.models.note import Note
from app.repositories.note import NoteRepository
from app.schemas.note import NoteCreate, NoteRead, NoteUpdate, TagRead
from app.services.tag_normalization import normalize_tag


class NoteNotFoundError(Exception):
    """Note does not exist or is soft-deleted."""


class InvalidTagError(Exception):
    """A tag filter value is not a valid tag (empty, too long, contains a comma)."""


def to_read(note: Note) -> NoteRead:
    return NoteRead(
        id=note.id,
        title=note.title,
        body=note.body,
        is_favorite=note.is_favorite,
        tags=sorted(tag.name for tag in note.tags),
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


class NoteService:
    """Note operations. GET paths never write."""

    def __init__(self, repository: NoteRepository):
        self.repository = repository

    def _get_active(self, note_id: int) -> Note:
        note = self.repository.get_by_id(note_id)
        if note is None:
            raise NoteNotFoundError(f"Note with id {note_id} not found")
        return note

    def create_note(self, data: NoteCreate) -> NoteRead:
        tags = self.repository.get_or_create_tags(data.tags)
        fields = data.model_dump(exclude={"tags"})
        return to_read(self.repository.create(fields, tags))

    def list_notes(self, favorite: bool | None = None, tag: str | None = None) -> list[NoteRead]:
        if tag is not None:
            try:
                tag = normalize_tag(tag)
            except ValueError as e:
                raise InvalidTagError(str(e)) from e
        return [to_read(n) for n in self.repository.get_all(favorite=favorite, tag=tag)]

    def get_note(self, note_id: int) -> NoteRead:
        return to_read(self._get_active(note_id))

    def get_note_of_the_day(self, day: date) -> NoteRead | None:
        """Deterministic note for a client-supplied day: notes by id, index = ordinal % n.

        The server never computes "today". The pick is stable only while the note set is
        unchanged: adding or deleting notes changes n (and positions), so the note of a
        given day may change. Returns None when there are no notes.
        """
        n = self.repository.count_active()
        if n == 0:
            return None
        note = self.repository.get_nth_by_id(day.toordinal() % n)
        return to_read(note) if note is not None else None

    def update_note(self, note_id: int, update: NoteUpdate) -> NoteRead:
        note = self._get_active(note_id)
        changes = update.changes()
        tags = None
        if "tags" in changes:
            tags = self.repository.get_or_create_tags(changes.pop("tags"))
        return to_read(self.repository.update(note, changes, tags))

    def delete_note(self, note_id: int) -> None:
        self.repository.soft_delete(self._get_active(note_id))

    def list_tags(self) -> list[TagRead]:
        return [TagRead(name=n, note_count=c) for n, c in self.repository.tag_counts()]
