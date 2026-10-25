from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

from helpers import NOW, make_case
from services.warehouse_api.main import create_app
from services.warehouse_api.repository import (
    RepositoryConflict,
    RepositoryNotFound,
    SQLiteWarehouseRepository,
)
from shared.config import Settings
from shared.models import DeliveryEvent, RecoveryPlan, RecoveryPlanStatus
from tools.recovery_optimizer import plan_recovery


@pytest.fixture
def sqlite_app(tmp_path):
    db_file = tmp_path / "delivery_test.db"
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


def test_delivery_event_timestamp_requires_timezone():
    with pytest.raises(ValueError, match="timezone"):
        DeliveryEvent(
            event_id="DEL-1",
            plan_id="plan-1",
            status="in_transit",
            occurred_at=datetime(2026, 10, 23, 12, 0, 0),  # naive
            source="carrier_api",
        )


def test_delivery_event_persistence_repository(tmp_path):
    repo = SQLiteWarehouseRepository(tmp_path / "repo_delivery.db")
    repo.initialize()

    case = make_case()
    repo.create_case(case)
    plan = plan_recovery(case)
    assert plan is not None
    repo.save_plan(plan)

    now = datetime.now(timezone.utc)
    event1 = DeliveryEvent(
        event_id="DEL-001",
        plan_id=plan.plan_id,
        status="in_transit",
        occurred_at=now,
        source="carrier_api",
        reference="TRK-98765",
        details="Package scanned at sorting facility",
    )
    saved = repo.record_delivery_event(event1)
    assert saved.event_id == "DEL-001"
    assert saved.status == "in_transit"

    # List events
    events, count = repo.list_delivery_events(plan.plan_id)
    assert count == 1
    assert len(events) == 1
    assert events[0].event_id == "DEL-001"
    assert events[0].reference == "TRK-98765"

    # Reject duplicate event_id
    with pytest.raises(RepositoryConflict):
        repo.record_delivery_event(event1)

    # Reject event for non-existent plan
    event_bad_plan = DeliveryEvent(
        event_id="DEL-002",
        plan_id="non-existent-plan",
        status="delivered",
        occurred_at=now,
        source="carrier_api",
    )
    with pytest.raises(RepositoryNotFound):
        repo.record_delivery_event(event_bad_plan)

    # Listing for non-existent plan raises RepositoryNotFound
    with pytest.raises(RepositoryNotFound):
        repo.list_delivery_events("non-existent-plan")


def test_delivery_event_api_endpoints(sqlite_app):
    client, repo = sqlite_app

    case = make_case()
    repo.create_case(case)
    plan = plan_recovery(case)
    assert plan is not None
    repo.save_plan(plan)

    # 1. Delivery status before any events -> unknown
    status_resp = client.get(f"/cases/{case.case_id}/plans/{plan.plan_id}/delivery-status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["status"] == "unknown"
    assert status_data["accepted_event_count"] == 0

    # 2. Record delivery event via API
    now = datetime.now(timezone.utc)
    create_resp = client.post(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/delivery-events",
        json={
            "status": "in_transit",
            "occurred_at": now.isoformat(),
            "source": "carrier_api",
            "reference": "CARRIER-123",
            "details": "Out for delivery",
        },
    )
    assert create_resp.status_code == 201
    created_event = create_resp.json()
    assert created_event["event_id"].startswith("DEL-")
    assert created_event["status"] == "in_transit"

    # 3. List events via API
    list_resp = client.get(f"/cases/{case.case_id}/plans/{plan.plan_id}/delivery-events")
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert list_data["count"] == 1
    assert list_data["items"][0]["status"] == "in_transit"

    # 4. Check delivery status again -> in_progress
    status_resp2 = client.get(f"/cases/{case.case_id}/plans/{plan.plan_id}/delivery-status")
    assert status_resp2.status_code == 200
    assert status_resp2.json()["status"] == "in_progress"
    assert status_resp2.json()["accepted_event_count"] == 1

    # 5. Record delivered event
    delivered_time = now + timedelta(hours=2)
    client.post(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/delivery-events",
        json={
            "status": "delivered",
            "occurred_at": delivered_time.isoformat(),
            "source": "carrier_api",
            "reference": "CARRIER-123",
            "details": "Delivered to reception",
        },
    )

    # Check status with as_of after delivery
    as_of_time = delivered_time + timedelta(minutes=5)
    status_resp3 = client.get(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/delivery-status",
        params={"as_of": as_of_time.isoformat()},
    )
    assert status_resp3.status_code == 200
    assert status_resp3.json()["status"] == "delivered"
    assert status_resp3.json()["accepted_event_count"] == 2

    # 6. Mismatched case and plan returns 404
    bad_resp = client.post(
        f"/cases/other-case/plans/{plan.plan_id}/delivery-events",
        json={
            "status": "delivered",
            "occurred_at": now.isoformat(),
            "source": "carrier_api",
        },
    )
    assert bad_resp.status_code == 404


def test_audit_event_recorded_on_delivery_event(sqlite_app):
    client, repo = sqlite_app

    case = make_case()
    repo.create_case(case)
    plan = plan_recovery(case)
    assert plan is not None
    repo.save_plan(plan)

    now = datetime.now(timezone.utc)
    client.post(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/delivery-events",
        json={
            "event_id": "DEL-AUDIT-1",
            "status": "confirmed",
            "occurred_at": now.isoformat(),
            "source": "authorized_operator",
            "details": "Operator confirmed shipment dispatch",
        },
    )

    audit_events, count = repo.list_audit_events(plan.plan_id)
    delivery_audits = [a for a in audit_events if a.event_type == "delivery_event.recorded"]
    assert len(delivery_audits) >= 1
    assert delivery_audits[0].details["event_id"] == "DEL-AUDIT-1"
    assert delivery_audits[0].details["status"] == "confirmed"
