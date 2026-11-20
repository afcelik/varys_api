"""HTTP-level tests for schedule blocks (recurring time blocks)."""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import get_db
from app.main import create_app
from app.models.base import Base
from app.models.routine import RecurrenceType
from app.models.schedule import ScheduleBlock
from app.services.schedule_time import TIME_FORMAT_MESSAGE, TIME_RANGE_MESSAGE

# In-memory SQLite, one shared connection (same setup as tests/test_routines.py).
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


def _messages(response) -> str:
    """All validation messages of a 422 response, joined (pydantic list or plain detail)."""
    detail = response.json()["detail"]
    if isinstance(detail, str):
        return detail
    return " | ".join(item["msg"] for item in detail)


# 2024-01-01 is a Monday.
MON = "2024-01-01"


async def post_block(client: AsyncClient, **overrides) -> dict:
    body = {
        "title": "Blok",
        "start_time": "07:00",
        "end_time": "08:00",
        "recurrence_type": "daily",
        "start_date": MON,
        **overrides,
    }
    response = await client.post("/schedule", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def day_titles(client: AsyncClient, day: str) -> list[str]:
    response = await client.get(f"/schedule/day/{day}")
    assert response.status_code == 200, response.text
    return [b["title"] for b in response.json()]


# --- CRUD -----------------------------------------------------------------------------


class TestScheduleCrud:
    async def test_create_returns_block(self, client) -> None:
        data = await post_block(
            client,
            title="  Spor  ",
            description="Koşu",
            start_time="06:30",
            end_time="07:15",
            recurrence_type="weekly",
            days_of_week=[4, 0, 4],
        )
        assert data["title"] == "Spor"
        assert data["description"] == "Koşu"
        assert data["start_time"] == "06:30"
        assert data["end_time"] == "07:15"
        assert data["recurrence_type"] == "weekly"
        assert data["days_of_week"] == [0, 4]
        assert data["day_of_month"] is None and data["interval_days"] is None
        assert data["start_date"] == MON
        assert {"id", "created_at", "updated_at"} <= data.keys()
        assert "deleted_at" not in data

    async def test_create_stores_real_time_values(self, client, db_session) -> None:
        data = await post_block(client, start_time="00:00", end_time="23:59")
        block = db_session.get(ScheduleBlock, data["id"])
        assert block.start_time == time(0, 0)
        assert block.end_time == time(23, 59)
        assert block.recurrence_type is RecurrenceType.DAILY

    async def test_get_list_and_one(self, client) -> None:
        a = await post_block(client, title="a")
        b = await post_block(client, title="b", start_time="09:00", end_time="10:00")
        response = await client.get("/schedule")
        assert response.status_code == 200
        assert [x["id"] for x in response.json()] == [a["id"], b["id"]]

        one = await client.get(f"/schedule/{b['id']}")
        assert one.status_code == 200
        assert one.json() == b

    async def test_get_missing_is_404(self, client) -> None:
        assert (await client.get("/schedule/999")).status_code == 404
        assert (await client.patch("/schedule/999", json={"title": "x"})).status_code == 404
        assert (await client.delete("/schedule/999")).status_code == 404

    async def test_soft_delete_hides_block_but_keeps_row(self, client, db_session) -> None:
        keep = await post_block(client, title="keep")
        gone = await post_block(client, title="gone")

        response = await client.delete(f"/schedule/{gone['id']}")
        assert response.status_code == 204
        assert response.content == b""

        assert [x["id"] for x in (await client.get("/schedule")).json()] == [keep["id"]]
        assert await day_titles(client, MON) == ["keep"]
        assert (await client.get(f"/schedule/{gone['id']}")).status_code == 404
        assert (await client.patch(f"/schedule/{gone['id']}", json={"title": "x"})).status_code == 404
        assert (await client.delete(f"/schedule/{gone['id']}")).status_code == 404

        db_session.expire_all()
        row = db_session.get(ScheduleBlock, gone["id"])
        assert row is not None
        assert row.deleted_at is not None
        assert row.title == "gone"


# --- Create validation ----------------------------------------------------------------


class TestCreateValidation:
    @pytest.mark.parametrize("value", ["00:00", "09:05", "23:58"])
    async def test_valid_start_times(self, client, value) -> None:
        data = await post_block(client, start_time=value, end_time="23:59")
        assert data["start_time"] == value

    @pytest.mark.parametrize("field", ["start_time", "end_time"])
    @pytest.mark.parametrize("value", ["07:30:00", "24:00", "7:5", "07:5", "7:05", None, 730, ""])
    async def test_bad_time_format_is_422(self, client, db_session, field, value) -> None:
        body = {
            "title": "x",
            "start_time": "07:00",
            "end_time": "08:00",
            "recurrence_type": "daily",
            "start_date": MON,
            field: value,
        }
        response = await client.post("/schedule", json=body)
        assert response.status_code == 422
        assert TIME_FORMAT_MESSAGE in _messages(response)
        assert db_session.scalars(select(ScheduleBlock)).all() == []

    @pytest.mark.parametrize("field", ["start_time", "end_time"])
    async def test_missing_time_is_422(self, client, field) -> None:
        body = {
            "title": "x",
            "start_time": "07:00",
            "end_time": "08:00",
            "recurrence_type": "daily",
            "start_date": MON,
        }
        del body[field]
        assert (await client.post("/schedule", json=body)).status_code == 422

    @pytest.mark.parametrize(
        ("start", "end"),
        [("08:00", "08:00"), ("09:00", "08:59"), ("23:59", "23:59"), ("23:59", "00:00")],
    )
    async def test_end_not_after_start_is_422(self, client, db_session, start, end) -> None:
        response = await client.post(
            "/schedule",
            json={
                "title": "x",
                "start_time": start,
                "end_time": end,
                "recurrence_type": "daily",
                "start_date": MON,
            },
        )
        assert response.status_code == 422
        assert TIME_RANGE_MESSAGE in _messages(response)
        assert db_session.scalars(select(ScheduleBlock)).all() == []

    @pytest.mark.parametrize("title", ["", "   ", "x" * 256, None])
    async def test_bad_title_is_422(self, client, title) -> None:
        response = await client.post(
            "/schedule",
            json={
                "title": title,
                "start_time": "07:00",
                "end_time": "08:00",
                "recurrence_type": "daily",
                "start_date": MON,
            },
        )
        assert response.status_code == 422

    async def test_description_too_long_is_422(self, client) -> None:
        response = await client.post(
            "/schedule",
            json={
                "title": "x",
                "description": "d" * 2001,
                "start_time": "07:00",
                "end_time": "08:00",
                "recurrence_type": "daily",
                "start_date": MON,
            },
        )
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "start_date", [None, "1999-12-31", "2101-01-01", "2024-02-30", "01.01.2024"]
    )
    async def test_bad_start_date_is_422(self, client, start_date) -> None:
        body = {
            "title": "x",
            "start_time": "07:00",
            "end_time": "08:00",
            "recurrence_type": "daily",
            "start_date": start_date,
        }
        assert (await client.post("/schedule", json=body)).status_code == 422

    async def test_start_date_is_required(self, client) -> None:
        body = {"title": "x", "start_time": "07:00", "end_time": "08:00", "recurrence_type": "daily"}
        assert (await client.post("/schedule", json=body)).status_code == 422

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
    async def test_inconsistent_recurrence_is_422(self, client, extra) -> None:
        body = {"title": "x", "start_time": "07:00", "end_time": "08:00", "start_date": MON, **extra}
        assert (await client.post("/schedule", json=body)).status_code == 422


