import os
import sqlite3
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import ForeignKeyViolation, UniqueViolation
from psycopg_pool import PoolTimeout

from helpers import NOW, make_case
from services.warehouse_api.postgres_repository import PostgresWarehouseRepository
from services.warehouse_api.repository import (
    RepositoryConflict,
    RepositoryCorruption,
    RepositoryError,
    RepositoryNotFound,
    SQLiteWarehouseRepository,
    WarehouseRepository,
    get_repository,
)
from tools.recovery_optimizer import plan_recovery
from shared.models import AuditEvent, DeliveryEvent

from tests.pg_support import assert_connected_to_test_database, validated_test_url

# dedicated throwaway database only; never fall back to the application database
POSTGRES_URL = validated_test_url()
NEEDS_POSTGRES = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="STOCKRESCUE_TEST_POSTGRES_URL is not set"),
]


class Backend:
    def __init__(self, repo, corrupt):
        self.repo, self._corrupt, self.ids = repo, corrupt, []

    def new_case(self, **overrides):
        case_id = f"T{uuid4().hex[:12]}"
        self.ids.append(case_id)
        return make_case(case_id=case_id, **overrides)

    def corrupt(self, table, column, key):
        self._corrupt(table, column, key)


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=NEEDS_POSTGRES)])
def backend(request, tmp_path):
    if request.param == "sqlite":
        repo = SQLiteWarehouseRepository(tmp_path / "test.db")
        repo.initialize()

        def corrupt(table, column, key):
            with sqlite3.connect(repo.database_path) as connection:
                connection.execute(
                    f"UPDATE {table} SET payload_json = '{{' WHERE {column} = ?", (key,))

        yield Backend(repo, corrupt)
        return

    assert_connected_to_test_database(POSTGRES_URL)
    repo = PostgresWarehouseRepository(POSTGRES_URL, environment="test")
    repo.initialize()

    def corrupt(table, column, key):
        with psycopg.connect(POSTGRES_URL, autocommit=True) as connection:
            connection.execute(
                f"UPDATE {table} SET payload_json = '{{' WHERE {column} = %s", (key,))

    state = Backend(repo, corrupt)
    yield state
    with psycopg.connect(POSTGRES_URL, autocommit=True) as connection:
        for case_id in state.ids:
            connection.execute("DELETE FROM disruption_cases WHERE case_id = %s", (case_id,))
    repo.close()


def test_case_roundtrip_is_exact(backend):
    case = backend.new_case()
    assert backend.repo.create_case(case) == case
    assert backend.repo.get_case(case.case_id) == case


def test_duplicate_case_is_a_conflict(backend):
    case = backend.new_case()
    backend.repo.create_case(case)
    with pytest.raises(RepositoryConflict):
        backend.repo.create_case(case)


def test_missing_case_is_not_found(backend):
    with pytest.raises(RepositoryNotFound):
        backend.repo.get_case("T-does-not-exist")


@pytest.mark.parametrize("bad_id", ["../etc/passwd", "a b", "", "x" * 101, "id;DROP TABLE x"])
def test_malformed_identifiers_are_rejected_before_any_query(backend, bad_id):
    with pytest.raises(RepositoryError) as error:
        backend.repo.get_case(bad_id)
    assert error.type is RepositoryError


def test_only_validated_models_are_accepted(backend):
    with pytest.raises(RepositoryError):
        backend.repo.create_case({"case_id": "x"})
    with pytest.raises(RepositoryError):
        backend.repo.save_plan("not a plan")


def test_listing_is_paginated_and_bounded(backend):
    cases = [backend.new_case() for _ in range(3)]
    for case in cases:
        backend.repo.create_case(case)
    page, total = backend.repo.list_cases(limit=2, offset=0)
    assert len(page) == 2 and total >= 3
    everything, _ = backend.repo.list_cases(limit=100, offset=0)
    assert {case.case_id for case in cases} <= {item.case_id for item in everything}
    for limit, offset in [(0, 0), (101, 0), (1, -1), (1, 1_000_001), (True, 0)]:
        with pytest.raises(RepositoryError):
            backend.repo.list_cases(limit=limit, offset=offset)


