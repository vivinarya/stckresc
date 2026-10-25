from datetime import timedelta
from decimal import Decimal

import pytest

from helpers import NOW, SKU, make_assessment, make_case, make_job, make_offer
from shared.models import CompatibilityStatus, InventoryRecord, RecoveryPlanStatus
from tools.recovery_optimizer import (
    NoProcurementRequired,
    RecoveryOptimizationError,
    plan_recovery,
)

ALT = "BRG-6204-ALT"


def blockers(plan):
    return " | ".join(plan.blockers)


def test_feasible_case_produces_exact_plan():
    plan = plan_recovery(make_case(), as_of=NOW)
    assert plan.status == RecoveryPlanStatus.PROPOSED
    (line,) = plan.lines
    assert (line.quantity, line.unit_price) == (2, Decimal("100.00"))
    assert plan.total_cost == Decimal("250.00")
    assert plan.expected_completion_at == NOW + timedelta(days=1)


def test_planning_is_deterministic():
    first = plan_recovery(make_case(), as_of=NOW)
    second = plan_recovery(make_case(), as_of=NOW)
    assert first.lines == second.lines and first.total_cost == second.total_cost


def test_cheapest_complete_option_is_selected():
    offers = [
        make_offer(offer_id="OFFER-1"),
        make_offer(offer_id="OFFER-2", supplier_id="supplier-c",
                   unit_price=Decimal("80.00")),
    ]
    plan = plan_recovery(make_case(offers=offers), as_of=NOW)
    assert plan.lines[0].offer_id == "OFFER-2" and plan.total_cost == Decimal("210.00")


def test_unknown_freight_is_never_assumed_zero():
    plan = plan_recovery(make_case(offers=[make_offer(freight_cost=None)]), as_of=NOW)
    assert plan.status == RecoveryPlanStatus.NO_FEASIBLE_PLAN
    assert "unknown freight" in blockers(plan)
    assert plan.total_cost == Decimal("0.00") and plan.lines == []


def test_unknown_lead_time_blocks_the_plan():
    plan = plan_recovery(make_case(offers=[make_offer(lead_time_days=None)]), as_of=NOW)
    assert plan.status == RecoveryPlanStatus.NO_FEASIBLE_PLAN
    assert "delivery estimate" in blockers(plan)


def test_late_delivery_blocks_the_plan():
    plan = plan_recovery(make_case(offers=[make_offer(lead_time_days=10)]), as_of=NOW)
    assert plan.status == RecoveryPlanStatus.NO_FEASIBLE_PLAN
    assert "deadline" in blockers(plan)


def test_budget_is_enforced():
    plan = plan_recovery(make_case(budget=Decimal("200.00")), as_of=NOW)
    assert plan.status == RecoveryPlanStatus.NO_FEASIBLE_PLAN
    assert "budget" in blockers(plan)


def test_expired_quote_is_not_used():
    offer = make_offer(valid_until=NOW - timedelta(seconds=1))
    assert plan_recovery(make_case(offers=[offer]), as_of=NOW).status == \
        RecoveryPlanStatus.NO_FEASIBLE_PLAN


def test_failed_supplier_offers_are_excluded():
    plan = plan_recovery(make_case(offers=[make_offer(supplier_id="supplier-a")]), as_of=NOW)
    assert plan.status == RecoveryPlanStatus.NO_FEASIBLE_PLAN
    assert "alternative supplier" in blockers(plan)


def test_no_jobs_gives_no_plan():
    plan = plan_recovery(make_case(jobs=[]), as_of=NOW)
    assert plan.status == RecoveryPlanStatus.NO_FEASIBLE_PLAN
    assert "No affected jobs" in blockers(plan)


def test_sufficient_stock_needs_no_procurement():
    inventory = [InventoryRecord(sku=SKU, quantity_on_hand=10)]
    with pytest.raises(NoProcurementRequired):
        plan_recovery(make_case(inventory=inventory), as_of=NOW)


def test_multi_sku_cases_are_rejected():
    jobs = [make_job(), make_job(job_id="JOB-2", sku="OTHER-1")]
    with pytest.raises(RecoveryOptimizationError):
        plan_recovery(make_case(jobs=jobs), as_of=NOW)


def test_naive_comparison_time_is_rejected():
    from datetime import datetime
    with pytest.raises(RecoveryOptimizationError):
        plan_recovery(make_case(), as_of=datetime(2026, 1, 15))


@pytest.mark.parametrize(
    "assessments, expected",
    [
        ([], "compatibility is unknown"),
        ([make_assessment(ALT, CompatibilityStatus.INCOMPATIBLE)], "incompatible"),
        ([make_assessment(ALT, assessed_by="unverified")], "named assessor"),
        ([make_assessment(ALT, evidence=[])], "named assessor or evidence"),
    ],
)
def test_substitute_without_verified_compatibility_is_excluded(assessments, expected):
    case = make_case(
        offers=[make_offer(sku=ALT, unit_price=Decimal("50.00"))],
        compatibility=assessments,
    )
    plan = plan_recovery(case, as_of=NOW)
    assert plan.status == RecoveryPlanStatus.NO_FEASIBLE_PLAN
    assert expected in blockers(plan)


def test_verified_substitute_is_used_and_flagged_for_recheck():
    case = make_case(
        offers=[make_offer(sku=ALT, unit_price=Decimal("50.00"))],
        compatibility=[make_assessment(ALT)],
    )
    plan = plan_recovery(case, as_of=NOW)
    assert plan.status == RecoveryPlanStatus.PROPOSED
    assert plan.lines[0].sku == ALT
    assert any("rechecked" in text for text in plan.assumptions)