class TestCheckConstraint:
    """The DB CHECK on its own, bypassing the API validation."""

    @pytest.mark.parametrize(("start", "end"), [(time(8, 0), time(8, 0)), (time(9, 0), time(8, 0))])
    def test_db_rejects_end_not_after_start(self, db_session, start, end) -> None:
        db_session.add(
            ScheduleBlock(
                title="x",
                start_time=start,
                end_time=end,
                recurrence_type=RecurrenceType.DAILY,
                start_date=date(2024, 1, 1),
            )
        )
        with pytest.raises(IntegrityError, match="ck_schedule_blocks_end_after_start"):
            db_session.commit()
        db_session.rollback()
        assert db_session.scalars(select(ScheduleBlock)).all() == []

    def test_db_accepts_end_after_start(self, db_session) -> None:
        db_session.add(
            ScheduleBlock(
                title="x",
                start_time=time(0, 0),
                end_time=time(23, 59),
                recurrence_type=RecurrenceType.DAILY,
                start_date=date(2024, 1, 1),
            )
        )
        db_session.commit()
        assert len(db_session.scalars(select(ScheduleBlock)).all()) == 1


# --- /day/{day} ------------------------------------------------------------------------


class TestDayRecurrence:
    async def test_daily(self, client) -> None:
        await post_block(client, title="d", recurrence_type="daily")
        for day in ("2024-01-01", "2024-01-02", "2024-12-31"):
            assert await day_titles(client, day) == ["d"]

    async def test_weekly(self, client) -> None:
        await post_block(client, title="w", recurrence_type="weekly", days_of_week=[0, 2])
        assert await day_titles(client, "2024-01-01") == ["w"]  # Monday
        assert await day_titles(client, "2024-01-02") == []  # Tuesday
        assert await day_titles(client, "2024-01-03") == ["w"]  # Wednesday
        assert await day_titles(client, "2024-01-07") == []  # Sunday
        assert await day_titles(client, "2024-01-08") == ["w"]  # next Monday

    async def test_monthly_clamps_to_month_end(self, client) -> None:
        await post_block(client, title="m", recurrence_type="monthly", day_of_month=31)
        assert await day_titles(client, "2024-01-31") == ["m"]
        assert await day_titles(client, "2024-01-30") == []
        assert await day_titles(client, "2024-02-29") == ["m"]  # leap year end
        assert await day_titles(client, "2024-02-28") == []
        assert await day_titles(client, "2024-04-30") == ["m"]
        assert await day_titles(client, "2024-04-29") == []
        assert await day_titles(client, "2025-02-28") == ["m"]

    async def test_monthly_regular_day(self, client) -> None:
        await post_block(client, title="m", recurrence_type="monthly", day_of_month=15)
        assert await day_titles(client, "2024-03-15") == ["m"]
        assert await day_titles(client, "2024-03-16") == []

    async def test_interval(self, client) -> None:
        await post_block(client, title="i", recurrence_type="interval", interval_days=3)
        assert await day_titles(client, "2024-01-01") == ["i"]  # start_date itself
        assert await day_titles(client, "2024-01-02") == []
        assert await day_titles(client, "2024-01-03") == []
        assert await day_titles(client, "2024-01-04") == ["i"]
        assert await day_titles(client, "2024-01-07") == ["i"]

    @pytest.mark.parametrize(
        "rule",
        [
            {"recurrence_type": "daily"},
            {"recurrence_type": "weekly", "days_of_week": [0, 1, 2, 3, 4, 5, 6]},
            {"recurrence_type": "monthly", "day_of_month": 10},
            {"recurrence_type": "interval", "interval_days": 1},
        ],
    )
    async def test_nothing_before_start_date(self, client, rule) -> None:
        await post_block(client, title="s", start_date="2024-01-10", **rule)
        assert await day_titles(client, "2024-01-09") == []
        assert await day_titles(client, "2023-12-09") == []
        assert await day_titles(client, "2024-01-10") == ["s"]

    async def test_mixed_rules_on_one_day(self, client) -> None:
        await post_block(client, title="daily", start_time="06:00", end_time="07:00")
        await post_block(
            client, title="tue", start_time="08:00", end_time="09:00",
            recurrence_type="weekly", days_of_week=[1],
        )
        await post_block(
            client, title="mon", start_time="10:00", end_time="11:00",
            recurrence_type="weekly", days_of_week=[0],
        )
        assert await day_titles(client, "2024-01-01") == ["daily", "mon"]
        assert await day_titles(client, "2024-01-02") == ["daily", "tue"]

    @pytest.mark.parametrize("day", ["1999-12-31", "2101-01-01", "2024-13-01", "2024-02-30", "bugün"])
    async def test_day_out_of_range_or_invalid_is_422(self, client, day) -> None:
        assert (await client.get(f"/schedule/day/{day}")).status_code == 422

    @pytest.mark.parametrize("day", ["2000-01-01", "2100-12-31"])
    async def test_day_bounds_inclusive(self, client, day) -> None:
        assert (await client.get(f"/schedule/day/{day}")).status_code == 200


