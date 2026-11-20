"""Eisenhower flags (is_important / is_urgent) on tasks: create, patch, events, responses."""

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
from app.models.task import Task

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


def _configure_test_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


event.listen(test_engine, "connect", _configure_test_connection)

TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

FLAGS = ("is_important", "is_urgent")


def _token() -> str:
    now = datetime.now(UTC)
    payload = {"sub": "user", "exp": now + timedelta(days=1), "iat": now}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


@pytest.fixture(scope="function")
def db_session() -> Session:
    Base.metadata.create_all(bind=test_engine)
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
async def client(db_session: Session) -> AsyncClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {_token()}"},
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


async def _create(client: AsyncClient, **extra) -> dict:
    body = {
        "title": "Rapor yaz",
        "description": "Q4 raporu",
        "status": "in_progress",
        "due_date": "2026-11-25T00:00:00",
        "planned_date": "2026-11-21T00:00:00",
        **extra,
    }
    response = await client.post("/tasks", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _events(client: AsyncClient, task_id: int, event_type: str = "edited") -> list[dict]:
    response = await client.get(f"/tasks/{task_id}/events")
    assert response.status_code == 200
    return [e for e in response.json() if e["event_type"] == event_type]


def _db_flags(db_session: Session, task_id: int) -> tuple[bool, bool]:
    """Read flags straight from the DB, bypassing the identity map."""
    db_session.expire_all()
    task = db_session.scalars(select(Task).where(Task.id == task_id)).one()
    return task.is_important, task.is_urgent


# --- create -----------------------------------------------------------------


async def test_create_defaults_both_false(client: AsyncClient, db_session: Session) -> None:
    created = await _create(client)
    assert created["is_important"] is False
    assert created["is_urgent"] is False
    assert _db_flags(db_session, created["id"]) == (False, False)


@pytest.mark.parametrize(
    ("important", "urgent"), [(True, False), (False, True), (True, True), (False, False)]
)
async def test_create_with_flag_combinations(
    client: AsyncClient, db_session: Session, important: bool, urgent: bool
) -> None:
    created = await _create(client, is_important=important, is_urgent=urgent)
    assert (created["is_important"], created["is_urgent"]) == (important, urgent)
    assert _db_flags(db_session, created["id"]) == (important, urgent)

    fetched = (await client.get(f"/tasks/{created['id']}")).json()
    assert (fetched["is_important"], fetched["is_urgent"]) == (important, urgent)


@pytest.mark.parametrize("flag", FLAGS)
async def test_create_explicit_null_flag_is_422(client: AsyncClient, flag: str) -> None:
    response = await client.post("/tasks", json={"title": "x", flag: None})
    assert response.status_code == 422
    assert (await client.get("/tasks")).json() == []


# --- patch ------------------------------------------------------------------


@pytest.mark.parametrize("flag", FLAGS)
async def test_patch_turns_flag_on_then_off(
    client: AsyncClient, db_session: Session, flag: str
) -> None:
    created = await _create(client)
    other = "is_urgent" if flag == "is_important" else "is_important"

    on = await client.patch(f"/tasks/{created['id']}", json={flag: True})
    assert on.status_code == 200
    assert on.json()[flag] is True
    assert on.json()[other] is False
    assert _db_flags(db_session, created["id"])[FLAGS.index(flag)] is True

    off = await client.patch(f"/tasks/{created['id']}", json={flag: False})
    assert off.status_code == 200
    assert off.json()[flag] is False
    assert _db_flags(db_session, created["id"]) == (False, False)


async def test_patch_unsent_flag_is_preserved(client: AsyncClient, db_session: Session) -> None:
    created = await _create(client, is_important=True, is_urgent=True)

    response = await client.patch(f"/tasks/{created['id']}", json={"is_urgent": False})
    assert response.status_code == 200
    assert response.json()["is_important"] is True
    assert response.json()["is_urgent"] is False

    # A patch touching neither flag keeps both.
    response = await client.patch(f"/tasks/{created['id']}", json={"title": "Yeni başlık"})
    assert response.status_code == 200
    assert response.json()["is_important"] is True
    assert response.json()["is_urgent"] is False
    assert _db_flags(db_session, created["id"]) == (True, False)


async def test_patch_flag_leaves_other_fields_untouched(client: AsyncClient) -> None:
    created = await _create(client)
    response = await client.patch(f"/tasks/{created['id']}", json={"is_important": True})
    assert response.status_code == 200
    body = response.json()
    for key in ("title", "description", "status", "due_date", "planned_date", "project_id",
                "is_urgent", "deleted_at", "created_at"):
        assert body[key] == created[key], key
    assert body["is_important"] is True


async def test_patch_null_is_important_is_422(client: AsyncClient, db_session: Session) -> None:
    created = await _create(client, is_important=True)
    response = await client.patch(f"/tasks/{created['id']}", json={"is_important": None})
    assert response.status_code == 422
    assert _db_flags(db_session, created["id"]) == (True, False)
    assert await _events(client, created["id"]) == []


async def test_patch_null_is_urgent_is_422(client: AsyncClient, db_session: Session) -> None:
    created = await _create(client, is_urgent=True)
    response = await client.patch(f"/tasks/{created['id']}", json={"is_urgent": None})
    assert response.status_code == 422
    assert _db_flags(db_session, created["id"]) == (False, True)
    assert await _events(client, created["id"]) == []


# --- events -----------------------------------------------------------------


@pytest.mark.parametrize("flag", FLAGS)
async def test_flag_change_writes_edited_event(client: AsyncClient, flag: str) -> None:
    created = await _create(client)
    await client.patch(f"/tasks/{created['id']}", json={flag: True})

    edited = await _events(client, created["id"])
    assert len(edited) == 1
    assert edited[0]["payload"] == {"fields_changed": [flag]}


@pytest.mark.parametrize("flag", FLAGS)
async def test_same_value_again_writes_no_event(client: AsyncClient, flag: str) -> None:
    created = await _create(client)
    await client.patch(f"/tasks/{created['id']}", json={flag: True})
    await client.patch(f"/tasks/{created['id']}", json={flag: True})
    assert len(await _events(client, created["id"])) == 1


async def test_sending_default_value_writes_no_event(client: AsyncClient) -> None:
    created = await _create(client)
    response = await client.patch(
        f"/tasks/{created['id']}", json={"is_important": False, "is_urgent": False}
    )
    assert response.status_code == 200
    assert await _events(client, created["id"]) == []


async def test_both_flags_changed_listed_in_one_event(client: AsyncClient) -> None:
    created = await _create(client)
    await client.patch(
        f"/tasks/{created['id']}", json={"is_important": True, "is_urgent": True}
    )
    edited = await _events(client, created["id"])
    assert len(edited) == 1
    assert sorted(edited[0]["payload"]["fields_changed"]) == ["is_important", "is_urgent"]
    assert set(edited[0]["payload"]) == {"fields_changed"}


async def test_only_actually_changed_flag_listed(client: AsyncClient) -> None:
    created = await _create(client, is_important=True)
    await client.patch(
        f"/tasks/{created['id']}", json={"is_important": True, "is_urgent": True}
    )
    edited = await _events(client, created["id"])
    assert len(edited) == 1
    assert edited[0]["payload"]["fields_changed"] == ["is_urgent"]


async def test_created_event_payload_unchanged(client: AsyncClient) -> None:
    created = await _create(client, is_important=True, is_urgent=True)
    created_events = await _events(client, created["id"], "created")
    assert len(created_events) == 1
    assert created_events[0]["payload"] == {"title": "Rapor yaz", "status": "in_progress"}


# --- other responses using TaskRead -----------------------------------------


async def test_task_list_includes_flags(client: AsyncClient) -> None:
    a = await _create(client, title="A", is_important=True)
    b = await _create(client, title="B", is_urgent=True)

    response = await client.get("/tasks")
    assert response.status_code == 200
    by_id = {t["id"]: t for t in response.json()}
    assert (by_id[a["id"]]["is_important"], by_id[a["id"]]["is_urgent"]) == (True, False)
    assert (by_id[b["id"]]["is_important"], by_id[b["id"]]["is_urgent"]) == (False, True)


async def test_daily_plan_tasks_and_focus_include_flags(client: AsyncClient) -> None:
    main = await _create(client, title="Ana", is_important=True, is_urgent=True)
    side = await _create(client, title="Yan", is_urgent=True)
    plain = await _create(client, title="Düz")

    put = await client.put(
        "/daily-plans/2026-11-21",
        json={"main_focus_task_id": main["id"], "side_focus_task_id": side["id"]},
    )
    assert put.status_code == 200

    response = await client.get("/daily-plans/2026-11-21")
    assert response.status_code == 200
    body = response.json()

    assert (body["main_focus"]["is_important"], body["main_focus"]["is_urgent"]) == (True, True)
    assert (body["side_focus"]["is_important"], body["side_focus"]["is_urgent"]) == (False, True)
    flags = {t["id"]: (t["is_important"], t["is_urgent"]) for t in body["tasks"]}
    assert flags == {
        main["id"]: (True, True),
        side["id"]: (False, True),
        plain["id"]: (False, False),
    }
