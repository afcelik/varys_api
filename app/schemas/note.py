"""Pydantic schemas for Note and Tag API."""

from datetime import datetime
from typing import Annotated, Any, ClassVar

from pydantic import AfterValidator, BaseModel, Field, StringConstraints, model_validator

from app.services.tag_normalization import normalize_tags

MAX_BODY_LENGTH = 20000

# Title is trimmed first, then checked: "" and whitespace-only -> 422.
NoteTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
NoteBody = Annotated[str, Field(max_length=MAX_BODY_LENGTH)]
# Normalized (Turkish-aware), de-duplicated, at most 20; invalid tag -> 422.
NoteTags = Annotated[list[str], AfterValidator(normalize_tags)]


class NoteCreate(BaseModel):
    """Schema for creating a note."""

    title: NoteTitle
    body: NoteBody | None = None
    tags: NoteTags = Field(default_factory=list)
    is_favorite: bool = False


class NoteUpdate(BaseModel):
    """Partial update.

    Field status is explicit via ``model_fields_set``:
    - not sent  -> unchanged (tags included: an absent ``tags`` keeps the current tags)
    - sent null -> cleared (body only; title/is_favorite/tags -> 422)
    - sent value -> set; ``tags`` replaces the whole list (``[]`` removes all tags)
    """

    NON_NULLABLE: ClassVar[tuple[str, ...]] = ("title", "is_favorite", "tags")

    title: NoteTitle | None = None
    body: NoteBody | None = None
    tags: NoteTags | None = None
    is_favorite: bool | None = None

    @model_validator(mode="after")
    def _reject_null_for_required(self) -> "NoteUpdate":
        for name in self.NON_NULLABLE:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self

    def changes(self) -> dict[str, Any]:
        """Only the fields the client actually sent (including explicit nulls), in field order."""
        sent = self.model_fields_set
        return {name: getattr(self, name) for name in type(self).model_fields if name in sent}


class NoteRead(BaseModel):
    """Schema for reading a note; tags sorted by their normalized text."""

    id: int
    title: str
    body: str | None
    is_favorite: bool
    tags: list[str]
    created_at: datetime
    updated_at: datetime


class TagRead(BaseModel):
    """A tag with the number of non-deleted notes using it (always >= 1)."""

    name: str
    note_count: int
