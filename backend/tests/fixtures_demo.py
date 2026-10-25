"""Canonical demo fixtures for StockRescue test suite (Phase 3).

Provides:
- make_fresh_demo_case: Fresh relative expiry/deadline data.
- make_expired_quote_case: Separate expired-quote fixture testing quote staleness and no-feasible-plan paths.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from shared.models import (
    CompatibilityAssessment,
    CompatibilityStatus,
    DisruptionCase,
    InventoryRecord,
    JobRequirement,
    SupplierOffer,
)


def make_fresh_demo_case(case_id: str = "DEMO-STOCK-RESCUE-001") -> DisruptionCase:
    now = datetime.now(timezone.utc)
    return DisruptionCase(
        case_id=case_id,
        affected_sku="BRG-6204",
        failed_supplier_id="primary-supplier",
        reported_at=now - timedelta(hours=1),
        delayed_until=now + timedelta(days=5),
        budget=Decimal("3000.00"),
        currency="USD",
        inventory=[
            InventoryRecord(
                sku="BRG-6204",
                quantity_on_hand=8,
                quantity_committed=5,
                unit_cost=Decimal("35.00"),
                location="Aisle 4, Bin B-12",
            )
        ],
        jobs=[
            JobRequirement(
                job_id="JOB-A",
                sku="BRG-6204",
                quantity_required=5,
                required_by=now + timedelta(days=1),
                priority=5,
                description="Cooling water pump rebuild (Production Line 4)",
            ),
            JobRequirement(
                job_id="JOB-B",
                sku="BRG-6204",
                quantity_required=3,
                required_by=now + timedelta(days=3),
                priority=3,
                description="Scheduled preventive overhaul (Conveyor Line 2)",
            ),
        ],
        offers=[
            SupplierOffer(
                offer_id="OFFER-MOTION-01",
                supplier_id="SUPP-MOTION-IND",
                supplier_name="Motion Industries",
                sku="BRG-6204",
                quantity_available=18,
                unit_price=Decimal("42.50"),
                freight_cost=Decimal("180.00"),
                minimum_order_quantity=1,
                pack_size=1,
                lead_time_days=0,
                quoted_at=now - timedelta(minutes=30),
                valid_until=now + timedelta(days=2),
                currency="USD",
            ),
            SupplierOffer(
                offer_id="OFFER-APPLIED-02",
                supplier_id="SUPP-APPLIED-TECH",
                supplier_name="Applied Industrial Technologies",
                sku="BRG-6204",
                quantity_available=25,
                unit_price=Decimal("38.00"),
                freight_cost=Decimal("120.00"),
                minimum_order_quantity=1,
                pack_size=1,
                lead_time_days=1,
                quoted_at=now - timedelta(hours=1),
                valid_until=now + timedelta(days=3),
                currency="USD",
            ),
        ],
        compatibility=[
            CompatibilityAssessment(
                requested_sku="BRG-6204",
                candidate_sku="BRG-6204",
                status=CompatibilityStatus.COMPATIBLE,
                evidence=["Exact OEM match"],
                assessed_by="catalog_verification",
                assessed_at=now - timedelta(hours=2),
            )
        ],
    )


def make_expired_quote_case(case_id: str = "DEMO-EXPIRED-001") -> DisruptionCase:
    now = datetime.now(timezone.utc)
    return DisruptionCase(
        case_id=case_id,
        affected_sku="BRG-6204",
        failed_supplier_id="primary-supplier",
        reported_at=now - timedelta(hours=48),
        delayed_until=now + timedelta(days=5),
        budget=Decimal("1000.00"),
        currency="USD",
        inventory=[
            InventoryRecord(
                sku="BRG-6204",
                quantity_on_hand=8,
                quantity_committed=5,
                unit_cost=Decimal("35.00"),
                location="Aisle 4, Bin B-12",
            )
        ],
        jobs=[
            JobRequirement(
                job_id="JOB-A",
                sku="BRG-6204",
                quantity_required=5,
                required_by=now + timedelta(days=1),
                priority=5,
                description="Cooling water pump rebuild",
            )
        ],
        offers=[
            SupplierOffer(
                offer_id="OFFER-EXPIRED-STALE",
                supplier_id="SUPP-STALE-IND",
                supplier_name="Stale Spares Ltd",
                sku="BRG-6204",
                quantity_available=10,
                unit_price=Decimal("40.00"),
                freight_cost=Decimal("50.00"),
                minimum_order_quantity=1,
                pack_size=1,
                lead_time_days=1,
                quoted_at=now - timedelta(days=5),
                valid_until=now - timedelta(hours=2),  # Expired 2 hours ago!
                currency="USD",
            )
        ],
        compatibility=[],
    )
