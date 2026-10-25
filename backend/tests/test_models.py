from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from helpers import NOW, SKU, make_case, make_offer
from shared.models import (
    AgentMessage,
    InventoryRecord,
    ProcurementApproval,
    RecoveryLine,
    RecoveryPlan,
    RecoveryPlanStatus,
)


def message(payload):
    return AgentMessage(
        workflow_id="wf-1", trace_id="tr-1", sender="a", recipient="b",
        message_type="inventory.result", payload=payload,
    )


def test_float_money_is_rejected():
    with pytest.raises(ValidationError):
        make_offer(unit_price=100.5)


def test_decimal_string_money_is_accepted():
    assert make_offer(unit_price="100.50").unit_price == Decimal("100.50")


def test_money_precision_is_bounded():
    with pytest.raises(ValidationError):
        make_offer(unit_price=Decimal("1.234"))


def test_naive_datetime_is_rejected():
    with pytest.raises(ValidationError):
        make_offer(quoted_at=datetime(2026, 1, 1))


def test_sku_is_normalised_to_uppercase():
    assert InventoryRecord(sku="brg-6204", quantity_on_hand=1).sku == SKU


@pytest.mark.parametrize("sku", ["", "bad sku", "-LEADING", "A" * 65])
def test_invalid_sku_is_rejected(sku):
    with pytest.raises(ValidationError):
        InventoryRecord(sku=sku, quantity_on_hand=1)


def test_unknown_fields_are_forbidden():
    with pytest.raises(ValidationError):
        make_offer(surprise="x")


def test_control_characters_in_text_are_rejected():
    with pytest.raises(ValidationError):
        InventoryRecord(sku=SKU, quantity_on_hand=1, description="a\x00b")


def test_available_quantity_is_computed():
    record = InventoryRecord(sku=SKU, quantity_on_hand=8, quantity_committed=5)
    assert record.available_quantity == 3


def test_quote_expiry_must_follow_quote_time():
    with pytest.raises(ValidationError):
        make_offer(valid_until=NOW - timedelta(hours=2))


def test_case_rejects_offers_in_another_currency():
    with pytest.raises(ValidationError):
        make_case(offers=[make_offer(currency="USD")])


def test_case_rejects_duplicate_offer_and_job_ids():
    with pytest.raises(ValidationError):
        make_case(offers=[make_offer(), make_offer()])
    job = make_case().jobs[0]
    with pytest.raises(ValidationError):
        make_case(jobs=[job, job])


def test_proposed_plan_needs_lines_and_infeasible_plan_cannot_have_them():
    line = RecoveryLine(offer_id="o", supplier_id="s", sku=SKU, quantity=1,
                        unit_price=Decimal("1.00"))
    with pytest.raises(ValidationError):
        RecoveryPlan(case_id="c", status=RecoveryPlanStatus.PROPOSED,
                     total_cost=Decimal("0.00"))
    with pytest.raises(ValidationError):
        RecoveryPlan(case_id="c", status=RecoveryPlanStatus.NO_FEASIBLE_PLAN,
                     lines=[line], total_cost=Decimal("1.00"))


def test_recovery_line_total_is_exact_decimal():
    line = RecoveryLine(offer_id="o", supplier_id="s", sku=SKU, quantity=3,
                        unit_price=Decimal("0.10"))
    assert line.line_total == Decimal("0.30")


def test_approval_must_expire_after_it_is_granted():
    with pytest.raises(ValidationError):
        ProcurementApproval(
            case_id="c", plan_id="p", approved_by="m", approved_at=NOW,
            expires_at=NOW, max_total_cost=Decimal("1.00"),
        )


def test_approval_expiry_check_requires_timezone():
    approval = ProcurementApproval(
        case_id="c", plan_id="p", approved_by="m", approved_at=NOW,
        expires_at=NOW + timedelta(hours=1), max_total_cost=Decimal("1.00"),
    )
    assert not approval.is_expired(NOW + timedelta(minutes=5))
    assert approval.is_expired(NOW + timedelta(hours=1))
    with pytest.raises(ValueError):
        approval.is_expired(datetime(2026, 1, 15, 9, 30))


def test_agent_message_accepts_small_json_payload():
    assert message({"count": 1, "ok": True, "items": ["a"]}).schema_version == "1.0"


@pytest.mark.parametrize(
    "payload",
    [
        {"x": float("nan")},
        {"x": float("inf")},
        {"x": "a\x00b"},
        {"x": "a" * 5000},
        {"x": 2 ** 60},
        {"x": object()},
        {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": 1}}}}}}}}},
        {"x": ["y"] * 101},
        {"big": ["a" * 1000] * 20},
    ],
)
def test_agent_message_rejects_unsafe_payloads(payload):
    with pytest.raises(ValidationError):
        message(payload)


def test_agent_message_rejects_bad_type_and_non_object_payload():
    with pytest.raises(ValidationError):
        AgentMessage(workflow_id="w", trace_id="t", sender="a", recipient="b",
                     message_type="Bad Type", payload={})
    with pytest.raises(ValidationError):
        message("not a dict")