# --- Ordering ---------------------------------------------------------------------------


class TestOrdering:
    async def test_ordered_by_start_time_including_bounds(self, client) -> None:
        # Created out of order on purpose; 23:58-23:59 ends at the last valid minute.
        await post_block(client, title="late", start_time="23:58", end_time="23:59")
        await post_block(client, title="noon", start_time="12:00", end_time="13:00")
        await post_block(client, title="midnight", start_time="00:00", end_time="00:01")
        await post_block(client, title="morning", start_time="07:30", end_time="08:00")
        expected = ["midnight", "morning", "noon", "late"]

        listed = [b["title"] for b in (await client.get("/schedule")).json()]
        on_day = await day_titles(client, MON)
        assert listed == expected
        assert on_day == expected

    async def test_same_start_time_ordered_by_id(self, client) -> None:
        # Includes the 00:00 bound and a block spanning the whole day up to 23:59.
        first = await post_block(client, title="a", start_time="00:00", end_time="23:59")
        second = await post_block(client, title="b", start_time="00:00", end_time="00:30")
        third = await post_block(client, title="c", start_time="00:00", end_time="01:00")
        await post_block(client, title="z", start_time="23:58", end_time="23:59")
        expected_ids = [first["id"], second["id"], third["id"]]

        with capture_sql() as sql:
            listed = (await client.get("/schedule")).json()
            on_day = (await client.get(f"/schedule/day/{MON}")).json()
        assert [b["id"] for b in listed[:3]] == expected_ids
        assert [b["id"] for b in on_day[:3]] == expected_ids
        assert listed[3]["title"] == "z" and on_day[3]["title"] == "z"

        # SQLite returns ORDER BY ties in rowid (= id) order even without the tie-breaker
        # (reverse_unordered_selects does not apply to ordered queries), so the response
        # alone cannot catch a missing id key. Assert the tie-breaker in the emitted SQL.
        selects = _selects(sql)
        assert len(selects) == 2
        for statement in selects:
            normalized = " ".join(statement.split())
            assert "ORDER BY schedule_blocks.start_time, schedule_blocks.id" in normalized

    async def test_order_follows_patched_start_time(self, client) -> None:
        a = await post_block(client, title="a", start_time="07:00", end_time="08:00")
        await post_block(client, title="b", start_time="09:00", end_time="10:00")
        await client.patch(f"/schedule/{a['id']}", json={"start_time": "11:00", "end_time": "12:00"})
        assert await day_titles(client, MON) == ["b", "a"]