def test_plan_lifecycle(backend):
    case = backend.new_case()
    backend.repo.create_case(case)
    plan = plan_recovery(case, as_of=NOW)
    assert backend.repo.save_plan(plan) == plan
    assert backend.repo.get_plan(plan.plan_id) == plan
    plans, total = backend.repo.list_plans(case.case_id)
    assert total == 1 and plans == [plan]
    with pytest.raises(RepositoryConflict):
        backend.repo.save_plan(plan)


def test_plan_for_unknown_case_is_not_found(backend):
    plan = plan_recovery(make_case(case_id="T-never-stored"), as_of=NOW)
    with pytest.raises(RepositoryNotFound):
        backend.repo.save_plan(plan)
    with pytest.raises(RepositoryNotFound):
        backend.repo.list_plans("T-never-stored")
    with pytest.raises(RepositoryNotFound):
        backend.repo.get_plan("T-no-such-plan")


def test_corrupt_stored_data_is_reported_not_returned(backend):
    case = backend.new_case()
    backend.repo.create_case(case)
    plan = plan_recovery(case, as_of=NOW)
    backend.repo.save_plan(plan)
    backend.corrupt("recovery_plans", "plan_id", plan.plan_id)
    with pytest.raises(RepositoryCorruption):
        backend.repo.get_plan(plan.plan_id)
    backend.corrupt("disruption_cases", "case_id", case.case_id)
    with pytest.raises(RepositoryCorruption):
        backend.repo.get_case(case.case_id)


# postgres error translation, exercised offline with a stub pool
class StubConnection:
    def __init__(self, error):
        self.error = error

    def execute(self, *args, **kwargs):
        raise self.error

    @contextmanager
    def transaction(self):
        yield


class StubPool:
    def __init__(self, error):
        self.error = error

    @contextmanager
    def connection(self):
        if isinstance(self.error, PoolTimeout):
            raise self.error
        yield StubConnection(self.error)

    def close(self):
        pass


def stub_repo(error):
    repo = PostgresWarehouseRepository(
        "postgresql://user:topsecret@localhost:5432/db", environment="test")
    repo._pool = StubPool(error)
    return repo


def test_postgres_unique_violation_becomes_conflict():
    with pytest.raises(RepositoryConflict):
        stub_repo(UniqueViolation("duplicate key")).create_case(make_case())
    plan = plan_recovery(make_case(), as_of=NOW)
    with pytest.raises(RepositoryConflict):
        stub_repo(UniqueViolation("duplicate key")).save_plan(plan)


def test_postgres_foreign_key_violation_becomes_not_found():
    plan = plan_recovery(make_case(), as_of=NOW)
    with pytest.raises(RepositoryNotFound):
        stub_repo(ForeignKeyViolation("no such case")).save_plan(plan)


@pytest.mark.parametrize(
    "error", [psycopg.OperationalError("password=topsecret refused"), PoolTimeout("busy")])
def test_postgres_outages_become_safe_repository_errors(error):
    with pytest.raises(RepositoryError) as caught:
        stub_repo(error).get_case("T-1")
    assert caught.type is RepositoryError
    assert "topsecret" not in str(caught.value)


def test_postgres_repository_must_be_initialized_first():
    repo = PostgresWarehouseRepository(
        "postgresql://user:topsecret@localhost:5432/db", environment="test")
    with pytest.raises(RepositoryError):
        repo.get_case("T-1")


@pytest.mark.parametrize(
    "url, environment",
    [
        ("postgresql://user:topsecret@db.example.com/app", "development"),
        ("postgresql://user:topsecret@db.example.com/app?sslmode=disable", "production"),
        ("postgresql://user:topsecret@localhost/app", "production"),
        ("mysql://user:topsecret@localhost/app", "development"),
        ("postgresql://user:topsecret@localhost", "development"),
        ("postgresql://user:topsecret@localhost/app\n", "development"),
        ("", "development"),
        ("postgresql://user:topsecret@localhost/app", "staging"),
    ],
)
def test_postgres_repository_rejects_unsafe_configuration(url, environment):
    with pytest.raises(RepositoryError) as caught:
        PostgresWarehouseRepository(url, environment=environment)
    assert "topsecret" not in str(caught.value)


