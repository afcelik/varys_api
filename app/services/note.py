"""Note service - business logic for notes, tags and the note of the day."""

from datetime import date

from app.models.note import Note
from app.repositories.note import NoteRepository
from app.schemas.note import NoteCreate, NoteRead, NoteUpdate, TagRead
from app.services.note_of_day import pick_note_of_day
from app.services.tag_normalization import normalize_tag

# Sentinel: another writer stored the same day first; resolve again from the stored row.
_RACE_LOST = object()


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

    def ensure_note_of_day(self, day: date) -> NoteRead | None:
        """The stored note for a client-supplied day, picking and storing one if needed.

        - Entry exists and its note is not deleted -> that note, no write.
        - Entry exists but its note was soft-deleted -> re-pick within the entry's cycle
          and update the entry.
        - No entry -> pick within the latest cycle and insert.
        A cycle shows each note at most once (deleted notes stay "shown"); when no note is
        left, a new cycle starts with every non-deleted note. No notes -> None, no write.
        Any day (past/future) follows the same rule; the server never computes "today".
        A concurrent insert of the same day (UNIQUE) is resolved by reading the stored row.
        """
        result = self._resolve_note_of_day(day)
        if result is _RACE_LOST:
            result = self._resolve_note_of_day(day)
        return None if result is _RACE_LOST else result

    def _resolve_note_of_day(self, day: date) -> NoteRead | None | object:
        repo = self.repository
        entry = repo.get_day_entry(day)
        if entry is not None:
            note = repo.get_by_id(entry.note_id)
            if note is not None:
                return to_read(note)
        active = repo.active_note_ids()
        if not active:
            return None
        cycle = entry.cycle if entry is not None else (repo.max_cycle() or 1)
        shown = repo.shown_note_ids(cycle)
        candidates = [i for i in active if i not in shown]
        if not candidates:
            cycle = (repo.max_cycle() or cycle) + 1
            candidates = active
        note_id = pick_note_of_day(day, candidates)
        if entry is not None:
            repo.update_day_entry(entry, note_id, cycle)
        elif not repo.insert_day_entry(day, note_id, cycle):
            return _RACE_LOST
        note = repo.get_by_id(note_id)
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
