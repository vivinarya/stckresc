from datetime import datetime, timedelta, timezone
from decimal import Decimal

from shared.models import (
    CompatibilityAssessment,
    CompatibilityStatus,
    DisruptionCase,
    InventoryRecord,
    JobRequirement,
    ProcurementApproval,
    SupplierOffer,
)

# fixed past instant so quote windows are deterministic
NOW = datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc)
SKU = "BRG-6204"


def make_offer(**overrides) -> SupplierOffer:
    values = dict(
        offer_id="OFFER-1",
        supplier_id="supplier-b",
        supplier_name="Supplier B",
        sku=SKU,
        quantity_available=20,
        unit_price=Decimal("100.00"),
        freight_cost=Decimal("50.00"),
        lead_time_days=1,
        quoted_at=NOW - timedelta(hours=1),
        valid_until=NOW + timedelta(days=1),
        currency="INR",
    )
    values.update(overrides)
    return SupplierOffer(**values)


def make_job(**overrides) -> JobRequirement:
    values = dict(
        job_id="JOB-1",
        sku=SKU,
        quantity_required=5,
        required_by=NOW + timedelta(days=3),
    )
    values.update(overrides)
    return JobRequirement(**values)


def make_case(**overrides) -> DisruptionCase:
    # demand 5, stock 3 -> shortage 2; one valid alternative offer totals 250.00
    values = dict(
        case_id="CASE-1",
        affected_sku=SKU,
        failed_supplier_id="supplier-a",
        reported_at=NOW - timedelta(hours=2),
        budget=Decimal("1000.00"),
        currency="INR",
        jobs=[make_job()],
        inventory=[InventoryRecord(sku=SKU, quantity_on_hand=3, quantity_committed=0)],
        offers=[make_offer()],
        compatibility=[],
    )
    values.update(overrides)
    return DisruptionCase(**values)


def make_assessment(candidate_sku: str, status=CompatibilityStatus.COMPATIBLE, **overrides):
    values = dict(
        requested_sku=SKU,
        candidate_sku=candidate_sku,
        status=status,
        evidence=["manufacturer cross-reference sheet"],
        assessed_by="engineer-1",
        assessed_at=NOW - timedelta(days=1),
    )
    values.update(overrides)
    return CompatibilityAssessment(**values)


def make_approval(plan, **overrides) -> ProcurementApproval:
    values = dict(
        case_id=plan.case_id,
        plan_id=plan.plan_id,
        plan_fingerprint=plan.compute_fingerprint(),
        approved_by="manager-1",
        approved_at=NOW + timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1),
        max_total_cost=plan.total_cost,
        currency=plan.currency,
    )
    values.update(overrides)
    return ProcurementApproval(**values)
