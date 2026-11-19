"""Note and tag routers - API endpoints for notes, the note of the day and tag usage."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories.note import NoteRepository
from app.schemas.note import NoteCreate, NoteRead, NoteUpdate, TagRead
from app.services.note import InvalidTagError, NoteNotFoundError, NoteService
from app.services.recurrence import MAX_DAY, MIN_DAY

router = APIRouter(prefix="/notes", tags=["notes"])
tags_router = APIRouter(prefix="/tags", tags=["tags"])


def get_note_service(db: Annotated[Session, Depends(get_db)]) -> NoteService:
    """Dependency to get NoteService with repository."""
    return NoteService(NoteRepository(db))


ServiceDep = Annotated[NoteService, Depends(get_note_service)]


def _not_found(e: NoteNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("", response_model=NoteRead, status_code=status.HTTP_201_CREATED, summary="Create a note")
def create_note(data: NoteCreate, service: ServiceDep) -> NoteRead:
    return service.create_note(data)


@router.get("", response_model=list[NoteRead], summary="List notes (not deleted), newest update first")
def list_notes(
    service: ServiceDep,
    favorite: bool | None = None,
    tag: Annotated[str | None, Query(description="Matched after normalization")] = None,
) -> list[NoteRead]:
    """Returns bodies too, so the response grows with the notes (no limit for now)."""
    try:
        return service.list_notes(favorite=favorite, tag=tag)
    except InvalidTagError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e


# Declared before /{note_id} so the literal "random" segment is matched first.
@router.get("/random", response_model=NoteRead | None, summary="Note of the day (null if no notes)")
def note_of_the_day(
    service: ServiceDep,
    day: Annotated[
        date,
        Query(ge=MIN_DAY, le=MAX_DAY, description="Plain calendar date YYYY-MM-DD (2000-01-01..2100-12-31)"),
    ],
) -> NoteRead | None:
    """Same day -> same note: non-deleted notes ordered by id, index = day.toordinal() % n.

    The client supplies the day; the server never computes "today".
    Known limit: the pick is stable only while the note set is unchanged; adding or
    deleting notes can change the note of the day.
    """
    return service.get_note_of_the_day(day)


@router.get("/{note_id}", response_model=NoteRead, summary="Get a note")
def get_note(note_id: int, service: ServiceDep) -> NoteRead:
    try:
        return service.get_note(note_id)
    except NoteNotFoundError as e:
        raise _not_found(e) from e


@router.patch("/{note_id}", response_model=NoteRead, summary="Partially update a note")
def update_note(note_id: int, data: NoteUpdate, service: ServiceDep) -> NoteRead:
    try:
        return service.update_note(note_id, data)
    except NoteNotFoundError as e:
        raise _not_found(e) from e


@router.delete(
    "/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft delete a note (its tags are kept)",
)
def delete_note(note_id: int, service: ServiceDep) -> None:
    try:
        service.delete_note(note_id)
    except NoteNotFoundError as e:
        raise _not_found(e) from e


@tags_router.get("", response_model=list[TagRead], summary="Tags used by non-deleted notes, with counts")
def list_tags(service: ServiceDep) -> list[TagRead]:
    return service.list_tags()
