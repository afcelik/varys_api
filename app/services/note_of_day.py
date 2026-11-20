"""Pure note-of-the-day pick: deterministic for a (day, candidate set) pair."""

import random
from datetime import date


def pick_note_of_day(day: date, candidate_ids: list[int]) -> int:
    """One id from ``candidate_ids``; same day and same set -> same id, in any input order.

    Ids are sorted first, then ``random.Random(day.toordinal()).randrange(n)`` picks an
    index (seeded integer generation, stable across Python versions; ``choice``/``shuffle``
    are avoided). A plain ``ordinal % n`` would walk the list in order on consecutive days.
    Raises ValueError when there are no candidates.
    """
    if not candidate_ids:
        raise ValueError("no candidate notes")
    ids = sorted(candidate_ids)
    return ids[random.Random(day.toordinal()).randrange(len(ids))]