def test_repository_protocol_conformance(tmp_path):
    sqlite_repo = SQLiteWarehouseRepository(tmp_path / "sqlite_proto.db")
    assert isinstance(sqlite_repo, WarehouseRepository)
    sqlite_repo.close()

    pg_repo = PostgresWarehouseRepository(
        "postgresql://user:s3cret@localhost:5432/app", environment="development"
    )
    assert isinstance(pg_repo, WarehouseRepository)
    pg_repo.close()


def test_repository_factory_selection(tmp_path):
    from shared.config import Settings

    sqlite_settings = Settings(
        database_backend="sqlite",
        database_path=str(tmp_path / "factory.db"),
        stockrescue_env="development",
    )
    repo = get_repository(sqlite_settings)
    assert isinstance(repo, SQLiteWarehouseRepository)
    repo.close()

    pg_settings = Settings(
        database_backend="postgres",
        warehouse_database_url="postgresql://user:s3cret@localhost:5432/app",
        stockrescue_env="development",
    )
    pg_repo = get_repository(pg_settings)
    assert isinstance(pg_repo, PostgresWarehouseRepository)
    pg_repo.close()

    missing_url_settings = Settings.model_construct(
        database_backend="postgres",
        warehouse_database_url=None,
        stockrescue_env="development",
    )
    with pytest.raises(RepositoryError):
        get_repository(missing_url_settings)

    prod_sqlite_settings = Settings.model_construct(
        database_backend="sqlite",
        stockrescue_env="production",
    )
    with pytest.raises(RepositoryError):
        get_repository(prod_sqlite_settings)


def test_computed_fields_and_decimal_roundtrip(backend):
    from decimal import Decimal

    case = backend.new_case(budget=Decimal("4500.50"))
    stored = backend.repo.create_case(case)
    retrieved = backend.repo.get_case(case.case_id)

    assert retrieved.budget == Decimal("4500.50")
    for inv in retrieved.inventory:
        assert inv.available_quantity == inv.quantity_on_hand - inv.quantity_committed

    plan = plan_recovery(case, as_of=NOW)
    backend.repo.save_plan(plan)
    retrieved_plan = backend.repo.get_plan(plan.plan_id)

    assert retrieved_plan.total_cost == plan.total_cost
    for line in retrieved_plan.lines:
        assert line.line_total == line.unit_price * line.quantity


def test_sqlite_schema_versioning(tmp_path):
    db_path = tmp_path / "versioned.db"
    repo = SQLiteWarehouseRepository(db_path)
    repo.initialize()

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT version, description FROM schema_version WHERE version = 1").fetchone()
        assert row is not None
        assert row[0] == 1

        # Simulate a database created with a newer code version
        conn.execute("INSERT OR REPLACE INTO schema_version (version, applied_at, description) VALUES (999, '2026-01-01', 'future')")

    repo2 = SQLiteWarehouseRepository(db_path)
    with pytest.raises(RepositoryError) as exc_info:
        repo2.initialize()
    assert "exceeds supported version" in str(exc_info.value)


def test_audit_event_roundtrip(backend):
    case = backend.new_case()
    backend.repo.create_case(case)
    event = AuditEvent(
        event_type="test_action",
        entity_type="disruption_case",
        entity_id=case.case_id,
        actor_id="test-operator",
        details={"note": "test audit details"},
    )
    backend.repo.record_audit_event(event)

    events, count = backend.repo.list_audit_events(case.case_id)
    assert count == 1
    assert events[0].event_id == event.event_id
    assert events[0].actor_id == "test-operator"
    assert events[0].details == {"note": "test audit details"}


def test_delivery_event_roundtrip(backend):
    case = backend.new_case()
    backend.repo.create_case(case)
    plan = plan_recovery(case)
    backend.repo.save_plan(plan)

    event = DeliveryEvent(
        event_id=f"DEL-{uuid4().hex[:8].upper()}",
        plan_id=plan.plan_id,
        status="in_transit",
        occurred_at=NOW,
        source="carrier_api",
        reference="TRK-100",
        details="Dispatched from hub",
    )
    backend.repo.record_delivery_event(event)

    events, count = backend.repo.list_delivery_events(plan.plan_id)
    assert count == 1
    assert events[0].event_id == event.event_id
    assert events[0].status == "in_transit"
    assert events[0].reference == "TRK-100"
