import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from helpers import NOW, make_approval, make_case, make_offer
from shared.idempotency import (
    IdempotencyConflict,
    IdempotencyError,
    claim_idempotency,
    complete_idempotency,
    get_idempotency_record,
    make_idempotency_key,
)
from shared.models import RecoveryPlanStatus, WorkflowStatus
from shared.state_machine import WorkflowTransitionError, transition_workflow
from tools.purchase_orders import PurchaseOrderError, prepare_purchase_order
from tools.recovery_optimizer import plan_recovery

LATER = NOW + timedelta(minutes=5)


def plan_and_approval(**overrides):
    plan = plan_recovery(make_case(), as_of=NOW)
    return plan, make_approval(plan, **overrides)


def approve(plan, approval, **overrides):
    values = dict(actor="manager-1", reason="reviewed", at=LATER,
                  case_id=plan.case_id, plan=plan, approval=approval)
    values.update(overrides)
    return transition_workflow("awaiting_approval", "approved", **values)


# state machine
def test_allowed_transition_is_recorded():
    result = transition_workflow("created", "investigating", actor="agent-1",
                                 reason="start", at=NOW)
    assert (result.previous, result.current) == (
        WorkflowStatus.CREATED, WorkflowStatus.INVESTIGATING)


@pytest.mark.parametrize(
    "current, target",
    [("completed", "planning"), ("cancelled", "investigating"), ("created", "approved"),
     ("investigating", "awaiting_approval")],
)
def test_illegal_transitions_are_refused(current, target):
    with pytest.raises(WorkflowTransitionError):
        transition_workflow(current, target, actor="agent-1", reason="x", at=NOW)


def test_live_procurement_submission_is_never_allowed():
    with pytest.raises(WorkflowTransitionError, match="not implemented"):
        transition_workflow("approved", "procurement_submitted", actor="agent-1",
                            reason="x", at=NOW)


@pytest.mark.parametrize(
    "overrides",
    [
        dict(actor="bad actor!"),
        dict(actor=""),
        dict(reason=""),
        dict(reason="a" * 501),
        dict(reason="line\x00break"),
        dict(at=datetime(2026, 1, 1)),
        dict(at=datetime.now(timezone.utc) + timedelta(hours=1)),
    ],
)
def test_transition_inputs_are_validated(overrides):
    values = dict(actor="agent-1", reason="x", at=NOW)
    values.update(overrides)
    with pytest.raises(WorkflowTransitionError):
        transition_workflow("created", "investigating", **values)


def test_unknown_status_is_refused():
    with pytest.raises(WorkflowTransitionError):
        transition_workflow("nope", "investigating", actor="a", reason="x", at=NOW)


def test_valid_approval_moves_plan_to_approved():
    plan, approval = plan_and_approval()
    assert approve(plan, approval).current == WorkflowStatus.APPROVED


def test_approval_requires_plan_and_approval_objects():
    with pytest.raises(WorkflowTransitionError):
        transition_workflow("awaiting_approval", "approved", actor="m", reason="x",
                            at=LATER, case_id="CASE-1")


@pytest.mark.parametrize(
    "overrides",
    [
        dict(expires_at=NOW + timedelta(minutes=2)),
        dict(plan_id="another-plan"),
        dict(case_id="OTHER-CASE"),
        dict(max_total_cost=Decimal("1.00")),
        dict(approved_at=NOW - timedelta(minutes=1)),
    ],
)
def test_invalid_approvals_are_refused(overrides):
    plan, approval = plan_and_approval(**overrides)
    with pytest.raises(WorkflowTransitionError):
        approve(plan, approval)


def test_infeasible_plan_cannot_be_approved():
    plan = plan_recovery(make_case(budget=Decimal("1.00")), as_of=NOW)
    assert plan.status == RecoveryPlanStatus.NO_FEASIBLE_PLAN
    with pytest.raises(WorkflowTransitionError):
        approve(plan, make_approval(plan))


# idempotency
@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


def test_key_is_stable_and_order_independent():
    first = make_idempotency_key("purchase-order", {"a": 1, "b": [1, 2]})
    second = make_idempotency_key("purchase-order", {"b": [1, 2], "a": 1})
    assert first == second and first.startswith("purchase-order:")
    assert first != make_idempotency_key("purchase-order", {"a": 2, "b": [1, 2]})


