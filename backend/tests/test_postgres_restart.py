from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from helpers import NOW, make_approval, make_case
from tests.pg_support import (
    EXPECTED_DATABASE,
    UnsafeTestDatabase,
    assert_connected_to_test_database,
    validated_test_url,
)
from tools.recovery_optimizer import plan_recovery
from shared.models import AuditEvent

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

TEST_POSTGRES_URL = validated_test_url()
SKIP_REASON = "STOCKRESCUE_TEST_POSTGRES_URL is not set or not dedicated test DB"
NEEDS_REAL_POSTGRES = pytest.mark.skipif(not TEST_POSTGRES_URL, reason=SKIP_REASON)


def test_test_database_safety_guard():
    # Proves the test suite fails closed against accidental production/Supabase runs
    with pytest.raises(UnsafeTestDatabase):
        from tests.pg_support import assert_connected_to_test_database
        assert_connected_to_test_database("postgresql://user:pass@remote.example.com:5432/stockrescue_test")

    with pytest.raises(UnsafeTestDatabase):
        from tests.pg_support import assert_connected_to_test_database
        assert_connected_to_test_database("postgresql://user:pass@localhost:5432/production_db")


@NEEDS_REAL_POSTGRES
def test_case_and_plan_survive_process_restart():
    assert_connected_to_test_database(TEST_POSTGRES_URL)
    from services.warehouse_api.postgres_repository import PostgresWarehouseRepository

    repo = PostgresWarehouseRepository(TEST_POSTGRES_URL, environment="test")
    repo.initialize()

    case_id = f"T-RESTART-{uuid4().hex[:8]}"
    case = make_case(case_id=case_id)
    plan = plan_recovery(case, as_of=NOW)

    repo.create_case(case)
    repo.save_plan(plan)
    repo.close()

    # Launch a completely separate Python worker process to read back the records
    env = os.environ.copy()
    env["DATABASE_BACKEND"] = "postgres"
    env["WAREHOUSE_DATABASE_URL"] = TEST_POSTGRES_URL
    env["STOCKRESCUE_ENV"] = "test"

    proc = subprocess.run(
        [PYTHON, str(BACKEND_ROOT / "tests" / "pg_worker.py"), "read-records", case.case_id, plan.plan_id],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        cwd=str(BACKEND_ROOT),
    )
    data = json.loads(proc.stdout.strip())
    assert data["case_id"] == case.case_id
    assert data["plan_case_id"] == case.case_id
    assert data["total_cost"] == str(plan.total_cost)

    # Clean up test rows
    repo.initialize()
    with repo._session() as conn:
        with conn.transaction():
            conn.execute("DELETE FROM disruption_cases WHERE case_id = %s", (case_id,))
    repo.close()


@NEEDS_REAL_POSTGRES
def test_langgraph_checkpoint_restored_after_process_restart():
    assert_connected_to_test_database(TEST_POSTGRES_URL)

    workflow_id = f"WF-RESTART-{uuid4().hex[:8]}"
    case_id = f"T-CKPT-{uuid4().hex[:8]}"

    env = os.environ.copy()
    env["DATABASE_BACKEND"] = "postgres"
    env["WAREHOUSE_DATABASE_URL"] = TEST_POSTGRES_URL
    env["LANGGRAPH_CHECKPOINTS_ENABLED"] = "true"
    env["LANGGRAPH_POSTGRES_URL"] = TEST_POSTGRES_URL
    env["STOCKRESCUE_ENV"] = "test"

    # Step 1: Run workflow in worker Process A, which saves checkpoint to PostgreSQL and terminates
    proc_write = subprocess.run(
        [PYTHON, str(BACKEND_ROOT / "tests" / "pg_worker.py"), "write-workflow", workflow_id, case_id],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        cwd=str(BACKEND_ROOT),
    )
    result_write = json.loads(proc_write.stdout.strip())
    assert result_write["workflow_id"] == workflow_id

    # Step 2: In a new, separate worker Process B, retrieve prior state from checkpoint store
    proc_read = subprocess.run(
        [PYTHON, str(BACKEND_ROOT / "tests" / "pg_worker.py"), "read-workflow", workflow_id],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        cwd=str(BACKEND_ROOT),
    )
    result_read = json.loads(proc_read.stdout.strip())
    assert result_read["found"] is True
    assert result_read["case_id"] == case_id
    assert result_read["outcome"] == result_write["outcome"]
    assert result_read["procurement_authorized"] is False


@NEEDS_REAL_POSTGRES
def test_approval_survives_process_restart():
    assert_connected_to_test_database(TEST_POSTGRES_URL)
    from services.warehouse_api.postgres_repository import PostgresWarehouseRepository

    repo = PostgresWarehouseRepository(TEST_POSTGRES_URL, environment="test")
    repo.initialize()

    case_id = f"T-APPR-{uuid4().hex[:8]}"
    case = make_case(case_id=case_id)
    plan = plan_recovery(case, as_of=NOW)
    approval = make_approval(plan, case_id=case.case_id)

    repo.create_case(case)
    repo.save_plan(plan)
    saved_appr = repo.save_approval(approval)
    audit_ev = AuditEvent(
        event_type="approval_created",
        entity_type="recovery_plan",
        entity_id=plan.plan_id,
        actor_id="test-operator-pg",
        details={"approval_id": saved_appr.approval_id},
    )
    repo.record_audit_event(audit_ev)
    repo.close()

    env = os.environ.copy()
    env["DATABASE_BACKEND"] = "postgres"
    env["WAREHOUSE_DATABASE_URL"] = TEST_POSTGRES_URL
    env["STOCKRESCUE_ENV"] = "test"

    proc = subprocess.run(
        [PYTHON, str(BACKEND_ROOT / "tests" / "pg_worker.py"), "read-approval", saved_appr.approval_id],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        cwd=str(BACKEND_ROOT),
    )
    data = json.loads(proc.stdout.strip())
    assert data["approval_id"] == saved_appr.approval_id
    assert data["case_id"] == case_id
    assert data["plan_id"] == plan.plan_id
    assert data["plan_fingerprint"] == plan.compute_fingerprint()
    assert data["revoked"] is False

    proc_audit = subprocess.run(
        [PYTHON, str(BACKEND_ROOT / "tests" / "pg_worker.py"), "read-audit", plan.plan_id],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        cwd=str(BACKEND_ROOT),
    )
    audit_data = json.loads(proc_audit.stdout.strip())
    assert audit_data["count"] >= 1
    assert audit_data["events"][0]["event_type"] == "approval_created"
    assert audit_data["events"][0]["actor_id"] == "test-operator-pg"

    # Clean up test rows
    repo.initialize()
    with repo._session() as conn:
        with conn.transaction():
            conn.execute("DELETE FROM disruption_cases WHERE case_id = %s", (case_id,))
            conn.execute("DELETE FROM audit_events WHERE entity_id = %s", (plan.plan_id,))
    repo.close()
