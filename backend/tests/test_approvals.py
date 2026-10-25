from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from helpers import NOW, make_approval, make_case, make_offer
from agents.procurement.agent import prepare_procurement_draft
from services.warehouse_api.main import create_app
from services.warehouse_api.repository import (
    RepositoryConflict,
    RepositoryError,
    RepositoryNotFound,
    SQLiteWarehouseRepository,
)
from shared.config import Settings
from shared.models import (
    ProcurementApproval,
    RecoveryPlan,
    RecoveryPlanStatus,
    compute_plan_fingerprint,
)
from shared.state_machine import WorkflowTransitionError, transition_workflow
from tools.purchase_orders import PurchaseOrderError, prepare_purchase_order
from tools.recovery_optimizer import plan_recovery

LATER = NOW + timedelta(minutes=5)


@pytest.fixture
def sqlite_repo(tmp_path):
    db_file = tmp_path / "stockrescue_approvals_test.db"
    repo = SQLiteWarehouseRepository(db_file)
    repo.initialize()
    yield repo
    repo.close()


def test_plan_canonical_fingerprint_is_deterministic():
    case = make_case()
    plan1 = plan_recovery(case, as_of=NOW)
    plan2 = plan_recovery(case, as_of=NOW)

    fp1 = plan1.compute_fingerprint()
    fp2 = plan2.compute_fingerprint()

    assert fp1 == fp2
    assert len(fp1) == 64
    assert fp1 == compute_plan_fingerprint(plan1)


def test_plan_fingerprint_changes_on_tampering():
    case = make_case()
    plan = plan_recovery(case, as_of=NOW)
    original_fp = plan.compute_fingerprint()

    # Change line quantity
    tampered_line = plan.lines[0].model_copy(update={"quantity": 999})
    tampered_plan = plan.model_copy(update={"lines": [tampered_line]})
    assert tampered_plan.compute_fingerprint() != original_fp

    # Change unit price
    tampered_line2 = plan.lines[0].model_copy(update={"unit_price": Decimal("999.00")})
    tampered_plan2 = plan.model_copy(update={"lines": [tampered_line2]})
    assert tampered_plan2.compute_fingerprint() != original_fp

    # Change supplier
    tampered_line3 = plan.lines[0].model_copy(update={"supplier_id": "rogue-supplier"})
    tampered_plan3 = plan.model_copy(update={"lines": [tampered_line3]})
    assert tampered_plan3.compute_fingerprint() != original_fp

    # Change delivery completion date
    tampered_plan4 = plan.model_copy(update={"expected_completion_at": NOW + timedelta(days=99)})
    assert tampered_plan4.compute_fingerprint() != original_fp


def test_approval_tampering_rejected_by_verification():
    case = make_case()
    plan = plan_recovery(case, as_of=NOW)
    approval = make_approval(plan)

    # Valid approval has no violations
    assert approval.verify_against_plan(plan, at=NOW + timedelta(minutes=2)) == []

    # Tampered plan against genuine approval
    tampered_line = plan.lines[0].model_copy(update={"quantity": 999})
    tampered_plan = plan.model_copy(update={"lines": [tampered_line]})
    violations = approval.verify_against_plan(tampered_plan, at=NOW + timedelta(minutes=2))
    assert "plan_fingerprint_mismatch" in violations


def test_approval_expiry_rejection():
    case = make_case()
    plan = plan_recovery(case, as_of=NOW)
    approval = make_approval(plan, expires_at=NOW + timedelta(minutes=30))

    # Before expiry: valid
    assert not approval.is_expired(NOW + timedelta(minutes=10))

    # After expiry: rejected
    assert approval.is_expired(NOW + timedelta(minutes=31))
    violations = approval.verify_against_plan(plan, at=NOW + timedelta(minutes=31))
    assert "approval_expired" in violations


def test_approval_revocation():
    case = make_case()
    plan = plan_recovery(case, as_of=NOW)
    approval = make_approval(plan)

    assert not approval.is_revoked()
    assert approval.revoked is False

    revoked_approval = approval.model_copy(
        update={
            "revoked": True,
            "revoked_at": NOW + timedelta(minutes=10),
            "revoked_by": "auditor-1",
            "revocation_reason": "budget reconsideration",
        }
    )
    assert revoked_approval.is_revoked()
    violations = revoked_approval.verify_against_plan(plan, at=NOW + timedelta(minutes=15))
    assert "approval_revoked" in violations


