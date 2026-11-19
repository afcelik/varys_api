"""HTTP-level tests for projects: CRUD, active limit, progress, events and task linking."""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import get_db
from app.main import create_app
from app.models.base import Base
from app.models.project import Project
from app.models.task import Task

# In-memory SQLite, one shared connection (same setup as tests/test_tasks.py).
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

LIMIT_DETAIL = "active_project_limit_reached"
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


def _selects(statements: list[str]) -> list[str]:
    return [s for s in statements if s.lstrip().upper().startswith("SELECT")]


async def make_project(client: AsyncClient, title: str = "P", **fields) -> dict:
    resp = await client.post("/projects", json={"title": title, **fields})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def make_task(client: AsyncClient, title: str = "T", **fields) -> dict:
    resp = await client.post("/tasks", json={"title": title, **fields})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def events_of(client: AsyncClient, project_id: int) -> list[dict]:
    resp = await client.get(f"/projects/{project_id}/events")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def task_events_of(client: AsyncClient, task_id: int) -> list[dict]:
    resp = await client.get(f"/tasks/{task_id}/events")
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- CRUD --------------------------------------------------------------------


class TestProjectCrud:
    async def test_create_returns_all_fields_with_defaults(self, client: AsyncClient) -> None:
        body = await make_project(
            client,
            "Kitap",
            description="Taslak",
            start_date="2026-01-01",
            target_date="2026-03-01",
        )
        assert body["title"] == "Kitap"
        assert body["description"] == "Taslak"
        assert body["status"] == "active"
        assert body["start_date"] == "2026-01-01"
        assert body["target_date"] == "2026-03-01"
        assert body["total_tasks"] == 0
        assert body["done_tasks"] == 0
        assert body["progress_percent"] is None
        assert isinstance(body["id"], int)

    async def test_create_minimal_has_null_optionals(self, client: AsyncClient) -> None:
        body = await make_project(client, "Yalın")
        assert body["description"] is None
        assert body["start_date"] is None
        assert body["target_date"] is None

    @pytest.mark.parametrize(
        "payload",
        [
            {"title": ""},
            {"title": "x" * 256},
            {"title": "P", "description": "x" * 2001},
            {"title": "P", "status": "archived"},
            {"title": "P", "status": None},
            {"title": None},
            {},
        ],
    )
    async def test_create_rejects_invalid_input(self, client: AsyncClient, payload: dict) -> None:
        resp = await client.post("/projects", json=payload)
        assert resp.status_code == 422

    async def test_create_target_before_start_is_422(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/projects",
            json={"title": "P", "start_date": "2026-02-01", "target_date": "2026-01-31"},
        )
        assert resp.status_code == 422

    async def test_create_same_start_and_target_is_allowed(self, client: AsyncClient) -> None:
        body = await make_project(client, start_date="2026-02-01", target_date="2026-02-01")
        assert body["target_date"] == "2026-02-01"

    async def test_get_project(self, client: AsyncClient) -> None:
        created = await make_project(client, "Bul")
        resp = await client.get(f"/projects/{created['id']}")
        assert resp.status_code == 200
        assert resp.json() == created

    async def test_get_missing_project_is_404(self, client: AsyncClient) -> None:
        assert (await client.get("/projects/999")).status_code == 404

    async def test_list_filters_by_status(self, client: AsyncClient) -> None:
        a = await make_project(client, "A")
        b = await make_project(client, "B", status="on_hold")
        c = await make_project(client, "C", status="someday")

        all_ids = [p["id"] for p in (await client.get("/projects")).json()]
        assert all_ids == [a["id"], b["id"], c["id"]]

        resp = await client.get("/projects", params={"status": "on_hold"})
        assert [p["id"] for p in resp.json()] == [b["id"]]

    async def test_list_with_invalid_status_is_422(self, client: AsyncClient) -> None:
        assert (await client.get("/projects", params={"status": "nope"})).status_code == 422

    async def test_patch_updates_sent_fields_and_keeps_unsent(self, client: AsyncClient) -> None:
        created = await make_project(
            client, "Eski", description="d", start_date="2026-01-01", target_date="2026-02-01"
        )
        resp = await client.patch(f"/projects/{created['id']}", json={"title": "Yeni"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Yeni"
        assert body["description"] == "d"
        assert body["status"] == "active"
        assert body["start_date"] == "2026-01-01"
        assert body["target_date"] == "2026-02-01"

    @pytest.mark.parametrize("field", ["description", "start_date", "target_date"])
    async def test_patch_explicit_null_clears(self, client: AsyncClient, field: str) -> None:
        created = await make_project(
            client, "P", description="d", start_date="2026-01-01", target_date="2026-02-01"
        )
        resp = await client.patch(f"/projects/{created['id']}", json={field: None})
        assert resp.status_code == 200
        assert resp.json()[field] is None
        stored = (await client.get(f"/projects/{created['id']}")).json()
        assert stored[field] is None
        others = {"description", "start_date", "target_date"} - {field}
        assert all(stored[name] == created[name] for name in others)

    @pytest.mark.parametrize("field", ["title", "status"])
    async def test_patch_null_on_required_is_422(self, client: AsyncClient, field: str) -> None:
        created = await make_project(client, "P")
        resp = await client.patch(f"/projects/{created['id']}", json={field: None})
        assert resp.status_code == 422
        stored = (await client.get(f"/projects/{created['id']}")).json()
        assert stored["title"] == "P"
        assert stored["status"] == "active"

    async def test_patch_target_before_stored_start_is_422(self, client: AsyncClient) -> None:
        created = await make_project(client, "P", start_date="2026-03-01")
        resp = await client.patch(f"/projects/{created['id']}", json={"target_date": "2026-02-01"})
        assert resp.status_code == 422
        assert (await client.get(f"/projects/{created['id']}")).json()["target_date"] is None

    async def test_patch_start_after_stored_target_is_422(self, client: AsyncClient) -> None:
        created = await make_project(client, "P", target_date="2026-02-01")
        resp = await client.patch(f"/projects/{created['id']}", json={"start_date": "2026-03-01"})
        assert resp.status_code == 422

    async def test_patch_clearing_start_makes_any_target_valid(self, client: AsyncClient) -> None:
        created = await make_project(client, "P", start_date="2026-03-01", target_date="2026-04-01")
        resp = await client.patch(
            f"/projects/{created['id']}", json={"start_date": None, "target_date": "2026-01-01"}
        )
        assert resp.status_code == 200
        assert resp.json()["target_date"] == "2026-01-01"

    async def test_patch_missing_project_is_404(self, client: AsyncClient) -> None:
        assert (await client.patch("/projects/999", json={"title": "X"})).status_code == 404

    async def test_delete_soft_deletes(self, client: AsyncClient, db_session: Session) -> None:
        created = await make_project(client, "Sil")
        pid = created["id"]
        assert (await client.delete(f"/projects/{pid}")).status_code == 204

        assert (await client.get(f"/projects/{pid}")).status_code == 404
        assert (await client.patch(f"/projects/{pid}", json={"title": "X"})).status_code == 404
        assert (await client.get(f"/projects/{pid}/events")).status_code == 404
        assert (await client.delete(f"/projects/{pid}")).status_code == 404
        assert (await client.get("/projects")).json() == []

        row = db_session.get(Project, pid)
        assert row is not None and row.deleted_at is not None

    async def test_delete_missing_project_is_404(self, client: AsyncClient) -> None:
        assert (await client.delete("/projects/999")).status_code == 404


# --- active limit ------------------------------------------------------------


class TestActiveLimit:
    async def _fill(self, client: AsyncClient, n: int = 3) -> list[dict]:
        return [await make_project(client, f"A{i}") for i in range(n)]

    async def test_creating_active_beyond_limit_is_409(self, client: AsyncClient) -> None:
        await self._fill(client)
        resp = await client.post("/projects", json={"title": "Fazla"})
        assert resp.status_code == 409
        assert resp.json() == {"detail": LIMIT_DETAIL}
        assert len((await client.get("/projects")).json()) == 3

    @pytest.mark.parametrize("status", ["on_hold", "someday", "completed"])
    async def test_creating_non_active_at_limit_is_201(
        self, client: AsyncClient, status: str
    ) -> None:
        await self._fill(client)
        body = await make_project(client, "Bekle", status=status)
        assert body["status"] == status

    async def test_activating_from_on_hold_at_limit_is_409(self, client: AsyncClient) -> None:
        await self._fill(client)
        held = await make_project(client, "Bekle", status="on_hold")
        resp = await client.patch(f"/projects/{held['id']}", json={"status": "active"})
        assert resp.status_code == 409
        assert resp.json() == {"detail": LIMIT_DETAIL}
        assert (await client.get(f"/projects/{held['id']}")).json()["status"] == "on_hold"
        assert [e["event_type"] for e in await events_of(client, held["id"])] == ["created"]

    async def test_completed_project_frees_a_slot(self, client: AsyncClient) -> None:
        active = await self._fill(client)
        resp = await client.patch(f"/projects/{active[0]['id']}", json={"status": "completed"})
        assert resp.status_code == 200
        body = await make_project(client, "Yeni")
        assert body["status"] == "active"

    async def test_deleted_active_project_frees_a_slot(self, client: AsyncClient) -> None:
        active = await self._fill(client)
        assert (await client.delete(f"/projects/{active[0]['id']}")).status_code == 204
        await make_project(client, "Yeni")

    async def test_reactivating_itself_at_limit_is_not_409(self, client: AsyncClient) -> None:
        active = await self._fill(client)
        pid = active[0]["id"]
        resp = await client.patch(f"/projects/{pid}", json={"status": "active", "title": "Aynı"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"
        assert resp.json()["title"] == "Aynı"

    async def test_editing_active_project_at_limit_is_allowed(self, client: AsyncClient) -> None:
        active = await self._fill(client)
        resp = await client.patch(f"/projects/{active[1]['id']}", json={"description": "x"})
        assert resp.status_code == 200

    async def test_limit_follows_setting(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "MAX_ACTIVE_PROJECTS", 2)
        await self._fill(client, 2)
        assert (await client.post("/projects", json={"title": "3."})).status_code == 409
        held = await make_project(client, "Bekle", status="someday")
        resp = await client.patch(f"/projects/{held['id']}", json={"status": "active"})
        assert resp.status_code == 409
        limits = (await client.get("/projects/limits")).json()
        assert limits == {"max_active": 2, "active_count": 2}

    async def test_limits_endpoint_counts_only_active(self, client: AsyncClient) -> None:
        assert (await client.get("/projects/limits")).json() == {"max_active": 3, "active_count": 0}
        a = await make_project(client, "A")
        await make_project(client, "B")
        await make_project(client, "C", status="on_hold")
        await make_project(client, "D", status="completed")
        assert (await client.get("/projects/limits")).json() == {"max_active": 3, "active_count": 2}
        await client.patch(f"/projects/{a['id']}", json={"status": "someday"})
        assert (await client.get("/projects/limits")).json() == {"max_active": 3, "active_count": 1}


# --- events ------------------------------------------------------------------


class TestProjectEvents:
    async def test_create_writes_created_event(self, client: AsyncClient) -> None:
        created = await make_project(client, "Olay", status="someday")
        events = await events_of(client, created["id"])
        assert len(events) == 1
        assert events[0]["event_type"] == "created"
        assert events[0]["project_id"] == created["id"]
        assert events[0]["payload"] == {"title": "Olay", "status": "someday"}

    async def test_status_change_writes_old_and_new(self, client: AsyncClient) -> None:
        created = await make_project(client, "P")
        await client.patch(f"/projects/{created['id']}", json={"status": "on_hold"})
        events = await events_of(client, created["id"])
        assert [e["event_type"] for e in events] == ["created", "status_changed"]
        assert events[1]["payload"] == {"old_status": "active", "new_status": "on_hold"}

    async def test_same_status_writes_no_event(self, client: AsyncClient) -> None:
        created = await make_project(client, "P")
        resp = await client.patch(f"/projects/{created['id']}", json={"status": "active"})
        assert resp.status_code == 200
        assert [e["event_type"] for e in await events_of(client, created["id"])] == ["created"]

    async def test_edit_writes_changed_fields_only(self, client: AsyncClient) -> None:
        created = await make_project(client, "P", description="d", start_date="2026-01-01")
        await client.patch(
            f"/projects/{created['id']}",
            json={
                "title": "P",
                "description": "yeni",
                "start_date": "2026-01-01",
                "target_date": "2026-05-01",
            },
        )
        events = await events_of(client, created["id"])
        assert [e["event_type"] for e in events] == ["created", "edited"]
        assert events[1]["payload"] == {"fields_changed": ["description", "target_date"]}

    async def test_clearing_a_field_is_an_edit(self, client: AsyncClient) -> None:
        created = await make_project(client, "P", start_date="2026-01-01")
        await client.patch(f"/projects/{created['id']}", json={"start_date": None})
        events = await events_of(client, created["id"])
        assert events[-1]["payload"] == {"fields_changed": ["start_date"]}

    async def test_same_values_write_no_event(self, client: AsyncClient) -> None:
        created = await make_project(client, "P", description="d", target_date="2026-02-01")
        resp = await client.patch(
            f"/projects/{created['id']}",
            json={"title": "P", "description": "d", "target_date": "2026-02-01", "status": "active"},
        )
        assert resp.status_code == 200
        assert [e["event_type"] for e in await events_of(client, created["id"])] == ["created"]

    async def test_status_and_edit_together_write_both(self, client: AsyncClient) -> None:
        created = await make_project(client, "P")
        await client.patch(
            f"/projects/{created['id']}", json={"status": "completed", "title": "Bitti"}
        )
        events = await events_of(client, created["id"])
        assert [e["event_type"] for e in events] == ["created", "status_changed", "edited"]
        assert events[2]["payload"] == {"fields_changed": ["title"]}

    async def test_rejected_patch_writes_no_event(self, client: AsyncClient) -> None:
        created = await make_project(client, "P", start_date="2026-03-01")
        await client.patch(f"/projects/{created['id']}", json={"target_date": "2026-01-01"})
        assert [e["event_type"] for e in await events_of(client, created["id"])] == ["created"]


# --- read-only GETs, query count, auth ---------------------------------------


class TestReadsAndAuth:
    async def test_get_endpoints_do_not_write(self, client: AsyncClient) -> None:
        p = await make_project(client, "P")
        with capture_sql() as sql:
            for url in (
                "/projects",
                "/projects?status=active",
                f"/projects/{p['id']}",
                f"/projects/{p['id']}/events",
                "/projects/limits",
            ):
                assert (await client.get(url)).status_code == 200
        assert _selects(sql), "expected the GETs to run SELECTs"
        assert _writes(sql) == []

    async def test_list_runs_constant_number_of_selects(self, client: AsyncClient) -> None:
        await make_project(client, "P0")
        with capture_sql() as one:
            assert len((await client.get("/projects")).json()) == 1
        for i in range(1, 10):
            await make_project(client, f"P{i}", status="someday")
        with capture_sql() as ten:
            assert len((await client.get("/projects")).json()) == 10
        assert len(_selects(ten)) == len(_selects(one))
        assert len(_selects(ten)) <= 2

    @pytest.mark.parametrize(
        ("method", "url"),
        [
            ("POST", "/projects"),
            ("GET", "/projects"),
            ("GET", "/projects/limits"),
            ("GET", "/projects/1"),
            ("PATCH", "/projects/1"),
            ("DELETE", "/projects/1"),
            ("GET", "/projects/1/events"),
        ],
    )
    async def test_requires_token(self, client_no_auth: AsyncClient, method: str, url: str) -> None:
        json = {"title": "P"} if method in ("POST", "PATCH") else None
        resp = await client_no_auth.request(method, url, json=json)
        assert resp.status_code == 401


# --- progress ----------------------------------------------------------------


class TestProgress:
    async def _progress(self, client: AsyncClient, pid: int) -> tuple[int, int, int | None]:
        body = (await client.get(f"/projects/{pid}")).json()
        return body["total_tasks"], body["done_tasks"], body["progress_percent"]

    async def test_no_tasks_is_null(self, client: AsyncClient) -> None:
        p = await make_project(client)
        assert await self._progress(client, p["id"]) == (0, 0, None)

    async def test_four_tasks_one_done_is_25(self, client: AsyncClient) -> None:
        p = await make_project(client)
        await make_task(client, "a", project_id=p["id"], status="done")
        for name in ("b", "c"):
            await make_task(client, name, project_id=p["id"])
        await make_task(client, "d", project_id=p["id"], status="in_progress")
        assert await self._progress(client, p["id"]) == (4, 1, 25)
        listed = (await client.get("/projects")).json()[0]
        assert (listed["total_tasks"], listed["done_tasks"], listed["progress_percent"]) == (4, 1, 25)

    @pytest.mark.parametrize(
        ("done", "total", "expected"),
        [
            (1, 3, 33),
            (2, 3, 67),
            # 12.5 -> 12: Python's round() is banker's rounding; this is deliberate.
            (1, 8, 12),
            (0, 2, 0),
            (2, 2, 100),
        ],
    )
    async def test_rounding(
        self, client: AsyncClient, done: int, total: int, expected: int
    ) -> None:
        p = await make_project(client)
        for i in range(total):
            await make_task(client, f"t{i}", project_id=p["id"], status="done" if i < done else "todo")
        assert await self._progress(client, p["id"]) == (total, done, expected)

    async def test_deleted_task_is_not_counted(self, client: AsyncClient) -> None:
        p = await make_project(client)
        done = await make_task(client, "bitti", project_id=p["id"], status="done")
        await make_task(client, "açık", project_id=p["id"])
        assert await self._progress(client, p["id"]) == (2, 1, 50)
        assert (await client.delete(f"/tasks/{done['id']}")).status_code == 204
        assert await self._progress(client, p["id"]) == (1, 0, 0)

    async def test_unlinking_and_moving_update_counts(self, client: AsyncClient) -> None:
        a = await make_project(client, "A")
        b = await make_project(client, "B")
        t1 = await make_task(client, "1", project_id=a["id"], status="done")
        t2 = await make_task(client, "2", project_id=a["id"])
        assert await self._progress(client, a["id"]) == (2, 1, 50)

        await client.patch(f"/tasks/{t2['id']}", json={"project_id": None})
        assert await self._progress(client, a["id"]) == (1, 1, 100)

        await client.patch(f"/tasks/{t1['id']}", json={"project_id": b["id"]})
        assert await self._progress(client, a["id"]) == (0, 0, None)
        assert await self._progress(client, b["id"]) == (1, 1, 100)

    async def test_patch_response_carries_progress(self, client: AsyncClient) -> None:
        p = await make_project(client)
        await make_task(client, "x", project_id=p["id"], status="done")
        body = (await client.patch(f"/projects/{p['id']}", json={"title": "Y"})).json()
        assert (body["total_tasks"], body["done_tasks"], body["progress_percent"]) == (1, 1, 100)


# --- task linking ------------------------------------------------------------


class TestTaskLinking:
    async def test_task_read_has_null_project_by_default(self, client: AsyncClient) -> None:
        task = await make_task(client, "bağsız")
        assert task["project_id"] is None

    async def test_create_task_with_project(self, client: AsyncClient) -> None:
        p = await make_project(client)
        task = await make_task(client, "bağlı", project_id=p["id"])
        assert task["project_id"] == p["id"]
        assert (await client.get(f"/tasks/{task['id']}")).json()["project_id"] == p["id"]

    async def test_create_task_with_missing_project_is_404(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        resp = await client.post("/tasks", json={"title": "x", "project_id": 999})
        assert resp.status_code == 404
        assert db_session.scalars(select(Task)).all() == []

    async def test_create_task_with_deleted_project_is_404(self, client: AsyncClient) -> None:
        p = await make_project(client)
        await client.delete(f"/projects/{p['id']}")
        resp = await client.post("/tasks", json={"title": "x", "project_id": p["id"]})
        assert resp.status_code == 404

    @pytest.mark.parametrize("status", ["completed", "on_hold", "someday"])
    async def test_linking_to_non_active_project_is_allowed(
        self, client: AsyncClient, status: str
    ) -> None:
        # Deliberate: only existence matters, not the project's status.
        p = await make_project(client, status=status)
        created = await make_task(client, "a", project_id=p["id"])
        assert created["project_id"] == p["id"]
        other = await make_task(client, "b")
        resp = await client.patch(f"/tasks/{other['id']}", json={"project_id": p["id"]})
        assert resp.status_code == 200
        assert resp.json()["project_id"] == p["id"]

    async def test_patch_assigns_and_writes_edited_event(self, client: AsyncClient) -> None:
        p = await make_project(client)
        task = await make_task(client)
        resp = await client.patch(f"/tasks/{task['id']}", json={"project_id": p["id"]})
        assert resp.status_code == 200
        assert resp.json()["project_id"] == p["id"]
        events = await task_events_of(client, task["id"])
        assert [e["event_type"] for e in events] == ["created", "edited"]
        assert events[1]["payload"] == {
            "fields_changed": ["project_id"],
            "old_project_id": None,
            "new_project_id": p["id"],
        }

    async def test_patch_explicit_null_clears_link(self, client: AsyncClient) -> None:
        p = await make_project(client)
        task = await make_task(client, project_id=p["id"])
        resp = await client.patch(f"/tasks/{task['id']}", json={"project_id": None})
        assert resp.status_code == 200
        assert resp.json()["project_id"] is None
        assert (await client.get(f"/tasks/{task['id']}")).json()["project_id"] is None
        events = await task_events_of(client, task["id"])
        assert events[-1]["payload"] == {
            "fields_changed": ["project_id"],
            "old_project_id": p["id"],
            "new_project_id": None,
        }

    async def test_patch_without_project_id_keeps_link(self, client: AsyncClient) -> None:
        p = await make_project(client)
        task = await make_task(client, project_id=p["id"])
        resp = await client.patch(f"/tasks/{task['id']}", json={"title": "yeni ad"})
        assert resp.status_code == 200
        assert resp.json()["project_id"] == p["id"]

    async def test_patch_same_project_writes_no_event(self, client: AsyncClient) -> None:
        p = await make_project(client)
        task = await make_task(client, project_id=p["id"])
        resp = await client.patch(f"/tasks/{task['id']}", json={"project_id": p["id"]})
        assert resp.status_code == 200
        assert [e["event_type"] for e in await task_events_of(client, task["id"])] == ["created"]

    async def test_patch_to_missing_or_deleted_project_is_404(self, client: AsyncClient) -> None:
        p = await make_project(client)
        gone = await make_project(client, "gone")
        await client.delete(f"/projects/{gone['id']}")
        task = await make_task(client, project_id=p["id"])
        for target in (999, gone["id"]):
            resp = await client.patch(f"/tasks/{task['id']}", json={"project_id": target})
            assert resp.status_code == 404
        assert (await client.get(f"/tasks/{task['id']}")).json()["project_id"] == p["id"]
        assert [e["event_type"] for e in await task_events_of(client, task["id"])] == ["created"]

    async def test_filter_tasks_by_project_and_status(self, client: AsyncClient) -> None:
        a = await make_project(client, "A")
        b = await make_project(client, "B")
        a_todo = await make_task(client, "a1", project_id=a["id"])
        a_done = await make_task(client, "a2", project_id=a["id"], status="done")
        await make_task(client, "b1", project_id=b["id"], status="done")
        await make_task(client, "none")

        resp = await client.get("/tasks", params={"project_id": a["id"]})
        assert {t["id"] for t in resp.json()} == {a_todo["id"], a_done["id"]}

        resp = await client.get("/tasks", params={"project_id": a["id"], "status": "done"})
        assert [t["id"] for t in resp.json()] == [a_done["id"]]

        assert len((await client.get("/tasks")).json()) == 4

    async def test_task_filter_get_does_not_write(self, client: AsyncClient) -> None:
        p = await make_project(client)
        await make_task(client, project_id=p["id"])
        with capture_sql() as sql:
            resp = await client.get("/tasks", params={"project_id": p["id"]})
            assert resp.status_code == 200
        assert _selects(sql)
        assert _writes(sql) == []


# --- project deletion unlinks tasks -------------------------------------------


class TestProjectDeleteUnlinksTasks:
    async def test_soft_delete_unlinks_tasks_in_service(
        self, client: AsyncClient, db_session: Session
    ) -> None:
        p = await make_project(client)
        t1 = await make_task(client, "1", project_id=p["id"])
        t2 = await make_task(client, "2", project_id=p["id"], status="done")
        other = await make_project(client, "O")
        t3 = await make_task(client, "3", project_id=other["id"])

        assert (await client.delete(f"/projects/{p['id']}")).status_code == 204

        # The project row still exists (soft delete), so FK ON DELETE SET NULL never fired:
        # the service is what cleared project_id.
        db_session.expire_all()
        row = db_session.get(Project, p["id"])
        assert row is not None and row.deleted_at is not None

        for t in (t1, t2):
            body = (await client.get(f"/tasks/{t['id']}")).json()
            assert body["project_id"] is None
            assert body["deleted_at"] is None
            events = await task_events_of(client, t["id"])
            assert events[-1]["event_type"] == "edited"
            assert events[-1]["payload"] == {
                "fields_changed": ["project_id"],
                "old_project_id": p["id"],
                "new_project_id": None,
                "reason": "project_deleted",
            }

        # Tasks are not deleted; tasks of other projects are untouched.
        assert len((await client.get("/tasks")).json()) == 3
        assert (await client.get(f"/tasks/{t3['id']}")).json()["project_id"] == other["id"]
        assert [e["event_type"] for e in await task_events_of(client, t3["id"])] == ["created"]
        assert (await client.get("/tasks", params={"project_id": p["id"]})).json() == []

    async def test_deleted_tasks_are_left_alone(self, client: AsyncClient) -> None:
        p = await make_project(client)
        t = await make_task(client, project_id=p["id"])
        await client.delete(f"/tasks/{t['id']}")
        await client.delete(f"/projects/{p['id']}")
        events = await task_events_of(client, t["id"])
        assert [e["event_type"] for e in events] == ["created", "deleted"]
