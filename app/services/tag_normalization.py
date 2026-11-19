"""Tag normalization: pure functions, no I/O.

Turkish-aware lowercasing: Python's ``str.lower()`` maps 'İ' (U+0130) to 'i' + U+0307
(combining dot above) and 'I' to 'i', both wrong for Turkish. So the dotted/dotless
capitals are mapped explicitly before ``lower()``.
"""

import unicodedata

MAX_TAG_LENGTH = 40
MAX_TAGS_PER_NOTE = 20


def normalize_tag(name: str) -> str:
    """NFC, Turkish-aware lowercase, trim, collapse inner whitespace.

    Raises ValueError when the result is empty, longer than MAX_TAG_LENGTH characters
    (Unicode code points) or contains a comma.
    """
    # NFC first: a decomposed 'İ' ('I' + U+0307) becomes the single U+0130 character,
    # so it takes the same 'İ' -> 'i' path below (no stray 'ı' + U+0307).
    text = unicodedata.normalize("NFC", name)
    text = text.replace("İ", "i").replace("I", "ı").lower()
    text = " ".join(text.split())
    if not text:
        raise ValueError("tag cannot be empty")
    if "," in text:
        raise ValueError("tag cannot contain a comma")
    if len(text) > MAX_TAG_LENGTH:
        raise ValueError(f"tag cannot be longer than {MAX_TAG_LENGTH} characters")
    return text


def normalize_tags(names: list[str]) -> list[str]:
    """Normalize every tag and drop duplicates (first occurrence wins, order kept).

    The MAX_TAGS_PER_NOTE limit applies to the de-duplicated list.
    """
    unique = list(dict.fromkeys(normalize_tag(n) for n in names))
    if len(unique) > MAX_TAGS_PER_NOTE:
        raise ValueError(f"a note cannot have more than {MAX_TAGS_PER_NOTE} tags")
    return unique