# --- PATCH -------------------------------------------------------------------------------


class TestPatch:
    async def test_unsent_fields_are_preserved(self, client) -> None:
        block = await post_block(
            client, title="t", description="d", start_time="07:00", end_time="08:00",
            recurrence_type="weekly", days_of_week=[1, 3],
        )
        response = await client.patch(f"/schedule/{block['id']}", json={"title": "yeni"})
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "yeni"
        for field in ("description", "start_time", "end_time", "recurrence_type",
                      "days_of_week", "day_of_month", "interval_days", "start_date"):
            assert data[field] == block[field], field

    async def test_description_null_clears(self, client) -> None:
        block = await post_block(client, description="d")
        response = await client.patch(f"/schedule/{block['id']}", json={"description": None})
        assert response.status_code == 200
        assert response.json()["description"] is None
        assert (await client.get(f"/schedule/{block['id']}")).json()["description"] is None

    @pytest.mark.parametrize("field", ["title", "start_time", "end_time", "recurrence_type"])
    async def test_null_for_required_field_is_422(self, client, field) -> None:
        block = await post_block(client)
        response = await client.patch(f"/schedule/{block['id']}", json={field: None})
        assert response.status_code == 422
        assert f"{field} cannot be null" in _messages(response)
        assert (await client.get(f"/schedule/{block['id']}")).json() == block

    @pytest.mark.parametrize("value", ["2024-01-01", "2024-02-01", None])
    async def test_start_date_cannot_be_patched(self, client, value) -> None:
        block = await post_block(client)
        response = await client.patch(
            f"/schedule/{block['id']}", json={"title": "x", "start_date": value}
        )
        assert response.status_code == 422
        assert "start_date cannot be changed" in _messages(response)
        assert (await client.get(f"/schedule/{block['id']}")).json() == block

    @pytest.mark.parametrize("value", ["07:30:00", "24:00", "7:5"])
    async def test_patch_bad_time_format_is_422(self, client, value) -> None:
        block = await post_block(client)
        response = await client.patch(f"/schedule/{block['id']}", json={"end_time": value})
        assert response.status_code == 422
        assert TIME_FORMAT_MESSAGE in _messages(response)

    async def test_only_end_before_stored_start_is_422(self, client, db_session) -> None:
        block = await post_block(client, start_time="07:00", end_time="08:00")
        response = await client.patch(f"/schedule/{block['id']}", json={"end_time": "06:30"})
        assert response.status_code == 422
        assert response.json()["detail"] == TIME_RANGE_MESSAGE
        assert (await client.get(f"/schedule/{block['id']}")).json() == block
        db_session.expire_all()
        assert db_session.get(ScheduleBlock, block["id"]).end_time == time(8, 0)

    async def test_only_start_at_stored_end_is_422(self, client) -> None:
        block = await post_block(client, start_time="07:00", end_time="08:00")
        response = await client.patch(f"/schedule/{block['id']}", json={"start_time": "08:00"})
        assert response.status_code == 422
        assert response.json()["detail"] == TIME_RANGE_MESSAGE

    async def test_moving_both_times_uses_merged_state(self, client) -> None:
        block = await post_block(client, start_time="07:00", end_time="08:00")
        # Only start 10:00 would break against stored end 08:00 ...
        alone = await client.patch(f"/schedule/{block['id']}", json={"start_time": "10:00"})
        assert alone.status_code == 422
        # ... but sending both is valid.
        both = await client.patch(
            f"/schedule/{block['id']}", json={"start_time": "10:00", "end_time": "11:00"}
        )
        assert both.status_code == 200
        assert (both.json()["start_time"], both.json()["end_time"]) == ("10:00", "11:00")

    async def test_type_change_requires_explicit_null_of_old_fields(self, client) -> None:
        block = await post_block(client, recurrence_type="weekly", days_of_week=[0])
        bad = await client.patch(f"/schedule/{block['id']}", json={"recurrence_type": "daily"})
        assert bad.status_code == 422
        assert "days_of_week must be null" in bad.json()["detail"]
        assert (await client.get(f"/schedule/{block['id']}")).json() == block

        ok = await client.patch(
            f"/schedule/{block['id']}", json={"recurrence_type": "daily", "days_of_week": None}
        )
        assert ok.status_code == 200
        assert ok.json()["recurrence_type"] == "daily"
        assert ok.json()["days_of_week"] is None

    async def test_type_change_to_interval_needs_its_field(self, client) -> None:
        block = await post_block(client, recurrence_type="monthly", day_of_month=5)
        missing = await client.patch(
            f"/schedule/{block['id']}", json={"recurrence_type": "interval", "day_of_month": None}
        )
        assert missing.status_code == 422
        ok = await client.patch(
            f"/schedule/{block['id']}",
            json={"recurrence_type": "interval", "day_of_month": None, "interval_days": 2},
        )
        assert ok.status_code == 200
        assert (ok.json()["interval_days"], ok.json()["day_of_month"]) == (2, None)
        assert await day_titles(client, "2024-01-03") == ["Blok"]
        assert await day_titles(client, "2024-01-02") == []

    async def test_patch_changes_day_membership(self, client) -> None:
        block = await post_block(client, recurrence_type="weekly", days_of_week=[0])
        assert await day_titles(client, "2024-01-02") == []
        await client.patch(f"/schedule/{block['id']}", json={"days_of_week": [1, 1]})
        assert (await client.get(f"/schedule/{block['id']}")).json()["days_of_week"] == [1]
        assert await day_titles(client, "2024-01-02") == ["Blok"]
        assert await day_titles(client, "2024-01-01") == []


