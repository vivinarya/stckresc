from __future__ import annotations

import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from helpers import NOW, make_case
from scripts.migrate_sqlite_to_postgres import load_sqlite_data, run_migration
from scripts.seed_demo_data import main as seed_main
from services.warehouse_api.main import create_app
from services.warehouse_api.repository import (
    RepositoryConflict,
    RepositoryCorruption,
    RepositoryError,
    RepositoryNotFound,
    SQLiteWarehouseRepository,
)
from shared.config import Settings
from tools.recovery_optimizer import plan_recovery


@pytest.fixture
def sqlite_app(tmp_path):
    db_file = tmp_path / "api_test.db"
    settings = Settings(
        database_backend="sqlite",
        database_path=str(db_file),
        demo_mode=True,
        warehouse_api_key=None,
    )
    repo = SQLiteWarehouseRepository(db_file)
    app = create_app(settings=settings, repository=repo)
    with TestClient(app, base_url="http://testclient") as client:
        yield client, repo


def test_health_endpoint(sqlite_app):
    client, _ = sqlite_app
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["real_purchasing_enabled"] is False


def test_api_case_and_plan_crud(sqlite_app):
    client, repo = sqlite_app

    # 1. Create Case
    case_payload = {
        "affected_sku": "BRG-6204",
        "failed_supplier_id": "sup-1",
        "budget": "5000.00",
        "currency": "INR",
        "jobs": [
            {
                "job_id": "JOB-1",
                "sku": "BRG-6204",
                "quantity_required": 5,
                "required_by": NOW.isoformat(),
                "priority": 5,
                "description": "Urgent job",
            }
        ],
        "inventory": [
            {
                "sku": "BRG-6204",
                "quantity_on_hand": 2,
                "quantity_committed": 0,
                "unit_cost": "300.00",
                "location": "Warehouse A",
            }
        ],
        "offers": [
            {
                "offer_id": "OFFER-1",
                "supplier_id": "sup-2",
                "supplier_name": "Fast Bearings",
                "sku": "BRG-6204",
                "quantity_available": 10,
                "unit_price": "350.00",
                "freight_cost": "100.00",
                "minimum_order_quantity": 1,
                "pack_size": 1,
                "lead_time_days": 1,
                "quoted_at": NOW.isoformat(),
                "valid_until": (NOW + timedelta(days=2)).isoformat(),
            }
        ],
        "compatibility": [],
    }

    create_resp = client.post("/cases", json=case_payload)
    assert create_resp.status_code == 201
    created_case = create_resp.json()
    case_id = created_case["case_id"]
    assert created_case["affected_sku"] == "BRG-6204"

    # 2. Duplicate case creation should fail with 409
    # Build a case with the same ID directly into repo to test conflict response
    existing_case = repo.get_case(case_id)
    with patch.object(repo, "create_case", side_effect=RepositoryConflict("conflict")):
        dup_resp = client.post("/cases", json=case_payload)
        assert dup_resp.status_code == 409

    # 3. Get Case
    get_resp = client.get(f"/cases/{case_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["case_id"] == case_id

    # 4. Get Missing Case -> 404
    missing_resp = client.get("/cases/T-nonexistent-id")
    assert missing_resp.status_code == 404

    # 5. List Cases
    list_resp = client.get("/cases?limit=10&offset=0")
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert list_data["count"] >= 1
    assert any(c["case_id"] == case_id for c in list_data["items"])

    # 6. Create Plan for Case
    plan_resp = client.post(f"/cases/{case_id}/plans")
    assert plan_resp.status_code == 200
    plan_data = plan_resp.json()
    assert plan_data["case_id"] == case_id
    plan_id = plan_data["plan_id"]

    # 7. List Plans for Case
    plans_resp = client.get(f"/cases/{case_id}/plans")
    assert plans_resp.status_code == 200
    plans_data = plans_resp.json()
    assert plans_data["count"] >= 1
    assert any(p["plan_id"] == plan_id for p in plans_data["items"])

    # 8. Create Plan for Missing Case -> 404
    missing_plan_resp = client.post("/cases/T-missing-case/plans")
    assert missing_plan_resp.status_code == 404


def test_api_repository_error_handling(sqlite_app):
    client, repo = sqlite_app

    # Test RepositoryCorruption -> 500
    with patch.object(repo, "get_case", side_effect=RepositoryCorruption("corrupt")):
        resp = client.get("/cases/T-corrupt-case")
        assert resp.status_code == 500

    # Test generic RepositoryError -> 503
    with patch.object(repo, "list_cases", side_effect=RepositoryError("db unavailable")):
        resp = client.get("/cases")
        assert resp.status_code == 503


def test_seed_demo_data_fails_in_production(monkeypatch):
    from shared.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("STOCKRESCUE_ENV", "production")
    monkeypatch.setenv("DATABASE_BACKEND", "postgres")
    monkeypatch.setenv("WAREHOUSE_DATABASE_URL", "postgresql://user:pass@db1.example.com:5432/app?sslmode=require")
    monkeypatch.setenv("LANGGRAPH_CHECKPOINTS_ENABLED", "true")
    monkeypatch.setenv("LANGGRAPH_POSTGRES_URL", "postgresql://user:pass@db2.example.com:5432/ckpt?sslmode=require")
    status = seed_main()
    assert status == 1


def test_seed_demo_data_fails_when_demo_mode_false(monkeypatch):
    from shared.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("DEMO_MODE", "false")
    status = seed_main()
    assert status == 1


def test_migration_load_and_dry_run(tmp_path):
    # Set up SQLite DB with a case and plan
    db_path = tmp_path / "source.db"
    repo = SQLiteWarehouseRepository(db_path)
    repo.initialize()

    case = make_case(case_id="MIGRATE-001")
    repo.create_case(case)
    plan = plan_recovery(case, as_of=NOW)
    repo.save_plan(plan)
    repo.close()

    # Test load_sqlite_data
    cases, plans = load_sqlite_data(db_path)
    assert len(cases) == 1
    assert cases[0].item.case_id == "MIGRATE-001"
    assert len(plans) == 1
    assert plans[0].item.plan_id == plan.plan_id

    # Test dry run against simulated postgres (mock)
    with patch("scripts.migrate_sqlite_to_postgres.PostgresWarehouseRepository") as MockPgRepo:
        mock_instance = MagicMock()
        MockPgRepo.return_value = mock_instance
        # No conflict: get_case and get_plan raise RepositoryNotFound
        mock_instance.get_case.side_effect = RepositoryNotFound("not found")
        mock_instance.get_plan.side_effect = RepositoryNotFound("not found")
        mock_instance.list_cases.return_value = ([case], 1)

        # Dry run (apply=False)
        result = run_migration(
            db_path,
            "postgresql://user:pass@localhost:5432/app",
            apply=False,
            environment="development",
        )
        assert result == 0

        # Apply run (apply=True)
        conflict_calls = {"cases": 0, "plans": 0}

        def get_case_check(cid):
            conflict_calls["cases"] += 1
            if conflict_calls["cases"] == 1:
                raise RepositoryNotFound("not found")
            return case

        def get_plan_check(pid):
            conflict_calls["plans"] += 1
            if conflict_calls["plans"] == 1:
                raise RepositoryNotFound("not found")
            return plan

        mock_instance.get_case.side_effect = get_case_check
        mock_instance.get_plan.side_effect = get_plan_check

        mock_conn = MagicMock()
        mock_instance._session.return_value.__enter__.return_value = mock_conn

        apply_result = run_migration(
            db_path,
            "postgresql://user:pass@localhost:5432/app",
            apply=True,
            environment="development",
        )
        assert apply_result == 0
        mock_conn.transaction.assert_called_once()
        assert mock_conn.execute.call_count == 2


def test_coordinator_workflow_state_retrieval(tmp_path):
    from agents.coordinator.langgraph_workflow import (
        get_coordinator_workflow_state,
        run_coordinator_graph,
    )

    case = make_case(case_id="WORKFLOW-TEST-001")
    workflow_id = "WF-TEST-12345"

    result = run_coordinator_graph(
        case,
        workflow_id=workflow_id,
        as_of=NOW,
    )
    assert result.context.workflow_id == workflow_id

    # Check state retrieval when checkpoints are disabled in dev: returns None cleanly
    state = get_coordinator_workflow_state(workflow_id)
    assert state is None  # since checkpoints are disabled in development fixture