def test_sqlite_approval_persistence_lifecycle(sqlite_repo):
    case = make_case(case_id="CASE-LIFECYCLE-1")
    plan = plan_recovery(case, as_of=NOW)
    approval = make_approval(plan, case_id=case.case_id)

    sqlite_repo.create_case(case)
    sqlite_repo.save_plan(plan)

    # Save approval
    saved = sqlite_repo.save_approval(approval)
    assert saved.approval_id == approval.approval_id
    assert saved.plan_fingerprint == plan.compute_fingerprint()

    # Get approval
    fetched = sqlite_repo.get_approval(approval.approval_id)
    assert fetched.approval_id == approval.approval_id
    assert fetched.plan_fingerprint == saved.plan_fingerprint

    # Active approval lookup
    active = sqlite_repo.get_active_approval(plan.plan_id, as_of=NOW + timedelta(minutes=2))
    assert active is not None
    assert active.approval_id == approval.approval_id

    # List approvals
    items, count = sqlite_repo.list_approvals(plan.plan_id)
    assert count == 1
    assert items[0].approval_id == approval.approval_id

    # Revoke approval
    revoked = sqlite_repo.revoke_approval(
        approval.approval_id,
        revoked_by="security-officer",
        reason="compromised credentials",
        at=NOW + timedelta(minutes=10),
    )
    assert revoked.is_revoked()
    assert revoked.revoked_by == "security-officer"

    # Active approval is now None
    active_after = sqlite_repo.get_active_approval(plan.plan_id, as_of=NOW + timedelta(minutes=15))
    assert active_after is None


def test_approval_rejects_fingerprint_mismatch_on_save(sqlite_repo):
    case = make_case(case_id="CASE-FP-MISMATCH")
    plan = plan_recovery(case, as_of=NOW)
    bogus_fp = "0" * 64
    approval = make_approval(plan, case_id=case.case_id, plan_fingerprint=bogus_fp)

    sqlite_repo.create_case(case)
    sqlite_repo.save_plan(plan)

    with pytest.raises(RepositoryConflict, match="fingerprint does not match"):
        sqlite_repo.save_approval(approval)


def test_approval_rejects_missing_case_or_plan(sqlite_repo):
    case = make_case(case_id="CASE-MISSING")
    plan = plan_recovery(case, as_of=NOW)
    approval = make_approval(plan, case_id=case.case_id)

    # Neither case nor plan exists
    with pytest.raises(RepositoryNotFound):
        sqlite_repo.save_approval(approval)

    # Case exists, but plan does not
    sqlite_repo.create_case(case)
    with pytest.raises(RepositoryNotFound):
        sqlite_repo.save_approval(approval)


def test_approval_rejects_duplicate_id(sqlite_repo):
    case = make_case(case_id="CASE-DUP")
    plan = plan_recovery(case, as_of=NOW)
    approval = make_approval(plan, case_id=case.case_id)

    sqlite_repo.create_case(case)
    sqlite_repo.save_plan(plan)
    sqlite_repo.save_approval(approval)

    with pytest.raises(RepositoryConflict, match="identifier already exists"):
        sqlite_repo.save_approval(approval)


def test_approval_rejects_budget_overrun(sqlite_repo):
    case = make_case(case_id="CASE-BUDGET")
    plan = plan_recovery(case, as_of=NOW)
    assert plan.status == RecoveryPlanStatus.PROPOSED
    # If approval max_total_cost is less than plan cost (250.00)
    approval_low = make_approval(plan, case_id=case.case_id, max_total_cost=Decimal("50.00"))

    sqlite_repo.create_case(case)
    sqlite_repo.save_plan(plan)

    with pytest.raises(RepositoryError, match="exceeds approved maximum"):
        sqlite_repo.save_approval(approval_low)


def test_approval_superseded_by_newer_approval(sqlite_repo):
    case = make_case(case_id="CASE-SUPERSEDED")
    plan = plan_recovery(case, as_of=NOW)

    sqlite_repo.create_case(case)
    sqlite_repo.save_plan(plan)

    appr1 = make_approval(
        plan,
        approval_id="APPR-1",
        case_id=case.case_id,
        approved_at=NOW + timedelta(minutes=1),
    )
    sqlite_repo.save_approval(appr1)

    appr2 = make_approval(
        plan,
        approval_id="APPR-2",
        case_id=case.case_id,
        approved_at=NOW + timedelta(minutes=2),
    )
    sqlite_repo.save_approval(appr2)

    active = sqlite_repo.get_active_approval(plan.plan_id, as_of=NOW + timedelta(minutes=5))
    assert active is not None
    assert active.approval_id == "APPR-2"


