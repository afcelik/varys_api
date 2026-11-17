"""Tests for routines: schemas, repository, service (stage iii) and API (stage iv)."""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import get_db
from app.main import create_app
from app.models.base import Base
from app.models.routine import RecurrenceType, Routine, RoutineLog
from app.repositories.routine import RoutineRepository
from app.schemas.routine import RoutineCreate, RoutineUpdate
from app.services.routine import (
    RoutineNotFoundError,
    RoutineNotScheduledError,
    RoutineService,
    RoutineValidationError,
)

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

# 2024-01-01 is a Monday.
MON, TUE, WED = date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)


@pytest.fixture
def db_session() -> Iterator[Session]:
    Base.metadata.create_all(bind=test_engine)
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def repo(db_session: Session) -> RoutineRepository:
    return RoutineRepository(db_session)


@pytest.fixture
def service(repo: RoutineRepository) -> RoutineService:
    return RoutineService(repo)


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


def make(service: RoutineService, title: str = "R", **kwargs):
    data = {"recurrence_type": "daily", "start_date": MON, **kwargs}
    return service.create_routine(RoutineCreate(title=title, **data))


def count(db: Session, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


# --- Schemas ------------------------------------------------------------------------


class TestRoutineSchemas:
    def test_create_normalizes_days_of_week(self) -> None:
        r = RoutineCreate(title="x", recurrence_type="weekly", days_of_week=[4, 0, 4], start_date=MON)
        assert r.days_of_week == [0, 4]
        assert r.recurrence_type is RecurrenceType.WEEKLY

    @pytest.mark.parametrize(
        "extra",
        [
            {"recurrence_type": "weekly"},
            {"recurrence_type": "weekly", "days_of_week": []},
            {"recurrence_type": "monthly"},
            {"recurrence_type": "interval"},
            {"recurrence_type": "daily", "interval_days": 2},
            {"recurrence_type": "weekly", "days_of_week": [1], "day_of_month": 3},
            {"recurrence_type": "weekly", "days_of_week": [7]},
            {"recurrence_type": "monthly", "day_of_month": 32},
            {"recurrence_type": "interval", "interval_days": 0},
            {"recurrence_type": "yearly"},
        ],
    )
    def test_create_rejects_inconsistent_recurrence(self, extra) -> None:
        with pytest.raises(ValidationError):
            RoutineCreate(title="x", start_date=MON, **extra)

    @pytest.mark.parametrize("title", ["", "a" * 256])
    def test_create_title_length(self, title) -> None:
        with pytest.raises(ValidationError):
            RoutineCreate(title=title, recurrence_type="daily", start_date=MON)

    def test_create_description_max_length(self) -> None:
        RoutineCreate(title="x", description="a" * 2000, recurrence_type="daily", start_date=MON)
        with pytest.raises(ValidationError):
            RoutineCreate(title="x", description="a" * 2001, recurrence_type="daily", start_date=MON)

    @pytest.mark.parametrize("start", ["2000-01-01", "2100-12-31"])
    def test_start_date_bounds_inclusive(self, start) -> None:
        r = RoutineCreate(title="x", recurrence_type="daily", start_date=start)
        assert r.start_date == date.fromisoformat(start)

    @pytest.mark.parametrize("start", ["1999-12-31", "2101-01-01"])
    def test_start_date_out_of_bounds(self, start) -> None:
        with pytest.raises(ValidationError):
            RoutineCreate(title="x", recurrence_type="daily", start_date=start)
        with pytest.raises(ValidationError):
            RoutineUpdate(start_date=start)

    @pytest.mark.parametrize("field", ["title", "recurrence_type", "start_date"])
    def test_update_rejects_null_for_required_fields(self, field) -> None:
        with pytest.raises(ValidationError, match=f"{field} cannot be null"):
            RoutineUpdate(**{field: None})

    def test_update_changes_tracks_sent_fields_only(self) -> None:
        u = RoutineUpdate.model_validate({"description": None, "title": "new"})
        assert u.changes() == {"description": None, "title": "new"}
        assert RoutineUpdate.model_validate({}).changes() == {}


# --- Repository ---------------------------------------------------------------------


class TestRoutineRepository:
    def test_unique_constraint_on_routine_and_day(self, repo, service, db_session) -> None:
        r = make(service)
        repo.create_log(r.id, MON)
        with pytest.raises(IntegrityError):
            repo.create_log(r.id, MON)
        # Session is usable again after the rollback inside create_log.
        assert count(db_session, RoutineLog) == 1

    def test_get_log_dates_single_query_grouped(self, repo, service) -> None:
        a, b = make(service, "a"), make(service, "b")
        repo.create_log(a.id, MON)
        repo.create_log(a.id, TUE)
        repo.create_log(b.id, WED)
        with capture_sql() as sql:
            dates = repo.get_log_dates([a.id, b.id])
        assert len(sql) == 1
        assert dates[a.id] == {MON, TUE}
        assert dates[b.id] == {WED}

    def test_get_log_dates_empty_ids_runs_no_query(self, repo) -> None:
        with capture_sql() as sql:
            assert repo.get_log_dates([]) == {}
        assert sql == []


# --- Service ------------------------------------------------------------------------


class TestRoutineServiceCrud:
    def test_create_and_get(self, service) -> None:
        created = make(service, "Read", recurrence_type="weekly", days_of_week=[2, 0], start_date=MON)
        got = service.get_routine(created.id)
        assert got.title == "Read"
        assert got.days_of_week == [0, 2]
        assert got.start_date == MON

    def test_get_missing_raises(self, service) -> None:
        with pytest.raises(RoutineNotFoundError):
            service.get_routine(999)

    def test_list_excludes_deleted(self, service) -> None:
        keep, gone = make(service, "keep"), make(service, "gone")
        service.delete_routine(gone.id)
        assert [r.id for r in service.list_routines()] == [keep.id]
        with pytest.raises(RoutineNotFoundError):
            service.get_routine(gone.id)

    def test_soft_delete_keeps_rows(self, service, db_session) -> None:
        r = make(service)
        service.mark_done(r.id, MON)
        service.delete_routine(r.id)
        row = db_session.get(Routine, r.id)
        assert row is not None and row.deleted_at is not None
        assert count(db_session, RoutineLog) == 1

    def test_delete_twice_raises_not_found(self, service) -> None:
        r = make(service)
        service.delete_routine(r.id)
        with pytest.raises(RoutineNotFoundError):
            service.delete_routine(r.id)


class TestRoutineServiceUpdate:
    def test_unsent_field_kept_and_null_clears(self, service) -> None:
        r = make(service, "T", description="keep me")
        updated = service.update_routine(r.id, RoutineUpdate.model_validate({"title": "T2"}))
        assert updated.title == "T2"
        assert updated.description == "keep me"
        cleared = service.update_routine(r.id, RoutineUpdate.model_validate({"description": None}))
        assert cleared.description is None
        assert cleared.title == "T2"

    def test_weekly_to_daily_requires_explicit_null(self, service) -> None:
        r = make(service, recurrence_type="weekly", days_of_week=[0, 2])
        with pytest.raises(RoutineValidationError, match="days_of_week must be null"):
            service.update_routine(r.id, RoutineUpdate.model_validate({"recurrence_type": "daily"}))
        assert service.get_routine(r.id).recurrence_type is RecurrenceType.WEEKLY

        ok = service.update_routine(
            r.id, RoutineUpdate.model_validate({"recurrence_type": "daily", "days_of_week": None})
        )
        assert ok.recurrence_type is RecurrenceType.DAILY
        assert ok.days_of_week is None

    def test_merged_state_validated_when_only_detail_sent(self, service) -> None:
        r = make(service, recurrence_type="monthly", day_of_month=10)
        with pytest.raises(RoutineValidationError):
            service.update_routine(r.id, RoutineUpdate.model_validate({"day_of_month": None}))
        with pytest.raises(RoutineValidationError):
            service.update_routine(r.id, RoutineUpdate.model_validate({"interval_days": 3}))
        assert service.update_routine(
            r.id, RoutineUpdate.model_validate({"day_of_month": 31})
        ).day_of_month == 31

    def test_update_missing_raises(self, service) -> None:
        with pytest.raises(RoutineNotFoundError):
            service.update_routine(999, RoutineUpdate.model_validate({"title": "x"}))


class TestRoutineServiceDay:
    def test_only_scheduled_routines_returned(self, service) -> None:
        mon_wed = make(service, "mw", recurrence_type="weekly", days_of_week=[0, 2])
        make(service, "tue", recurrence_type="weekly", days_of_week=[1])
        everyday = make(service, "daily")
        assert [i.id for i in service.get_routines_for_day(MON)] == [mon_wed.id, everyday.id]

    def test_future_start_not_listed(self, service) -> None:
        make(service, "later", start_date=date(2024, 2, 1))
        assert service.get_routines_for_day(date(2024, 1, 31)) == []
        assert len(service.get_routines_for_day(date(2024, 2, 1))) == 1

    def test_deleted_not_listed(self, service) -> None:
        r = make(service)
        service.delete_routine(r.id)
        assert service.get_routines_for_day(MON) == []

    def test_completion_and_streaks(self, service) -> None:
        r = make(service)  # daily from MON
        for d in (MON, TUE, WED, date(2024, 1, 5), date(2024, 1, 6)):
            service.mark_done(r.id, d)
        # 01-07 not done yet -> skipped; 01-06, 01-05 done; 01-04 missed.
        (item,) = service.get_routines_for_day(date(2024, 1, 7))
        assert (item.completed, item.current_streak, item.longest_streak) == (False, 2, 3)
        (item,) = service.get_routines_for_day(WED)
        assert (item.completed, item.current_streak, item.longest_streak) == (True, 3, 3)

    def test_logs_loaded_in_one_query(self, service) -> None:
        ids = [make(service, f"r{i}").id for i in range(5)]
        for rid in ids:
            for d in (MON, TUE, WED):
                service.mark_done(rid, d)
        with capture_sql() as sql:
            items = service.get_routines_for_day(date(2024, 1, 4))
        log_queries = [s for s in sql if "routine_logs" in s]
        assert len(items) == 5
        assert len(log_queries) == 1
        assert all(not s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in sql)


class TestRoutineServiceLogs:
    def test_mark_done_is_idempotent(self, service, db_session) -> None:
        r = make(service)
        first, created1 = service.mark_done(r.id, MON)
        second, created2 = service.mark_done(r.id, MON)
        assert (created1, created2) == (True, False)
        assert first.id == second.id
        assert count(db_session, RoutineLog) == 1

    def test_mark_done_unscheduled_day_raises(self, service, db_session) -> None:
        r = make(service, recurrence_type="weekly", days_of_week=[0])
        with pytest.raises(RoutineNotScheduledError):
            service.mark_done(r.id, TUE)
        with pytest.raises(RoutineNotScheduledError):
            service.mark_done(r.id, date(2023, 12, 25))  # Monday, but before start_date
        assert count(db_session, RoutineLog) == 0

    def test_mark_done_deleted_routine_raises(self, service) -> None:
        r = make(service)
        service.delete_routine(r.id)
        with pytest.raises(RoutineNotFoundError):
            service.mark_done(r.id, MON)

    def test_concurrent_insert_returns_existing(self, service, repo, db_session, monkeypatch) -> None:
        """First create_log loses a race: another writer inserted the row first."""
        r = make(service)
        real_create_log = repo.create_log
        calls: list[date] = []

        def racing_create_log(routine_id: int, day: date):
            calls.append(day)
            if len(calls) == 1:
                other = TestSessionLocal()
                try:
                    RoutineRepository(other).create_log(routine_id, day)  # the winner
                finally:
                    other.close()
                raise IntegrityError("INSERT INTO routine_logs ...", {}, Exception("UNIQUE"))
            return real_create_log(routine_id, day)

        monkeypatch.setattr(repo, "create_log", racing_create_log)
        log, created = service.mark_done(r.id, MON)
        assert created is False
        assert log.routine_id == r.id and log.log_date == MON
        assert calls == [MON]
        assert count(db_session, RoutineLog) == 1

    def test_integrity_error_without_existing_row_propagates(self, service, repo, monkeypatch) -> None:
        r = make(service)

        def broken_create_log(routine_id: int, day: date):
            raise IntegrityError("INSERT INTO routine_logs ...", {}, Exception("FOREIGN KEY"))

        monkeypatch.setattr(repo, "create_log", broken_create_log)
        with pytest.raises(IntegrityError):
            service.mark_done(r.id, MON)

    def test_undo_removes_and_is_idempotent(self, service, db_session) -> None:
        r = make(service)
        service.mark_done(r.id, MON)
        service.mark_done(r.id, TUE)
        service.undo(r.id, MON)
        service.undo(r.id, MON)
        assert db_session.scalars(select(RoutineLog.log_date)).all() == [TUE]

    def test_undo_deleted_routine_raises(self, service) -> None:
        r = make(service)
        service.delete_routine(r.id)
        with pytest.raises(RoutineNotFoundError):
            service.undo(r.id, MON)


# --- API ----------------------------------------------------------------------------


def create_test_token() -> str:
    now = datetime.now(UTC)
    payload = {"sub": "user", "exp": now + timedelta(days=1), "iat": now}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


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
    async for ac in _client(db_session, {"Authorization": f"Bearer {create_test_token()}"}):
        yield ac


@pytest.fixture
async def client_no_auth(db_session: Session) -> AsyncIterator[AsyncClient]:
    async for ac in _client(db_session, {}):
        yield ac


async def post_routine(client: AsyncClient, **overrides) -> dict:
    body = {"title": "Routine", "recurrence_type": "daily", "start_date": "2024-01-01", **overrides}
    response = await client.post("/routines", json=body)
    assert response.status_code == 201, response.text
    return response.json()


class TestRoutinesApiCrud:
    async def test_create_returns_routine(self, client) -> None:
        data = await post_routine(
            client, title="Spor", recurrence_type="weekly", days_of_week=[4, 0], start_date="2024-01-01"
        )
        assert data["title"] == "Spor"
        assert data["recurrence_type"] == "weekly"
        assert data["days_of_week"] == [0, 4]
        assert data["day_of_month"] is None and data["interval_days"] is None
        assert data["start_date"] == "2024-01-01"
        assert {"id", "created_at", "updated_at"} <= data.keys()

    @pytest.mark.parametrize(
        "body",
        [
            {"recurrence_type": "weekly"},
            {"recurrence_type": "weekly", "days_of_week": []},
            {"recurrence_type": "monthly"},
            {"recurrence_type": "interval"},
            {"recurrence_type": "daily", "day_of_month": 5},
            {"recurrence_type": "monthly", "day_of_month": 5, "interval_days": 2},
        ],
    )
    async def test_create_inconsistent_recurrence_422(self, client, db_session, body) -> None:
        response = await client.post(
            "/routines", json={"title": "x", "start_date": "2024-01-01", **body}
        )
        assert response.status_code == 422
        assert count(db_session, Routine) == 0

    @pytest.mark.parametrize("start", ["1999-12-31", "2101-01-01"])
    async def test_create_start_date_out_of_range_422(self, client, start) -> None:
        response = await client.post(
            "/routines", json={"title": "x", "recurrence_type": "daily", "start_date": start}
        )
        assert response.status_code == 422

    async def test_list_and_get(self, client) -> None:
        a = await post_routine(client, title="a")
        b = await post_routine(client, title="b")
        listed = (await client.get("/routines")).json()
        assert [r["id"] for r in listed] == [a["id"], b["id"]]
        got = await client.get(f"/routines/{a['id']}")
        assert got.status_code == 200 and got.json()["title"] == "a"

    async def test_get_missing_404(self, client) -> None:
        response = await client.get("/routines/999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_soft_delete_hides_but_keeps_rows(self, client, db_session) -> None:
        keep = await post_routine(client, title="keep")
        gone = await post_routine(client, title="gone")
        assert (await client.put(f"/routines/{gone['id']}/logs/2024-01-01")).status_code == 201

        assert (await client.delete(f"/routines/{gone['id']}")).status_code == 204

        assert [r["id"] for r in (await client.get("/routines")).json()] == [keep["id"]]
        day_ids = [r["id"] for r in (await client.get("/routines/day/2024-01-01")).json()]
        assert day_ids == [keep["id"]]
        assert (await client.get(f"/routines/{gone['id']}")).status_code == 404
        assert (await client.patch(f"/routines/{gone['id']}", json={"title": "x"})).status_code == 404
        assert (await client.delete(f"/routines/{gone['id']}")).status_code == 404

        db_session.expire_all()
        row = db_session.get(Routine, gone["id"])
        assert row is not None and row.deleted_at is not None
        logs = db_session.scalars(select(RoutineLog).where(RoutineLog.routine_id == gone["id"])).all()
        assert [log.log_date for log in logs] == [MON]


class TestRoutinesApiPatch:
    async def test_unsent_field_preserved_null_clears(self, client) -> None:
        r = await post_routine(client, title="T", description="desc")
        response = await client.patch(f"/routines/{r['id']}", json={"title": "T2"})
        assert response.status_code == 200
        assert response.json()["title"] == "T2"
        assert response.json()["description"] == "desc"

        response = await client.patch(f"/routines/{r['id']}", json={"description": None})
        assert response.status_code == 200
        assert response.json()["description"] is None
        assert response.json()["title"] == "T2"
        assert (await client.get(f"/routines/{r['id']}")).json()["description"] is None

    @pytest.mark.parametrize("field", ["title", "recurrence_type", "start_date"])
    async def test_null_for_required_field_422(self, client, field) -> None:
        r = await post_routine(client)
        response = await client.patch(f"/routines/{r['id']}", json={field: None})
        assert response.status_code == 422
        assert (await client.get(f"/routines/{r['id']}")).json()[field] == r[field]

    async def test_weekly_to_daily_needs_explicit_null(self, client) -> None:
        r = await post_routine(client, recurrence_type="weekly", days_of_week=[0, 2])
        response = await client.patch(f"/routines/{r['id']}", json={"recurrence_type": "daily"})
        assert response.status_code == 422
        assert "days_of_week must be null" in response.json()["detail"]
        assert (await client.get(f"/routines/{r['id']}")).json()["recurrence_type"] == "weekly"

        response = await client.patch(
            f"/routines/{r['id']}", json={"recurrence_type": "daily", "days_of_week": None}
        )
        assert response.status_code == 200
        assert response.json()["recurrence_type"] == "daily"
        assert response.json()["days_of_week"] is None

    async def test_inconsistent_merged_state_422(self, client) -> None:
        r = await post_routine(client, recurrence_type="monthly", day_of_month=10)
        response = await client.patch(f"/routines/{r['id']}", json={"interval_days": 3})
        assert response.status_code == 422
        assert (await client.get(f"/routines/{r['id']}")).json()["interval_days"] is None

    async def test_patch_missing_404(self, client) -> None:
        assert (await client.patch("/routines/999", json={"title": "x"})).status_code == 404


class TestRoutinesApiDay:
    async def test_returns_only_scheduled_with_streaks(self, client) -> None:
        mon_wed = await post_routine(client, title="mw", recurrence_type="weekly", days_of_week=[0, 2])
        await post_routine(client, title="tue", recurrence_type="weekly", days_of_week=[1])
        later = await post_routine(client, title="later", start_date="2024-02-01")
        for day in ("2024-01-01", "2024-01-03"):
            assert (await client.put(f"/routines/{mon_wed['id']}/logs/{day}")).status_code == 201

        response = await client.get("/routines/day/2024-01-08")  # Monday
        assert response.status_code == 200
        items = response.json()
        assert [i["id"] for i in items] == [mon_wed["id"]]  # tue: not scheduled; later: not started
        assert items[0]["completed"] is False
        assert items[0]["current_streak"] == 2  # reference day not done -> not a break
        assert items[0]["longest_streak"] == 2
        assert later["id"] not in [i["id"] for i in items]

        wed = (await client.get("/routines/day/2024-01-03")).json()
        assert [(i["id"], i["completed"], i["current_streak"]) for i in wed] == [(mon_wed["id"], True, 2)]

    async def test_day_route_not_swallowed_by_routine_id(self, client) -> None:
        await post_routine(client)
        response = await client.get("/routines/day/2024-01-01")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        assert {"completed", "current_streak", "longest_streak"} <= body[0].keys()
        # And /routines/{id} still resolves for numeric ids.
        assert (await client.get(f"/routines/{body[0]['id']}")).status_code == 200

    def test_day_route_registered_before_routine_id(self) -> None:
        from app.api.routines import router

        paths = [route.path for route in router.routes if "GET" in route.methods]
        assert paths.index("/routines/day/{day}") < paths.index("/routines/{routine_id}")

    async def test_empty_day(self, client) -> None:
        response = await client.get("/routines/day/2024-01-01")
        assert response.status_code == 200
        assert response.json() == []


class TestRoutinesApiLogs:
    async def test_put_is_idempotent(self, client, db_session) -> None:
        r = await post_routine(client)
        first = await client.put(f"/routines/{r['id']}/logs/2024-01-02")
        second = await client.put(f"/routines/{r['id']}/logs/2024-01-02")
        assert first.status_code == 201
        assert second.status_code == 200
        assert first.json() == second.json()
        assert first.json()["log_date"] == "2024-01-02"
        assert count(db_session, RoutineLog) == 1

    async def test_put_unscheduled_day_422(self, client, db_session) -> None:
        r = await post_routine(client, recurrence_type="weekly", days_of_week=[0])
        response = await client.put(f"/routines/{r['id']}/logs/2024-01-02")  # Tuesday
        assert response.status_code == 422
        assert "not scheduled" in response.json()["detail"]
        before_start = await client.put(f"/routines/{r['id']}/logs/2023-12-25")  # Monday < start
        assert before_start.status_code == 422
        assert count(db_session, RoutineLog) == 0

    async def test_delete_undoes_and_is_idempotent(self, client) -> None:
        r = await post_routine(client)
        await client.put(f"/routines/{r['id']}/logs/2024-01-01")
        assert (await client.delete(f"/routines/{r['id']}/logs/2024-01-01")).status_code == 204
        assert (await client.delete(f"/routines/{r['id']}/logs/2024-01-01")).status_code == 204
        item = (await client.get("/routines/day/2024-01-01")).json()[0]
        assert item["completed"] is False

    @pytest.mark.parametrize("method", ["PUT", "DELETE"])
    async def test_logs_for_missing_routine_404(self, client, method) -> None:
        response = await client.request(method, "/routines/999/logs/2024-01-01")
        assert response.status_code == 404

    @pytest.mark.parametrize("method", ["PUT", "DELETE"])
    async def test_logs_for_deleted_routine_404(self, client, method) -> None:
        r = await post_routine(client)
        await client.delete(f"/routines/{r['id']}")
        response = await client.request(method, f"/routines/{r['id']}/logs/2024-01-01")
        assert response.status_code == 404


class TestRoutinesApiDateBounds:
    @pytest.mark.parametrize("day", ["1999-12-31", "2101-01-01", "2024-13-01", "2024-02-30", "yesterday"])
    async def test_out_of_range_or_invalid_day_422(self, client, db_session, day) -> None:
        r = await post_routine(client, start_date="2000-01-01")
        for method, url in [
            ("GET", f"/routines/day/{day}"),
            ("PUT", f"/routines/{r['id']}/logs/{day}"),
            ("DELETE", f"/routines/{r['id']}/logs/{day}"),
        ]:
            response = await client.request(method, url)
            assert response.status_code == 422, (method, url)
        assert count(db_session, RoutineLog) == 0

    @pytest.mark.parametrize("day", ["2000-01-01", "2100-12-31"])
    async def test_boundary_days_accepted(self, client, day) -> None:
        r = await post_routine(client, start_date="2000-01-01")
        assert (await client.get(f"/routines/day/{day}")).status_code == 200
        assert (await client.put(f"/routines/{r['id']}/logs/{day}")).status_code == 201
        assert (await client.delete(f"/routines/{r['id']}/logs/{day}")).status_code == 204


class TestRoutinesApiAuthAndReadOnly:
    @pytest.mark.parametrize(
        ("method", "url"),
        [
            ("POST", "/routines"),
            ("GET", "/routines"),
            ("GET", "/routines/day/2024-01-01"),
            ("GET", "/routines/1"),
            ("PATCH", "/routines/1"),
            ("DELETE", "/routines/1"),
            ("PUT", "/routines/1/logs/2024-01-01"),
            ("DELETE", "/routines/1/logs/2024-01-01"),
        ],
    )
    async def test_requires_token(self, client_no_auth, method, url) -> None:
        kwargs = {"json": {"title": "x"}} if method in ("POST", "PATCH") else {}
        response = await client_no_auth.request(method, url, **kwargs)
        assert response.status_code == 401

    async def test_get_endpoints_do_not_write(self, client, db_session) -> None:
        r = await post_routine(client, description="d")
        await client.put(f"/routines/{r['id']}/logs/2024-01-01")
        before = db_session.scalars(select(Routine.updated_at)).all()

        with capture_sql() as sql:
            for url in ("/routines", f"/routines/{r['id']}", "/routines/day/2024-01-02",
                        "/routines/day/2024-01-01", "/routines/999"):
                await client.get(url)

        writes = [s for s in sql if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        assert sql, "expected the GETs to run SELECTs"
        assert writes == []
        db_session.expire_all()
        assert db_session.scalars(select(Routine.updated_at)).all() == before
        assert count(db_session, RoutineLog) == 1