@pytest.mark.parametrize("payload", [{"x": float("nan")}, {"x": object()}, {"x": "a\x00"}])
def test_key_rejects_non_json_payloads(payload):
    with pytest.raises(IdempotencyError):
        make_idempotency_key("purchase-order", payload)


def test_only_first_claim_is_acquired(db):
    key = make_idempotency_key("po", {"n": 1})
    assert claim_idempotency(db, key, {"n": 1}).acquired
    again = claim_idempotency(db, key, {"n": 1})
    assert not again.acquired and again.status == "in_progress"


def test_same_key_with_different_payload_conflicts(db):
    key = make_idempotency_key("po", {"n": 1})
    claim_idempotency(db, key, {"n": 1})
    with pytest.raises(IdempotencyConflict):
        claim_idempotency(db, key, {"n": 2})


def test_completion_is_recorded_and_replay_safe(db):
    key = make_idempotency_key("po", {"n": 1})
    claim_idempotency(db, key, {"n": 1})
    done = complete_idempotency(db, key, {"ok": True})
    assert done.status == "completed"
    assert complete_idempotency(db, key, {"ok": True}).status == "completed"
    with pytest.raises(IdempotencyConflict):
        complete_idempotency(db, key, {"ok": False})
    assert get_idempotency_record(db, key).response_json == '{"ok":true}'


def test_completing_an_unclaimed_key_fails_and_bad_keys_are_rejected(db):
    with pytest.raises(IdempotencyError):
        complete_idempotency(db, "po:unclaimed", {"ok": True})
    with pytest.raises(IdempotencyError):
        claim_idempotency(db, "bad key; DROP TABLE", {"n": 1})
    assert get_idempotency_record(db, "po:missing") is None


# purchase order draft
def test_draft_matches_approved_plan_and_is_idempotent():
    plan, approval = plan_and_approval()
    first = prepare_purchase_order(plan, approval, make_case(), as_of=LATER)
    second = prepare_purchase_order(plan, approval, make_case(), as_of=LATER + timedelta(minutes=1))
    assert first.status == "draft"
    assert first.total_cost == Decimal("250.00") and first.supplier_id == "supplier-b"
    assert (first.lines[0].quantity, first.lines[0].unit_price) == (2, Decimal("100.00"))
    assert first.idempotency_key == second.idempotency_key
    assert first.draft_id == second.draft_id


@pytest.mark.parametrize(
    "case_overrides, approval_overrides",
    [
        (dict(offers=[make_offer(unit_price=Decimal("110.00"))]), {}),
        (dict(offers=[make_offer(quantity_available=1)]), {}),
        (dict(offers=[make_offer(supplier_id="supplier-a")]), {}),
        (dict(offers=[]), {}),
        (dict(inventory=[]), {}),
        ({}, dict(expires_at=NOW + timedelta(minutes=2))),
        ({}, dict(plan_id="another-plan")),
        ({}, dict(approved_at=NOW - timedelta(minutes=1))),
        ({}, dict(currency="USD")),
    ],
)
def test_draft_is_refused_when_reality_has_changed(case_overrides, approval_overrides):
    plan, approval = plan_and_approval(**approval_overrides)
    with pytest.raises(PurchaseOrderError):
        prepare_purchase_order(plan, approval, make_case(**case_overrides), as_of=LATER)


def test_draft_is_refused_when_stock_now_covers_demand():
    from shared.models import InventoryRecord
    plan, approval = plan_and_approval()
    stocked = make_case(inventory=[InventoryRecord(sku="BRG-6204", quantity_on_hand=10)])
    with pytest.raises(PurchaseOrderError, match="no longer supports"):
        prepare_purchase_order(plan, approval, stocked, as_of=LATER)


def test_draft_needs_a_time_limited_quote():
    case = make_case(offers=[make_offer(valid_until=None)])
    plan = plan_recovery(case, as_of=NOW)
    approval = make_approval(plan)
    with pytest.raises(PurchaseOrderError, match="time-limited"):
        prepare_purchase_order(plan, approval, case, as_of=LATER)


def test_infeasible_plan_never_becomes_a_draft():
    case = make_case(budget=Decimal("1.00"))
    plan = plan_recovery(case, as_of=NOW)
    with pytest.raises(PurchaseOrderError):
        prepare_purchase_order(plan, make_approval(plan), case, as_of=LATER)