def test_procurement_gate_rejects_revoked_and_tampered_approval():
    case = make_case()
    plan = plan_recovery(case, as_of=NOW)
    approval = make_approval(plan, approved_at=NOW)

    # Valid approval creates draft
    result = prepare_procurement_draft(
        case,
        plan,
        approval,
        current_plan=plan,
        approval_verified=True,
        as_of=NOW,
    )
    assert result.status == "draft_prepared"

    # Revoked approval is blocked
    revoked = approval.model_copy(update={"revoked": True})
    res_revoked = prepare_procurement_draft(
        case,
        plan,
        revoked,
        current_plan=plan,
        approval_verified=True,
        as_of=NOW,
    )
    assert res_revoked.status == "blocked"
    assert "approval_revoked" in res_revoked.blockers

    # Fingerprint mismatch is blocked
    tampered_appr = approval.model_copy(update={"plan_fingerprint": "f" * 64})
    res_tampered = prepare_procurement_draft(
        case,
        plan,
        tampered_appr,
        current_plan=plan,
        approval_verified=True,
        as_of=NOW,
    )
    assert res_tampered.status == "blocked"
    assert "plan_fingerprint_mismatch" in res_tampered.blockers


def test_state_machine_enforces_approval_fingerprint_and_revocation():
    case = make_case()
    plan = plan_recovery(case, as_of=NOW)
    approval = make_approval(plan, approved_at=NOW)

    # Valid transition to approved
    res = transition_workflow(
        "awaiting_approval",
        "approved",
        actor="manager-1",
        reason="looks good",
        at=NOW + timedelta(minutes=1),
        case_id=case.case_id,
        plan=plan,
        approval=approval,
    )
    assert res.current.value == "approved"

    # Revoked approval refuses transition
    revoked = approval.model_copy(update={"revoked": True})
    with pytest.raises(WorkflowTransitionError, match="revoked"):
        transition_workflow(
            "awaiting_approval",
            "approved",
            actor="manager-1",
            reason="looks good",
            at=NOW + timedelta(minutes=1),
            case_id=case.case_id,
            plan=plan,
            approval=revoked,
        )

    # Tampered fingerprint refuses transition
    tampered = approval.model_copy(update={"plan_fingerprint": "a" * 64})
    with pytest.raises(WorkflowTransitionError, match="fingerprint"):
        transition_workflow(
            "awaiting_approval",
            "approved",
            actor="manager-1",
            reason="looks good",
            at=NOW + timedelta(minutes=1),
            case_id=case.case_id,
            plan=plan,
            approval=tampered,
        )


def test_purchase_orders_enforce_approval_fingerprint_and_revocation():
    case = make_case()
    plan = plan_recovery(case, as_of=NOW)
    approval = make_approval(plan, approved_at=NOW)

    # Normal execution prepares PO draft
    po = prepare_purchase_order(plan, approval, case, as_of=NOW + timedelta(minutes=1))
    assert po.status == "draft"

    # Revoked approval raises PurchaseOrderError
    revoked = approval.model_copy(update={"revoked": True})
    with pytest.raises(PurchaseOrderError, match="revoked"):
        prepare_purchase_order(plan, revoked, case, as_of=NOW + timedelta(minutes=1))

    # Mismatched fingerprint raises PurchaseOrderError
    tampered = approval.model_copy(update={"plan_fingerprint": "b" * 64})
    with pytest.raises(PurchaseOrderError, match="fingerprint"):
        prepare_purchase_order(plan, tampered, case, as_of=NOW + timedelta(minutes=1))


def test_warehouse_api_approval_read_routes(sqlite_repo):
    from fastapi.testclient import TestClient

    settings = Settings(database_backend="sqlite", warehouse_api_key=None)
    app = create_app(settings=settings, repository=sqlite_repo)

    case = make_case(case_id="CASE-API-TEST")
    plan = plan_recovery(case, as_of=NOW)
    approval = make_approval(
        plan,
        case_id=case.case_id,
        approved_at=NOW,
        expires_at=NOW + timedelta(days=3650),
    )

    sqlite_repo.create_case(case)
    sqlite_repo.save_plan(plan)
    sqlite_repo.save_approval(approval)

    with TestClient(app, base_url="http://testclient") as client:
        # List approvals
        res = client.get(f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals")
        assert res.status_code == 200
        data = res.json()
        assert data["count"] == 1
        assert data["items"][0]["approval_id"] == approval.approval_id

        # Active approval (query with explicit as_of timestamp)
        res_active_as_of = client.get(
            f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals/active",
            params={"as_of": NOW.isoformat()},
        )
        assert res_active_as_of.status_code == 200
        assert res_active_as_of.json()["approval_id"] == approval.approval_id

        # Active approval without as_of (uses current time, still within 3650 days)
        res_active = client.get(f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals/active")
        assert res_active.status_code == 200
        assert res_active.json()["approval_id"] == approval.approval_id

        # Get single approval
        res_single = client.get(f"/approvals/{approval.approval_id}")
        assert res_single.status_code == 200
        assert res_single.json()["approval_id"] == approval.approval_id

        # Non-existent approval gives 404
        res_404 = client.get("/approvals/NON-EXISTENT")
        assert res_404.status_code == 404