# --- Read-only GETs, query count, auth ------------------------------------------------------


class TestReadsAndAuth:
    async def test_get_endpoints_do_not_write(self, client, db_session) -> None:
        block = await post_block(client, description="d")
        await post_block(client, title="w", recurrence_type="weekly", days_of_week=[3])
        before = db_session.scalars(
            select(ScheduleBlock.id, ScheduleBlock.updated_at, ScheduleBlock.deleted_at)
        ).all()

        with capture_sql() as sql:
            for url in ("/schedule", f"/schedule/{block['id']}", f"/schedule/day/{MON}",
                        "/schedule/day/2023-01-01", "/schedule/999"):
                await client.get(url)

        assert _selects(sql), "expected the GETs to run SELECTs"
        assert _writes(sql) == []
        db_session.expire_all()
        assert db_session.scalars(
            select(ScheduleBlock.id, ScheduleBlock.updated_at, ScheduleBlock.deleted_at)
        ).all() == before

    async def test_day_query_count_does_not_grow_with_blocks(self, client) -> None:
        await post_block(client, title="b0")
        with capture_sql() as sql_one:
            assert len(await day_titles(client, MON)) == 1

        for i in range(1, 10):
            await post_block(client, title=f"b{i}", recurrence_type="weekly", days_of_week=[0])
        with capture_sql() as sql_ten:
            assert len(await day_titles(client, MON)) == 10

        assert len(_selects(sql_one)) == len(_selects(sql_ten)) == 1

    @pytest.mark.parametrize(
        ("method", "url"),
        [
            ("POST", "/schedule"),
            ("GET", "/schedule"),
            ("GET", "/schedule/day/2024-01-01"),
            ("GET", "/schedule/1"),
            ("PATCH", "/schedule/1"),
            ("DELETE", "/schedule/1"),
        ],
    )
    async def test_requires_token(self, client_no_auth, db_session, method, url) -> None:
        kwargs = {"json": {"title": "x"}} if method in ("POST", "PATCH") else {}
        response = await client_no_auth.request(method, url, **kwargs)
        assert response.status_code == 401
        assert db_session.scalars(select(ScheduleBlock)).all() == []
