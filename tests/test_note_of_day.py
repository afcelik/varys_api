"""Note of the day: pure pick function (unit) and POST /notes/of-day (HTTP)."""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import get_db
from app.main import create_app
from app.models.base import Base
from app.models.note import NoteOfDay
from app.repositories.note import NoteRepository
from app.services.note_of_day import pick_note_of_day

# In-memory SQLite, one shared connection (same setup as tests/test_notes.py).
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


def _configure_test_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


event.listen(test_engine, "connect", _configure_test_connection)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

WRITE_PREFIXES = ("INSERT", "UPDATE", "DELETE")


def _token() -> str:
    now = datetime.now(UTC)
    payload = {"sub": "user", "exp": now + timedelta(days=1), "iat": now}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


@pytest.fixture
def db_session() -> Iterator[Session]:
    Base.metadata.create_all(bind=test_engine)
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=test_engine)


async def _client(db_session: Session, headers: dict[str, str]) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def client(db_session: Session) -> AsyncIterator[AsyncClient]:
    async for ac in _client(db_session, {"Authorization": f"Bearer {_token()}"}):
        yield ac


@pytest.fixture
async def client_no_auth(db_session: Session) -> AsyncIterator[AsyncClient]:
    async for ac in _client(db_session, {}):
        yield ac


@contextmanager
def capture_sql() -> Iterator[list[str]]:
    """Collect every SQL statement sent to the test engine."""
    statements: list[str] = []

    def _before(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    event.listen(test_engine, "before_cursor_execute", _before)
    try:
        yield statements
    finally:
        event.remove(test_engine, "before_cursor_execute", _before)


def _writes(statements: list[str]) -> list[str]:
    return [s for s in statements if s.lstrip().upper().startswith(WRITE_PREFIXES)]


async def make_note(client: AsyncClient, title: str = "N", **fields) -> dict:
    resp = await client.post("/notes", json={"title": title, **fields})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def delete_note(client: AsyncClient, note_id: int) -> None:
    assert (await client.delete(f"/notes/{note_id}")).status_code == 204


async def of_day(client: AsyncClient, day: date) -> dict | None:
    resp = await client.post("/notes/of-day", params={"day": day.isoformat()})
    assert resp.status_code == 200, resp.text
    return resp.json()


def entries(db: Session) -> list[tuple[date, int, int]]:
    """(day, note_id, cycle) rows by day, read straight from the table."""
    stmt = select(NoteOfDay.day, NoteOfDay.note_id, NoteOfDay.cycle).order_by(NoteOfDay.day)
    return [tuple(row) for row in db.execute(stmt)]


def entry_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(NoteOfDay)) or 0


# --- pick_note_of_day (pure) -------------------------------------------------------

DAY = date(2026, 11, 20)
TEN_IDS = [3, 7, 11, 12, 20, 21, 34, 40, 41, 55]


class TestPickNoteOfDay:
    def test_same_input_same_output(self) -> None:
        picks = {pick_note_of_day(DAY, TEN_IDS) for _ in range(20)}
        assert len(picks) == 1
        assert picks.pop() in TEN_IDS

    def test_result_is_always_a_candidate(self) -> None:
        for offset in range(200):
            assert pick_note_of_day(DAY + timedelta(days=offset), TEN_IDS) in TEN_IDS

    def test_consecutive_days_move_around(self) -> None:
        picks = [pick_note_of_day(DAY + timedelta(days=i), TEN_IDS) for i in range(100)]
        changes = sum(1 for a, b in zip(picks, picks[1:], strict=False) if a != b)
        assert changes >= 50
        assert set(picks) == set(TEN_IDS)

    def test_not_plain_ordinal_mod_n(self) -> None:
        ids = sorted(TEN_IDS)
        days = [DAY + timedelta(days=i) for i in range(10)]
        walk = [ids[d.toordinal() % len(ids)] for d in days]
        assert [pick_note_of_day(d, TEN_IDS) for d in days] != walk

    def test_single_candidate(self) -> None:
        for offset in (0, 1, 365, -365):
            assert pick_note_of_day(DAY + timedelta(days=offset), [42]) == 42

    def test_order_independent(self) -> None:
        shuffled = [41, 3, 55, 20, 12, 7, 40, 34, 11, 21]
        reversed_ids = list(reversed(TEN_IDS))
        for offset in range(50):
            day = DAY + timedelta(days=offset)
            expected = pick_note_of_day(day, TEN_IDS)
            assert pick_note_of_day(day, shuffled) == expected
            assert pick_note_of_day(day, reversed_ids) == expected

    def test_input_list_not_mutated(self) -> None:
        shuffled = [41, 3, 55, 20]
        pick_note_of_day(DAY, shuffled)
        assert shuffled == [41, 3, 55, 20]

    def test_empty_candidates_raise(self) -> None:
        with pytest.raises(ValueError):
            pick_note_of_day(DAY, [])


