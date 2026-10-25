from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.config import get_settings
from shared.models import DisruptionCase, InventoryRecord, JobRequirement, SupplierOffer
from services.warehouse_api.repository import RepositoryConflict, SQLiteWarehouseRepository


def build_demo_case() -> DisruptionCase:
    now = datetime.now(timezone.utc)
    local_now = datetime.now(ZoneInfo("Asia/Kolkata"))
    tomorrow_morning = (local_now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    four_days_out = (local_now + timedelta(days=4)).replace(hour=17, minute=0, second=0, microsecond=0)

    return DisruptionCase(
        case_id="DEMO-STOCK-RESCUE-001",
        affected_sku="BRG-6204",
        failed_supplier_id="primary-supplier",
        reported_at=now,
        delayed_until=now + timedelta(days=5),
        budget=Decimal("3000.00"),
        currency="INR",
        inventory=[
            InventoryRecord(
                sku="BRG-6204",
                quantity_on_hand=8,
                quantity_committed=5,
                unit_cost=Decimal("300.00"),
                location="main warehouse",
            )
        ],
        jobs=[
            JobRequirement(
                job_id="JOB-A",
                sku="BRG-6204",
                quantity_required=5,
                required_by=tomorrow_morning,
                priority=5,
                description="Critical maintenance job",
            ),
            JobRequirement(
                job_id="JOB-B",
                sku="BRG-6204",
                quantity_required=3,
                required_by=four_days_out,
                priority=3,
                description="Scheduled repair job",
            ),
        ],
        offers=[
            SupplierOffer(
                offer_id="OFFER-PRIMARY-DELAYED",
                supplier_id="primary-supplier",
                supplier_name="Primary Supplier (delayed)",
                sku="BRG-6204",
                quantity_available=20,
                unit_price=Decimal("300.00"),
                freight_cost=Decimal("0.00"),
                minimum_order_quantity=1,
                pack_size=1,
                lead_time_days=5,
                quoted_at=now,
                valid_until=now + timedelta(days=5, hours=12),
            ),
            SupplierOffer(
                offer_id="OFFER-LOCAL-URGENT",
                supplier_id="local-supplier",
                supplier_name="Local Supplier",
                sku="BRG-6204",
                quantity_available=5,
                unit_price=Decimal("550.00"),
                freight_cost=None,
                minimum_order_quantity=1,
                pack_size=1,
                lead_time_days=0,
                quoted_at=now,
                valid_until=now + timedelta(hours=12),
            ),
            SupplierOffer(
                offer_id="OFFER-REGIONAL-BULK",
                supplier_id="regional-supplier",
                supplier_name="Regional Supplier",
                sku="BRG-6204",
                quantity_available=10,
                unit_price=Decimal("380.00"),
                freight_cost=Decimal("300.00"),
                minimum_order_quantity=1,
                pack_size=1,
                lead_time_days=2,
                quoted_at=now,
                valid_until=now + timedelta(days=1),
            ),
        ],
    )


def main() -> int:
    settings = get_settings()
    if settings.stockrescue_env == "production":
        print("Error: demo data seeding is strictly disabled in production.", file=sys.stderr)
        return 1
    if not settings.demo_mode:
        print("Error: demo data seeding is disabled when DEMO_MODE is false.", file=sys.stderr)
        return 1

    if settings.database_backend != "sqlite":
        print("Error: demo seeding only targets the local SQLite development database.", file=sys.stderr)
        return 1

    repository = SQLiteWarehouseRepository(
        settings.database_path,
        timeout=settings.database_timeout_seconds,
    )
    repository.initialize()
    case = build_demo_case()
    try:
        try:
            repository.create_case(case)
        except RepositoryConflict:
            existing = repository.get_case(case.case_id)
            print(f"Demo case already exists: {existing.case_id}")
            return 0
        print(f"Seeded synthetic demo case: {case.case_id}")
        print("Demo data includes an unknown local freight charge intentionally; the planner must not assume it is zero.")
        return 0
    finally:
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
