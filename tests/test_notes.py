"""HTTP-level tests for notes: CRUD, tags, favorites, note of the day and query shape."""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import get_db
from app.main import create_app
from app.models.base import Base
from app.models.note import Note, Tag, note_tags
from app.repositories.note import NoteRepository

# In-memory SQLite, one shared connection (same setup as tests/test_projects.py).
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
DECOMPOSED_I = "İ"  # 'I' + COMBINING DOT ABOVE (a decomposed 'İ')


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


def _selects(statements: list[str]) -> list[str]:
    return [s for s in statements if s.lstrip().upper().startswith("SELECT")]


async def make_note(client: AsyncClient, title: str = "N", **fields) -> dict:
    resp = await client.post("/notes", json={"title": title, **fields})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def list_notes(client: AsyncClient, **params) -> list[dict]:
    resp = await client.get("/notes", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def random_note(client: AsyncClient, day: date) -> dict | None:
    resp = await client.get("/notes/random", params={"day": day.isoformat()})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _tag_rows(db: Session) -> list[str]:
    return sorted(db.scalars(select(Tag.name)))


def _link_count(db: Session, note_id: int) -> int:
    stmt = select(func.count()).select_from(note_tags).where(note_tags.c.note_id == note_id)
    return db.scalar(stmt) or 0


def _set_updated_at(db: Session, note_id: int, value: datetime) -> None:
    db.execute(update(Note).where(Note.id == note_id).values(updated_at=value))
    db.commit()


# --- CRUD --------------------------------------------------------------------


class TestNoteCrud:
    async def test_create_returns_all_fields_with_defaults(self, client: AsyncClient) -> None:
        body = await make_note(client, "Fikir")
        assert body["title"] == "Fikir"
        assert body["body"] is None
        assert body["is_favorite"] is False
        assert body["tags"] == []
        assert isinstance(body["id"], int)
        assert body["created_at"] and body["updated_at"]

    async def test_create_with_all_fields(self, client: AsyncClient) -> None:
        body = await make_note(client, "Kitap", body="Gövde", is_favorite=True, tags=["okuma"])
        assert (body["body"], body["is_favorite"], body["tags"]) == ("Gövde", True, ["okuma"])

    async def test_get_returns_stored_note(self, client: AsyncClient) -> None:
        created = await make_note(client, "A", body="b", tags=["x"])
        resp = await client.get(f"/notes/{created['id']}")
        assert resp.status_code == 200
        assert resp.json() == created

    async def test_patch_sets_only_sent_fields(self, client: AsyncClient) -> None:
        created = await make_note(client, "A", body="b", tags=["x"])
        resp = await client.patch(f"/notes/{created['id']}", json={"is_favorite": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_favorite"] is True
        assert (body["title"], body["body"], body["tags"]) == ("A", "b", ["x"])

    async def test_patch_title_and_body(self, client: AsyncClient) -> None:
        created = await make_note(client, "A", body="b")
        resp = await client.patch(f"/notes/{created['id']}", json={"title": "B", "body": "c"})
        assert (resp.json()["title"], resp.json()["body"]) == ("B", "c")

    async def test_body_null_clears(self, client: AsyncClient) -> None:
        created = await make_note(client, "A", body="b")
        resp = await client.patch(f"/notes/{created['id']}", json={"body": None})
        assert resp.status_code == 200
        assert resp.json()["body"] is None
        assert (await client.get(f"/notes/{created['id']}")).json()["body"] is None

    @pytest.mark.parametrize("method", ["get", "patch", "delete"])
    async def test_missing_note_404(self, client: AsyncClient, method: str) -> None:
        kwargs = {"json": {"title": "x"}} if method == "patch" else {}
        resp = await getattr(client, method)("/notes/999", **kwargs)
        assert resp.status_code == 404


class TestTitleAndBodyValidation:
    @pytest.mark.parametrize("title", ["", "   ", "\t\n"])
    async def test_create_blank_title_422(self, client: AsyncClient, title: str) -> None:
        assert (await client.post("/notes", json={"title": title})).status_code == 422

    async def test_create_null_or_missing_title_422(self, client: AsyncClient) -> None:
        assert (await client.post("/notes", json={"title": None})).status_code == 422
        assert (await client.post("/notes", json={})).status_code == 422

    async def test_title_is_trimmed(self, client: AsyncClient) -> None:
        assert (await make_note(client, "  Başlık  "))["title"] == "Başlık"

    async def test_title_255_ok_256_422(self, client: AsyncClient) -> None:
        await make_note(client, "a" * 255)
        assert (await client.post("/notes", json={"title": "a" * 256})).status_code == 422

    @pytest.mark.parametrize("payload", [{"title": None}, {"title": "  "}, {"is_favorite": None}])
    async def test_patch_invalid_required_fields_422(
        self, client: AsyncClient, payload: dict
    ) -> None:
        created = await make_note(client, "A")
        resp = await client.patch(f"/notes/{created['id']}", json=payload)
        assert resp.status_code == 422
        assert (await client.get(f"/notes/{created['id']}")).json() == created

    async def test_body_20000_ok_20001_422(self, client: AsyncClient) -> None:
        created = await make_note(client, "A", body="ş" * 20000)
        assert len(created["body"]) == 20000
        assert (await client.post("/notes", json={"title": "B", "body": "x" * 20001})).status_code == 422
        resp = await client.patch(f"/notes/{created['id']}", json={"body": "x" * 20001})
        assert resp.status_code == 422


# --- tags ----------------------------------------------------------------------


class TestTags:
    async def test_create_normalizes_turkish_and_sorts(self, client: AsyncClient) -> None:
        body = await make_note(client, tags=["ISPARTA", "İş", "  Proje   Fikri "])
        assert body["tags"] == ["iş", "proje fikri", "ısparta"]
        assert "̇" not in "".join(body["tags"])

    async def test_duplicates_after_normalization_are_merged(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        body = await make_note(client, tags=["İş", "iş", " İŞ ", DECOMPOSED_I + "ş"])
        assert body["tags"] == ["iş"]
        assert _tag_rows(db_session) == ["iş"]
        assert _link_count(db_session, body["id"]) == 1

    async def test_existing_tag_is_reused_across_notes(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        await make_note(client, "A", tags=["iş"])
        await make_note(client, "B", tags=["İŞ"])
        assert _tag_rows(db_session) == ["iş"]

    async def test_20_tags_ok_21_422(self, client: AsyncClient) -> None:
        twenty = [f"t{i}" for i in range(20)]
        assert len((await make_note(client, tags=twenty))["tags"]) == 20
        resp = await client.post("/notes", json={"title": "x", "tags": [*twenty, "t20"]})
        assert resp.status_code == 422

    async def test_tag_40_chars_ok_41_422(self, client: AsyncClient) -> None:
        assert (await make_note(client, tags=["ğ" * 40]))["tags"] == ["ğ" * 40]
        resp = await client.post("/notes", json={"title": "x", "tags": ["ğ" * 41]})
        assert resp.status_code == 422

    @pytest.mark.parametrize("tag", ["iş,ev", "", "   "])
    async def test_invalid_tag_422(self, client: AsyncClient, tag: str) -> None:
        assert (await client.post("/notes", json={"title": "x", "tags": [tag]})).status_code == 422

    async def test_patch_replaces_whole_list(self, client: AsyncClient) -> None:
        created = await make_note(client, tags=["a", "b"])
        resp = await client.patch(f"/notes/{created['id']}", json={"tags": ["c", "A"]})
        assert resp.status_code == 200
        assert resp.json()["tags"] == ["a", "c"]

    async def test_patch_empty_list_clears(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        created = await make_note(client, tags=["a", "b"])
        resp = await client.patch(f"/notes/{created['id']}", json={"tags": []})
        assert resp.json()["tags"] == []
        assert _link_count(db_session, created["id"]) == 0

    async def test_patch_without_tags_keeps_them(self, client: AsyncClient) -> None:
        created = await make_note(client, tags=["a", "b"])
        resp = await client.patch(f"/notes/{created['id']}", json={"title": "Yeni"})
        assert resp.json()["tags"] == ["a", "b"]
        assert (await client.get(f"/notes/{created['id']}")).json()["tags"] == ["a", "b"]

    async def test_patch_tags_null_422(self, client: AsyncClient) -> None:
        created = await make_note(client, tags=["a"])
        resp = await client.patch(f"/notes/{created['id']}", json={"tags": None})
        assert resp.status_code == 422
        assert (await client.get(f"/notes/{created['id']}")).json()["tags"] == ["a"]

    async def test_patch_invalid_tag_422(self, client: AsyncClient) -> None:
        created = await make_note(client, tags=["a"])
        resp = await client.patch(f"/notes/{created['id']}", json={"tags": ["x,y"]})
        assert resp.status_code == 422

    async def test_tag_only_patch_bumps_updated_at(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        created = await make_note(client, tags=["a"])
        old = datetime(2020, 1, 1, 0, 0, 0)
        _set_updated_at(db_session, created["id"], old)

        resp = await client.patch(f"/notes/{created['id']}", json={"tags": ["b"]})
        assert resp.status_code == 200
        assert datetime.fromisoformat(resp.json()["updated_at"]).replace(tzinfo=None) > old
        db_session.expire_all()
        stored = db_session.scalar(select(Note.updated_at).where(Note.id == created["id"]))
        assert stored.replace(tzinfo=None) > old


class TestTagCreateRace:
    """A concurrent writer creates the same tag between our lookup and our INSERT."""

    @pytest.fixture
    def stale_lookup(self, monkeypatch: pytest.MonkeyPatch) -> list[int]:
        """The first tag lookup misses every tag (as if read before the other writer)."""
        calls: list[int] = []
        original = NoteRepository._existing_tags

        def _stale_once(self: NoteRepository, names: list[str]) -> dict[str, Tag]:
            calls.append(1)
            return {} if len(calls) == 1 else original(self, names)

        monkeypatch.setattr(NoteRepository, "_existing_tags", _stale_once)
        return calls

    async def test_create_recovers_from_unique_violation(
        self, client: AsyncClient, db_session: Session, stale_lookup: list[int]
    ) -> None:
        # Seed the "other writer's" tag without going through the patched lookup.
        db_session.add(Tag(name="iş"))
        db_session.commit()

        with capture_sql() as sql:
            resp = await client.post("/notes", json={"title": "x", "tags": ["İş", "ev"]})

        assert resp.status_code == 201, resp.text
        assert resp.json()["tags"] == ["ev", "iş"]
        assert stale_lookup == [1]
        # Both INSERTs were attempted: "iş" hit UNIQUE and was re-read, "ev" was created.
        assert len([s for s in sql if s.lstrip().upper().startswith("INSERT INTO TAGS")]) == 2
        assert _tag_rows(db_session) == ["ev", "iş"]
        assert _link_count(db_session, resp.json()["id"]) == 2

    async def test_patch_recovers_from_unique_violation(
        self, client: AsyncClient, db_session: Session, stale_lookup: list[int]
    ) -> None:
        db_session.add(Tag(name="iş"))
        db_session.add(Note(title="x"))
        db_session.commit()
        note_id = db_session.scalar(select(Note.id))

        resp = await client.patch(f"/notes/{note_id}", json={"tags": ["İŞ"]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["tags"] == ["iş"]
        assert stale_lookup == [1]
        assert _tag_rows(db_session) == ["iş"]


# --- filters and ordering --------------------------------------------------------


class TestListFilters:
    async def test_favorite_filter(self, client: AsyncClient) -> None:
        fav = await make_note(client, "F", is_favorite=True)
        plain = await make_note(client, "P")
        assert [n["id"] for n in await list_notes(client, favorite="true")] == [fav["id"]]
        assert [n["id"] for n in await list_notes(client, favorite="false")] == [plain["id"]]
        assert {n["id"] for n in await list_notes(client)} == {fav["id"], plain["id"]}

    async def test_tag_filter_matches_turkish_case_insensitively(
        self, client: AsyncClient
    ) -> None:
        work = await make_note(client, "İş notu", tags=["iş"])
        await make_note(client, "Diğer", tags=["is"])
        for query in ["İŞ", "iş", " İş ", DECOMPOSED_I + "Ş"]:
            assert [n["id"] for n in await list_notes(client, tag=query)] == [work["id"]], query

    async def test_tag_filter_ısparta(self, client: AsyncClient) -> None:
        note = await make_note(client, tags=["ısparta"])
        assert [n["id"] for n in await list_notes(client, tag="ISPARTA")] == [note["id"]]

    async def test_tag_and_favorite_combined(self, client: AsyncClient) -> None:
        both = await make_note(client, "A", tags=["iş"], is_favorite=True)
        await make_note(client, "B", tags=["iş"])
        await make_note(client, "C", is_favorite=True)
        result = await list_notes(client, tag="iş", favorite="true")
        assert [n["id"] for n in result] == [both["id"]]

    async def test_unknown_tag_returns_empty(self, client: AsyncClient) -> None:
        await make_note(client, tags=["a"])
        assert await list_notes(client, tag="yok") == []

    @pytest.mark.parametrize("tag", ["a,b", "   ", "x" * 41])
    async def test_invalid_tag_filter_422(self, client: AsyncClient, tag: str) -> None:
        assert (await client.get("/notes", params={"tag": tag})).status_code == 422

    async def test_list_includes_body_and_tags(self, client: AsyncClient) -> None:
        await make_note(client, "A", body="gövde", tags=["b", "a"])
        [note] = await list_notes(client)
        assert (note["body"], note["tags"]) == ("gövde", ["a", "b"])

    async def test_ordered_by_updated_at_desc_then_id_desc(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        a = await make_note(client, "A")
        b = await make_note(client, "B")
        c = await make_note(client, "C")
        _set_updated_at(db_session, a["id"], datetime(2026, 1, 3))
        _set_updated_at(db_session, b["id"], datetime(2026, 1, 1))
        _set_updated_at(db_session, c["id"], datetime(2026, 1, 1))
        assert [n["id"] for n in await list_notes(client)] == [a["id"], c["id"], b["id"]]


# --- /tags --------------------------------------------------------------------


class TestTagCounts:
    async def test_counts_only_non_deleted_and_hides_unused(self, client: AsyncClient) -> None:
        await make_note(client, "A", tags=["iş", "ev"])
        await make_note(client, "B", tags=["İş"])
        deleted = await make_note(client, "C", tags=["ev", "silinen"])
        unused = await make_note(client, "D", tags=["eski"])
        assert (await client.delete(f"/notes/{deleted['id']}")).status_code == 204
        await client.patch(f"/notes/{unused['id']}", json={"tags": []})

        resp = await client.get("/tags")
        assert resp.status_code == 200
        assert resp.json() == [
            {"name": "ev", "note_count": 1},
            {"name": "iş", "note_count": 2},
        ]

    async def test_no_notes_empty_list(self, client: AsyncClient) -> None:
        assert (await client.get("/tags")).json() == []


# --- soft delete ----------------------------------------------------------------


class TestSoftDelete:
    async def test_deleted_note_hidden_but_row_and_tags_kept(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        note = await make_note(client, "A", tags=["iş", "ev"])
        keep = await make_note(client, "B")
        assert (await client.delete(f"/notes/{note['id']}")).status_code == 204

        assert (await client.get(f"/notes/{note['id']}")).status_code == 404
        assert (await client.patch(f"/notes/{note['id']}", json={"title": "x"})).status_code == 404
        assert (await client.delete(f"/notes/{note['id']}")).status_code == 404
        assert [n["id"] for n in await list_notes(client)] == [keep["id"]]
        assert await list_notes(client, tag="iş") == []

        db_session.expire_all()
        row = db_session.get(Note, note["id"])
        assert row is not None and row.deleted_at is not None
        assert _link_count(db_session, note["id"]) == 2
        assert _tag_rows(db_session) == ["ev", "iş"]


# --- note of the day -------------------------------------------------------------


class TestNoteOfTheDay:
    DAY = date(2026, 11, 19)

    async def test_same_day_same_note(self, client: AsyncClient) -> None:
        for title in "ABCDE":
            await make_note(client, title)
        first = await random_note(client, self.DAY)
        second = await random_note(client, self.DAY)
        assert first is not None and first == second

    async def test_index_is_ordinal_mod_n_over_id_order(self, client: AsyncClient) -> None:
        ids = [(await make_note(client, t))["id"] for t in "ABCD"]
        for offset in range(8):
            day = self.DAY + timedelta(days=offset)
            assert (await random_note(client, day))["id"] == ids[day.toordinal() % 4]

    async def test_consecutive_n_days_visit_all_n_notes(self, client: AsyncClient) -> None:
        ids = {(await make_note(client, t))["id"] for t in "ABC"}
        seen = {(await random_note(client, self.DAY + timedelta(days=i)))["id"] for i in range(3)}
        assert seen == ids

    async def test_single_note_always_returned(self, client: AsyncClient) -> None:
        only = await make_note(client, "Tek", tags=["a"])
        for offset in (0, 1, 2, 365):
            assert await random_note(client, self.DAY + timedelta(days=offset)) == only

    async def test_no_notes_returns_null(self, client: AsyncClient) -> None:
        resp = await client.get("/notes/random", params={"day": "2026-11-19"})
        assert resp.status_code == 200
        assert resp.json() is None

    async def test_deleted_note_never_picked(self, client: AsyncClient) -> None:
        gone = await make_note(client, "Silinecek")
        kept = await make_note(client, "Kalan")
        await client.delete(f"/notes/{gone['id']}")
        for offset in range(5):
            assert (await random_note(client, self.DAY + timedelta(days=offset)))["id"] == kept["id"]

    async def test_all_deleted_returns_null(self, client: AsyncClient) -> None:
        note = await make_note(client)
        await client.delete(f"/notes/{note['id']}")
        assert await random_note(client, self.DAY) is None

    @pytest.mark.parametrize("day", ["2000-01-01", "2100-12-31"])
    async def test_day_bounds_inclusive(self, client: AsyncClient, day: str) -> None:
        await make_note(client)
        assert (await client.get("/notes/random", params={"day": day})).status_code == 200

    @pytest.mark.parametrize("day", ["1999-12-31", "2101-01-01", "2026-13-01", "bugün"])
    async def test_invalid_day_422(self, client: AsyncClient, day: str) -> None:
        assert (await client.get("/notes/random", params={"day": day})).status_code == 422

    async def test_missing_day_422(self, client: AsyncClient) -> None:
        assert (await client.get("/notes/random")).status_code == 422


# --- query shape: no writes on GET, no N+1 ------------------------------------------


class TestQueryShape:
    async def test_get_endpoints_do_not_write(self, client: AsyncClient) -> None:
        note = await make_note(client, "A", tags=["iş"], is_favorite=True)
        with capture_sql() as sql:
            for url, params in [
                ("/notes", {}),
                ("/notes", {"favorite": "true", "tag": "İŞ"}),
                (f"/notes/{note['id']}", {}),
                ("/notes/random", {"day": "2026-11-19"}),
                ("/tags", {}),
            ]:
                assert (await client.get(url, params=params)).status_code == 200
        assert _selects(sql)
        assert _writes(sql) == []

    async def test_list_select_count_independent_of_note_count(
        self, client: AsyncClient
    ) -> None:
        await make_note(client, "N0", tags=["a", "b"])
        with capture_sql() as sql_one:
            assert len(await list_notes(client)) == 1

        for i in range(1, 10):
            await make_note(client, f"N{i}", tags=[f"t{i}", "a"])
        with capture_sql() as sql_ten:
            notes = await list_notes(client)
        assert len(notes) == 10 and all(n["tags"] for n in notes)

        assert len(_selects(sql_one)) == len(_selects(sql_ten))


# --- auth -----------------------------------------------------------------------


class TestAuth:
    @pytest.mark.parametrize(
        ("method", "url"),
        [
            ("get", "/notes"),
            ("post", "/notes"),
            ("get", "/notes/random?day=2026-11-19"),
            ("get", "/notes/1"),
            ("patch", "/notes/1"),
            ("delete", "/notes/1"),
            ("get", "/tags"),
        ],
    )
    async def test_requires_token(
        self, client_no_auth: AsyncClient, method: str, url: str
    ) -> None:
        kwargs = {"json": {"title": "x"}} if method in ("post", "patch") else {}
        resp = await getattr(client_no_auth, method)(url, **kwargs)
        assert resp.status_code == 401