# --- POST /notes/of-day ------------------------------------------------------------

D0 = date(2026, 11, 20)


def days(n: int, start: date = D0) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


class TestStoredPick:
    async def test_same_day_same_note_and_second_call_does_not_write(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        made = {n["id"]: n for n in [await make_note(client, t, tags=["iş", t]) for t in "ABC"]}
        first = await of_day(client, D0)
        with capture_sql() as sql:
            second = await of_day(client, D0)
        assert first is not None and first == second
        assert first == made[first["id"]]  # full NoteRead, tags included
        assert _writes(sql) == []
        assert entries(db_session) == [(D0, first["id"], 1)]

    async def test_no_notes_returns_null_and_writes_nothing(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        with capture_sql() as sql:
            resp = await client.post("/notes/of-day", params={"day": D0.isoformat()})
        assert resp.status_code == 200 and resp.json() is None
        assert _writes(sql) == []
        assert entry_count(db_session) == 0

    async def test_all_deleted_returns_null_and_writes_nothing(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        note = await make_note(client)
        await delete_note(client, note["id"])
        with capture_sql() as sql:
            assert await of_day(client, D0) is None
        assert _writes(sql) == []
        assert entry_count(db_session) == 0

    async def test_note_added_during_the_day_does_not_change_todays_note(
        self, client: AsyncClient
    ) -> None:
        for t in "AB":
            await make_note(client, t)
        today = await of_day(client, D0)
        for t in "CDEFG":
            await make_note(client, t)
        assert await of_day(client, D0) == today


class TestCycles:
    async def test_n_notes_n_days_each_once_then_new_cycle(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        ids = {(await make_note(client, t))["id"] for t in "ABCDE"}
        shown = [(await of_day(client, d))["id"] for d in days(5)]
        assert sorted(shown) == sorted(ids)  # each note exactly once
        assert [c for _, _, c in entries(db_session)] == [1] * 5

        sixth = await of_day(client, D0 + timedelta(days=5))
        assert sixth["id"] in ids
        assert entries(db_session)[-1] == (D0 + timedelta(days=5), sixth["id"], 2)

    async def test_second_cycle_again_shows_every_note_once(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        ids = sorted([(await make_note(client, t))["id"] for t in "ABCD"])
        shown = [(await of_day(client, d))["id"] for d in days(8)]
        assert sorted(shown[:4]) == ids and sorted(shown[4:]) == ids
        assert [c for _, _, c in entries(db_session)] == [1] * 4 + [2] * 4

    async def test_note_added_mid_cycle_joins_the_same_cycle(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        ids = [(await make_note(client, t))["id"] for t in "ABCDE"]
        shown = [(await of_day(client, d))["id"] for d in days(2)]
        ids.append((await make_note(client, "F"))["id"])
        shown += [(await of_day(client, d))["id"] for d in days(4, D0 + timedelta(days=2))]
        assert sorted(shown) == sorted(ids)  # 6 notes, 6 days, each once
        assert [c for _, _, c in entries(db_session)] == [1] * 6

    async def test_single_note_always_returned_cycles_grow(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        only = await make_note(client, "Tek", tags=["a"])
        for d in days(4):
            assert await of_day(client, d) == only
        assert [(n, c) for _, n, c in entries(db_session)] == [
            (only["id"], c) for c in (1, 2, 3, 4)
        ]

    async def test_deleted_shown_note_does_not_return_in_next_cycle(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        ids = {(await make_note(client, t))["id"] for t in "ABC"}
        gone = (await of_day(client, D0))["id"]
        await delete_note(client, gone)
        later = [(await of_day(client, d))["id"] for d in days(6, D0 + timedelta(days=1))]
        assert gone not in later
        assert sorted(later[:2]) == sorted(ids - {gone})  # rest of cycle 1
        assert sorted(later[2:4]) == sorted(ids - {gone})  # cycle 2
        assert [c for _, _, c in entries(db_session)] == [1, 1, 1, 2, 2, 3, 3]

    async def test_any_order_of_days_follows_the_same_rule(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        ids = sorted([(await make_note(client, t))["id"] for t in "ABC"])
        order = [D0 + timedelta(days=10), D0 - timedelta(days=30), D0]
        shown = [(await of_day(client, d))["id"] for d in order]
        assert sorted(shown) == ids
        assert [c for _, _, c in entries(db_session)] == [1, 1, 1]
        assert [(await of_day(client, d))["id"] for d in order] == shown  # stored, stable
        fourth = await of_day(client, D0 + timedelta(days=1))  # pool used up -> cycle 2
        assert (D0 + timedelta(days=1), fourth["id"], 2) in entries(db_session)


class TestDeletedTodaysNote:
    async def test_repicks_unshown_note_and_updates_entry(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        ids = {(await make_note(client, t))["id"] for t in "ABC"}
        d1, d2 = days(2)
        first = (await of_day(client, d1))["id"]
        todays = (await of_day(client, d2))["id"]
        await delete_note(client, todays)

        repicked = (await of_day(client, d2))["id"]
        assert repicked == (ids - {first, todays}).pop()  # the only unshown, live note
        assert entries(db_session) == [(d1, first, 1), (d2, repicked, 1)]
        with capture_sql() as sql:
            assert (await of_day(client, d2))["id"] == repicked
        assert _writes(sql) == []

    async def test_repick_starts_new_cycle_when_cycle_is_used_up(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        ids = {(await make_note(client, t))["id"] for t in "AB"}
        d1, d2 = days(2)
        first = (await of_day(client, d1))["id"]
        todays = (await of_day(client, d2))["id"]
        await delete_note(client, todays)

        assert (await of_day(client, d2))["id"] == first == (ids - {todays}).pop()
        assert entries(db_session) == [(d1, first, 1), (d2, first, 2)]

    async def test_entry_kept_and_null_when_no_live_note_left(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        note = await make_note(client)
        await of_day(client, D0)
        await delete_note(client, note["id"])
        with capture_sql() as sql:
            assert await of_day(client, D0) is None
        assert _writes(sql) == []
        assert entries(db_session) == [(D0, note["id"], 1)]


class TestRace:
    async def test_unique_violation_returns_stored_note_not_500(
        self, client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stored = await make_note(client, "Önce yazılan", tags=["x"])
        await make_note(client, "Diğer")
        db_session.add(NoteOfDay(day=D0, note_id=stored["id"], cycle=1))
        db_session.commit()

        real = NoteRepository.get_day_entry
        calls = {"n": 0}

        def first_read_misses(self: NoteRepository, day: date) -> NoteOfDay | None:
            calls["n"] += 1
            return None if calls["n"] == 1 else real(self, day)

        monkeypatch.setattr(NoteRepository, "get_day_entry", first_read_misses)
        resp = await client.post("/notes/of-day", params={"day": D0.isoformat()})
        assert resp.status_code == 200, resp.text
        assert resp.json() == stored
        assert calls["n"] == 2
        assert entries(db_session) == [(D0, stored["id"], 1)]


class TestValidationAndAuth:
    @pytest.mark.parametrize("day", ["2000-01-01", "2100-12-31"])
    async def test_day_bounds_inclusive(self, client: AsyncClient, day: str) -> None:
        await make_note(client)
        assert (await client.post("/notes/of-day", params={"day": day})).status_code == 200

    @pytest.mark.parametrize("day", ["1999-12-31", "2101-01-01", "2026-13-01", "bugün"])
    async def test_invalid_day_422(
        self, client: AsyncClient, db_session: Session, day: str
    ) -> None:
        await make_note(client)
        assert (await client.post("/notes/of-day", params={"day": day})).status_code == 422
        assert entry_count(db_session) == 0

    async def test_missing_day_422(self, client: AsyncClient) -> None:
        assert (await client.post("/notes/of-day")).status_code == 422

    async def test_requires_token(self, client_no_auth: AsyncClient, db_session: Session) -> None:
        resp = await client_no_auth.post("/notes/of-day", params={"day": "2026-11-20"})
        assert resp.status_code == 401
        assert entry_count(db_session) == 0
