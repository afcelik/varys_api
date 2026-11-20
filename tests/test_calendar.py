"""Tests for GET /calendar/month (HTTP), the calendar repository and the pure month_summary."""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import get_db
from app.main import create_app
from app.models.base import Base
from app.models.task import Task, TaskStatus
from app.repositories.calendar import CalendarRepository
from app.services.calendar import month_summary

# In-memory SQLite, one shared connection (same setup as tests/test_schedule.py).
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


def _selects(statements: list[str]) -> list[str]:
    return [s for s in statements if s.lstrip().upper().startswith("SELECT")]


# --- helpers ----------------------------------------------------------------------------


async def post_task(client: AsyncClient, **overrides) -> dict:
    body = {"title": "Görev", **overrides}
    response = await client.post("/tasks", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def post_routine(client: AsyncClient, **overrides) -> dict:
    body = {"title": "Rutin", "recurrence_type": "daily", "start_date": "2024-01-01", **overrides}
    response = await client.post("/routines", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def post_block(client: AsyncClient, **overrides) -> dict:
    body = {
        "title": "Blok",
        "start_time": "07:00",
        "end_time": "08:00",
        "recurrence_type": "daily",
        "start_date": "2024-01-01",
        **overrides,
    }
    response = await client.post("/schedule", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def get_month(client: AsyncClient, year: int, month: int) -> dict:
    response = await client.get("/calendar/month", params={"year": year, "month": month})
    assert response.status_code == 200, response.text
    return response.json()


def counts(data: dict, field: str) -> dict[str, int]:
    """Non-zero counts of one field, keyed by 'YYYY-MM-DD'."""
    return {d["day"]: d[field] for d in data["days"] if d[field]}


COUNT_FIELDS = ("task_count", "due_count", "routine_count", "schedule_count")


# --- month shape ------------------------------------------------------------------------


class TestMonthShape:
    @pytest.mark.parametrize(
        ("year", "month", "length"),
        [(2023, 2, 28), (2024, 2, 29), (2024, 4, 30), (2024, 1, 31), (2100, 12, 31)],
    )
    async def test_every_day_of_month_in_order(self, client, year, month, length) -> None:
        data = await get_month(client, year, month)
        assert data["year"] == year and data["month"] == month
        expected = [date(year, month, d).isoformat() for d in range(1, length + 1)]
        assert [d["day"] for d in data["days"]] == expected

    async def test_empty_month_has_zero_counts(self, client) -> None:
        data = await get_month(client, 2024, 3)
        assert len(data["days"]) == 31
        for day in data["days"]:
            assert set(day) == {"day", *COUNT_FIELDS}
            assert all(day[f] == 0 for f in COUNT_FIELDS)


# --- tasks ------------------------------------------------------------------------------


class TestTaskCounts:
    async def test_planned_tasks_counted_on_their_day(self, client) -> None:
        await post_task(client, planned_date="2024-03-05T00:00:00")
        await post_task(client, planned_date="2024-03-05T00:00:00")
        await post_task(client, planned_date="2024-03-20T00:00:00")
        await post_task(client)  # no dates
        data = await get_month(client, 2024, 3)
        assert counts(data, "task_count") == {"2024-03-05": 2, "2024-03-20": 1}
        assert counts(data, "due_count") == {}

    async def test_time_component_counts_on_same_day(self, client) -> None:
        await post_task(client, planned_date="2024-03-05T13:30:00")
        await post_task(client, planned_date="2024-03-05T23:59:59")
        await post_task(client, planned_date="2024-03-05T00:00:00")
        data = await get_month(client, 2024, 3)
        assert counts(data, "task_count") == {"2024-03-05": 3}

    async def test_done_task_not_counted(self, client) -> None:
        await post_task(client, planned_date="2024-03-05T00:00:00", status="done")
        patched = await post_task(client, planned_date="2024-03-05T00:00:00")
        response = await client.patch(f"/tasks/{patched['id']}", json={"status": "done"})
        assert response.status_code == 200, response.text
        await post_task(client, planned_date="2024-03-05T00:00:00", status="in_progress")
        data = await get_month(client, 2024, 3)
        assert counts(data, "task_count") == {"2024-03-05": 1}

    async def test_deleted_task_not_counted(self, client) -> None:
        task = await post_task(client, planned_date="2024-03-05T00:00:00", due_date="2024-03-06T00:00:00")
        await post_task(client, planned_date="2024-03-05T00:00:00")
        response = await client.delete(f"/tasks/{task['id']}")
        assert response.status_code == 204, response.text
        data = await get_month(client, 2024, 3)
        assert counts(data, "task_count") == {"2024-03-05": 1}
        assert counts(data, "due_count") == {}

    async def test_due_counted_separately(self, client) -> None:
        await post_task(client, planned_date="2024-03-05T00:00:00", due_date="2024-03-10T18:00:00")
        await post_task(client, due_date="2024-03-10T00:00:00")
        data = await get_month(client, 2024, 3)
        assert counts(data, "task_count") == {"2024-03-05": 1}
        assert counts(data, "due_count") == {"2024-03-10": 2}

    async def test_done_task_not_in_due_count(self, client) -> None:
        await post_task(client, due_date="2024-03-10T00:00:00", status="done")
        await post_task(client, due_date="2024-03-10T00:00:00")
        data = await get_month(client, 2024, 3)
        assert counts(data, "due_count") == {"2024-03-10": 1}

    async def test_planned_in_month_due_in_next_month(self, client) -> None:
        """A task appears in each month only for the date that falls in it."""
        await post_task(client, planned_date="2024-01-31T00:00:00", due_date="2024-02-02T00:00:00")
        jan = await get_month(client, 2024, 1)
        feb = await get_month(client, 2024, 2)
        assert counts(jan, "task_count") == {"2024-01-31": 1} and counts(jan, "due_count") == {}
        assert counts(feb, "task_count") == {} and counts(feb, "due_count") == {"2024-02-02": 1}


class TestMonthBoundary:
    @pytest.mark.parametrize("field", ["planned_date", "due_date"])
    async def test_last_day_of_month_stays_in_month(self, client, field) -> None:
        await post_task(client, **{field: "2024-01-31T00:00:00"})
        await post_task(client, **{field: "2024-01-31T23:59:59"})
        count_field = "task_count" if field == "planned_date" else "due_count"
        jan = await get_month(client, 2024, 1)
        feb = await get_month(client, 2024, 2)
        assert counts(jan, count_field) == {"2024-01-31": 2}
        assert counts(feb, count_field) == {}

    @pytest.mark.parametrize("field", ["planned_date", "due_date"])
    async def test_first_day_of_next_month_not_in_previous(self, client, field) -> None:
        await post_task(client, **{field: "2024-02-01T00:00:00"})
        count_field = "task_count" if field == "planned_date" else "due_count"
        jan = await get_month(client, 2024, 1)
        feb = await get_month(client, 2024, 2)
        assert counts(jan, count_field) == {}
        assert counts(feb, count_field) == {"2024-02-01": 1}

    async def test_year_boundary(self, client) -> None:
        await post_task(client, planned_date="2023-12-31T23:59:59")
        await post_task(client, planned_date="2024-01-01T00:00:00")
        dec = await get_month(client, 2023, 12)
        jan = await get_month(client, 2024, 1)
        assert counts(dec, "task_count") == {"2023-12-31": 1}
        assert counts(jan, "task_count") == {"2024-01-01": 1}


class TestCalendarRepository:
    """The SQL range filter on its own (the pure function also filters by month)."""

    def _add(self, db: Session, planned: datetime | None = None, due: datetime | None = None) -> None:
        db.add(Task(title="T", planned_date=planned, due_date=due))
        db.commit()

    def test_range_is_inclusive_and_excludes_neighbours(self, db_session) -> None:
        self._add(db_session, planned=datetime(2024, 1, 31))
        self._add(db_session, planned=datetime(2024, 2, 1))
        self._add(db_session, planned=datetime(2024, 2, 29, 23, 59, 59))
        self._add(db_session, planned=datetime(2024, 3, 1))
        self._add(db_session, due=datetime(2024, 1, 31, 23, 59, 59))
        self._add(db_session, due=datetime(2024, 2, 15))
        self._add(db_session, due=datetime(2024, 3, 1))
        rows = CalendarRepository(db_session).get_task_dates_in_range(
            date(2024, 2, 1), date(2024, 2, 29)
        )
        planned = sorted(r[0] for r in rows if r[0] is not None)
        due = sorted(r[1] for r in rows if r[1] is not None)
        assert planned == [datetime(2024, 2, 1), datetime(2024, 2, 29, 23, 59, 59)]
        assert due == [datetime(2024, 2, 15)]

    def test_excludes_deleted(self, db_session) -> None:
        db_session.add(Task(title="T", planned_date=datetime(2024, 2, 5), deleted_at=datetime.now(UTC)))
        db_session.commit()
        rows = CalendarRepository(db_session).get_task_dates_in_range(
            date(2024, 2, 1), date(2024, 2, 29)
        )
        assert rows == []

    def test_returns_status(self, db_session) -> None:
        db_session.add(Task(title="T", planned_date=datetime(2024, 2, 5), status=TaskStatus.DONE))
        db_session.commit()
        rows = CalendarRepository(db_session).get_task_dates_in_range(
            date(2024, 2, 1), date(2024, 2, 29)
        )
        assert rows == [(datetime(2024, 2, 5), None, TaskStatus.DONE)]


# --- routines & schedule ----------------------------------------------------------------

# 2024-04: 1st is a Monday; 30 days. 2024-02: 29 days.
RECURRENCE_CASES = [
    pytest.param(
        {"recurrence_type": "daily", "start_date": "2024-04-10"},
        2024,
        4,
        {f"2024-04-{d:02d}" for d in range(10, 31)},
        id="daily_from_start",
    ),
    pytest.param(
        {"recurrence_type": "weekly", "days_of_week": [0, 2], "start_date": "2024-04-01"},
        2024,
        4,
        {"2024-04-01", "2024-04-03", "2024-04-08", "2024-04-10", "2024-04-15", "2024-04-17",
         "2024-04-22", "2024-04-24", "2024-04-29"},
        id="weekly_mon_wed",
    ),
    pytest.param(
        {"recurrence_type": "monthly", "day_of_month": 31, "start_date": "2024-01-01"},
        2024,
        2,
        {"2024-02-29"},
        id="monthly_31_clamped_feb_leap",
    ),
    pytest.param(
        {"recurrence_type": "monthly", "day_of_month": 31, "start_date": "2024-01-01"},
        2024,
        4,
        {"2024-04-30"},
        id="monthly_31_clamped_april",
    ),
    pytest.param(
        {"recurrence_type": "interval", "interval_days": 3, "start_date": "2024-03-30"},
        2024,
        4,
        {"2024-04-02", "2024-04-05", "2024-04-08", "2024-04-11", "2024-04-14", "2024-04-17",
         "2024-04-20", "2024-04-23", "2024-04-26", "2024-04-29"},
        id="interval_3_from_previous_month",
    ),
    pytest.param(
        {"recurrence_type": "daily", "start_date": "2024-05-01"},
        2024,
        4,
        set(),
        id="starts_after_month",
    ),
]


class TestRoutineCounts:
    @pytest.mark.parametrize(("body", "year", "month", "expected"), RECURRENCE_CASES)
    async def test_recurrence(self, client, body, year, month, expected) -> None:
        await post_routine(client, **body)
        data = await get_month(client, year, month)
        assert counts(data, "routine_count") == dict.fromkeys(expected, 1)
        assert counts(data, "schedule_count") == {}

    async def test_multiple_routines_summed(self, client) -> None:
        await post_routine(client, start_date="2024-04-01")
        await post_routine(client, start_date="2024-04-29")
        data = await get_month(client, 2024, 4)
        assert counts(data, "routine_count")["2024-04-28"] == 1
        assert counts(data, "routine_count")["2024-04-29"] == 2

    async def test_deleted_routine_not_counted(self, client) -> None:
        routine = await post_routine(client, start_date="2024-04-01")
        response = await client.delete(f"/routines/{routine['id']}")
        assert response.status_code == 204, response.text
        data = await get_month(client, 2024, 4)
        assert counts(data, "routine_count") == {}


class TestScheduleCounts:
    @pytest.mark.parametrize(("body", "year", "month", "expected"), RECURRENCE_CASES)
    async def test_recurrence(self, client, body, year, month, expected) -> None:
        await post_block(client, **body)
        data = await get_month(client, year, month)
        assert counts(data, "schedule_count") == dict.fromkeys(expected, 1)
        assert counts(data, "routine_count") == {}

    async def test_deleted_block_not_counted(self, client) -> None:
        block = await post_block(client, start_date="2024-04-01")
        response = await client.delete(f"/schedule/{block['id']}")
        assert response.status_code == 204, response.text
        data = await get_month(client, 2024, 4)
        assert counts(data, "schedule_count") == {}


# --- validation, auth, read-only, query count -------------------------------------------


class TestValidation:
    @pytest.mark.parametrize(
        "params",
        [
            {"year": 1999, "month": 1},
            {"year": 2101, "month": 1},
            {"year": 2024, "month": 0},
            {"year": 2024, "month": 13},
            {"month": 1},
            {"year": 2024},
            {},
            {"year": "abc", "month": 1},
        ],
    )
    async def test_invalid_params_422(self, client, params) -> None:
        response = await client.get("/calendar/month", params=params)
        assert response.status_code == 422, response.text

    @pytest.mark.parametrize(("year", "month"), [(2000, 1), (2100, 12)])
    async def test_range_edges_accepted(self, client, year, month) -> None:
        data = await get_month(client, year, month)
        assert data["days"][0]["day"] == date(year, month, 1).isoformat()

    async def test_requires_token(self, client_no_auth) -> None:
        response = await client_no_auth.get("/calendar/month", params={"year": 2024, "month": 1})
        assert response.status_code == 401


class TestReadOnlyAndQueries:
    async def test_get_writes_nothing(self, client) -> None:
        await post_task(client, planned_date="2024-04-05T00:00:00", due_date="2024-04-06T00:00:00")
        await post_routine(client, start_date="2024-04-01")
        await post_block(client, start_date="2024-04-01")
        with capture_sql() as statements:
            await get_month(client, 2024, 4)
        assert _selects(statements)
        assert _writes(statements) == []

    async def _select_count(self, client, n: int) -> int:
        for i in range(n):
            day = f"2024-04-{i % 30 + 1:02d}T00:00:00"
            await post_task(client, planned_date=day, due_date=day)
            await post_routine(client, start_date="2024-04-01")
            await post_block(client, start_date="2024-04-01")
        with capture_sql() as statements:
            data = await get_month(client, 2024, 4)
        assert sum(d["routine_count"] for d in data["days"]) == 30 * n
        return len(_selects(statements))

    async def test_select_count_independent_of_rows(self, client, db_session) -> None:
        one = await self._select_count(client, 1)
        for table in reversed(Base.metadata.sorted_tables):
            db_session.execute(table.delete())
        db_session.commit()
        fifty = await self._select_count(client, 50)
        assert one == fifty == 3


# --- pure month_summary -----------------------------------------------------------------


@dataclass
class Rule:
    start_date: date
    recurrence_type: str = "daily"
    days_of_week: list[int] | None = None
    day_of_month: int | None = None
    interval_days: int | None = None


def _nonzero(summary, field: str) -> dict[date, int]:
    return {d.day: getattr(d, field) for d in summary.days if getattr(d, field)}


class TestMonthSummary:
    def test_empty_inputs_all_zero(self) -> None:
        summary = month_summary(2024, 2, [], [], [])
        assert [d.day for d in summary.days] == [date(2024, 2, d) for d in range(1, 30)]
        for d in summary.days:
            assert (d.task_count, d.due_count, d.routine_count, d.schedule_count) == (0, 0, 0, 0)

    def test_done_skipped_for_planned_and_due(self) -> None:
        tasks = [
            (datetime(2024, 2, 5), datetime(2024, 2, 7), TaskStatus.DONE),
            (datetime(2024, 2, 5, 23, 59, 59), datetime(2024, 2, 7), TaskStatus.TODO),
            (datetime(2024, 2, 5, 13, 30), None, "in_progress"),
            (None, datetime(2024, 2, 7), "done"),
        ]
        summary = month_summary(2024, 2, tasks, [], [])
        assert _nonzero(summary, "task_count") == {date(2024, 2, 5): 2}
        assert _nonzero(summary, "due_count") == {date(2024, 2, 7): 1}

    def test_dates_outside_month_ignored(self) -> None:
        tasks = [
            (datetime(2024, 1, 31, 23, 59, 59), datetime(2024, 3, 1), TaskStatus.TODO),
            (datetime(2024, 3, 1), datetime(2024, 1, 31), TaskStatus.TODO),
            (datetime(2024, 2, 29, 23, 59, 59), datetime(2024, 2, 1), TaskStatus.TODO),
        ]
        summary = month_summary(2024, 2, tasks, [], [])
        assert _nonzero(summary, "task_count") == {date(2024, 2, 29): 1}
        assert _nonzero(summary, "due_count") == {date(2024, 2, 1): 1}

    def test_routines_and_blocks_use_is_due(self) -> None:
        routines = [Rule(date(2024, 2, 27)), Rule(date(2024, 1, 1), "monthly", day_of_month=30)]
        blocks = [Rule(date(2024, 2, 1), "weekly", days_of_week=[3])]  # Thursdays
        summary = month_summary(2024, 2, [], routines, blocks)
        assert _nonzero(summary, "routine_count") == {
            date(2024, 2, 27): 1,
            date(2024, 2, 28): 1,
            date(2024, 2, 29): 2,
        }
        assert _nonzero(summary, "schedule_count") == {
            date(2024, 2, d): 1 for d in (1, 8, 15, 22, 29)
        }
