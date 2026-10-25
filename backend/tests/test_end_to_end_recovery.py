"""End-to-end recovery scenario validation tests (Part 10).

Demonstrates the full StockRescue recovery workflow through the actual coordinator:
1. Case retrieval from the repository (SQLite & Postgres compatible)
2. Inventory/BOM assessment (calculates on-hand, committed, net shortage)
3. Job impact assessment (sorts affected jobs, verifies uncovered demand)
4. Supplier discovery (filters failed suppliers and stale quotes)
5. Compatibility verification (evaluates substitute SKUs vs evidence)
6. Quote risk analysis (checks quote validity, terms, deadlines)
7. Recovery planning (deterministic optimizer generating a Proposed plan or legitimate no_feasible_plan)
8. Human-approval enforcement & procurement draft preparation (untrusted messages cannot authorize purchasing)
9. Vendor communication draft preparation (drafts generated for operator review without sending)
10. Delivery monitoring (evaluates verified events; preserves unknown status when none exist)
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from agents.coordinator.agent import run_coordinator
from agents.delivery_watch.agent import run_delivery_watch
from agents.procurement.agent import prepare_procurement_draft
from agents.vendor_voice.agent import prepare_vendor_communication
from helpers import NOW, make_approval, make_case, make_offer
from services.warehouse_api.repository import SQLiteWarehouseRepository
from shared.agent_contracts import (
    AgentStatus,
    CoordinatorOutcome,
    build_agent_context,
)
from shared.models import (
    DeliveryEvent,
    DisruptionCase,
    ProcurementApproval,
    RecoveryPlan,
    RecoveryPlanStatus,
)


def test_full_recovery_scenario_standard_brg6204(tmp_path):
    # 1. Setup isolated warehouse repository and persist disruption case
    repo = SQLiteWarehouseRepository(tmp_path / "recovery_test.db")
    repo.initialize()

    case = make_case(case_id="CASE-RECOVERY-BRG6204")
    repo.create_case(case)

    # Verify retrieval through configured repository
    retrieved_case = repo.get_case("CASE-RECOVERY-BRG6204")
    assert retrieved_case.case_id == "CASE-RECOVERY-BRG6204"
    assert retrieved_case.affected_sku == "BRG-6204"

    # 2-7. Execute coordinator workflow (Deterministic Local Transport Mode)
    coordinator_result = run_coordinator(retrieved_case, as_of=NOW)

    assert coordinator_result.outcome == CoordinatorOutcome.AWAITING_APPROVAL
    assert coordinator_result.inventory.status == AgentStatus.COMPLETE
    assert coordinator_result.inventory.affected_sku == "BRG-6204"
    assert coordinator_result.inventory.demand_quantity == 5
    assert coordinator_result.inventory.available_quantity == 3
    assert coordinator_result.inventory.shortage_quantity == 2

    assert coordinator_result.job_impact.status in {AgentStatus.PARTIAL, AgentStatus.COMPLETE}
    assert coordinator_result.job_impact.uncovered_demand == 2
    assert len(coordinator_result.job_impact.jobs) == 1

    assert coordinator_result.supplier_discovery.status == AgentStatus.COMPLETE
    assert len(coordinator_result.supplier_discovery.offers) >= 1

    assert coordinator_result.compatibility.status == AgentStatus.COMPLETE

    assert coordinator_result.recovery_plan is not None
    plan = coordinator_result.recovery_plan
    assert plan.status == RecoveryPlanStatus.PROPOSED
    assert len(plan.lines) == 1
    assert plan.lines[0].quantity == 2
    assert plan.lines[0].unit_price == Decimal("100.00")
    assert plan.total_cost == Decimal("250.00")

    # Persist the generated plan
    repo.save_plan(plan)
    stored_plan = repo.get_plan(plan.plan_id)
    assert stored_plan.plan_id == plan.plan_id

    # 8. Human-approval enforcement and procurement draft preparation
    # Test A: Demonstrates that untrusted approval_verified claim is blocked without server-side verification
    untrusted_approval = make_approval(plan)
    blocked_draft = prepare_procurement_draft(
        retrieved_case,
        plan,
        untrusted_approval,
        current_plan=stored_plan,
        approval_verified=False,  # Untrusted client assertion rejected!
        as_of=NOW,
    )
    assert blocked_draft.status == "blocked"
    assert "approval_source_not_verified" in blocked_draft.blockers

    # Test B: Server-side verified approval authorizes draft preparation
    trusted_approval = make_approval(plan, approved_at=NOW)
    repo.save_approval(trusted_approval)
    active_approval = repo.get_active_approval(plan.plan_id, as_of=NOW)
    assert active_approval is not None

    valid_draft = prepare_procurement_draft(
        retrieved_case,
        plan,
        active_approval,
        current_plan=stored_plan,
        approval_verified=True,  # Authoritative server-side verified check
        as_of=NOW,
    )
    assert valid_draft.status == "draft_prepared"
    assert valid_draft.draft is not None
    assert valid_draft.draft.lines[0].quantity == 2
    assert valid_draft.draft.total_cost == Decimal("250.00")
    assert valid_draft.external_submission_enabled is False  # Safety: draft only!

    # 9. Vendor communication draft preparation
    comm_result = prepare_vendor_communication(retrieved_case, plan=plan, as_of=NOW)
    assert comm_result.status == "ready"
    assert len(comm_result.drafts) == 1
    draft = comm_result.drafts[0]
    assert draft.sku == "BRG-6204"
    assert draft.quantity == 2
    assert draft.communication_type == "order_notification"
    assert draft.requires_human_approval is True
    assert comm_result.procurement_authorized is False

    # 10. Delivery monitoring
    # Test A: When no delivery events exist, status must report unknown, not fabricated
    events, _ = repo.list_delivery_events(plan.plan_id)
    assert len(events) == 0
    delivery_status = run_delivery_watch(plan, events, as_of=NOW)
    assert delivery_status.status == "unknown"
    assert delivery_status.accepted_event_count == 0

    # Test B: With recorded verified delivery event
    del_event = DeliveryEvent(
        event_id="DEL-REC-01",
        plan_id=plan.plan_id,
        status="in_transit",
        occurred_at=NOW,
        source="carrier_api",
        reference="TRK-98765",
        details="In transit from hub",
    )
    repo.record_delivery_event(del_event)
    events, count = repo.list_delivery_events(plan.plan_id)
    assert count == 1

    delivery_status_active = run_delivery_watch(plan, events, as_of=NOW)
    assert delivery_status_active.status == "in_progress"
    assert delivery_status_active.latest_event_id == "DEL-REC-01"

    repo.close()


def test_full_recovery_scenario_no_feasible_plan(tmp_path):
    repo = SQLiteWarehouseRepository(tmp_path / "no_feasible_plan.db")
    repo.initialize()

    # Disruption case with zero offers within budget or deadline
    case = make_case(
        case_id="CASE-NO-FEASIBLE",
        budget=Decimal("50.00"),  # Demand is 2 @ 100 + 50 freight = 250, exceeding 50
    )
    repo.create_case(case)

    coordinator_result = run_coordinator(case, as_of=NOW)
    assert coordinator_result.outcome == CoordinatorOutcome.NO_FEASIBLE_PLAN
    assert coordinator_result.recovery_plan is not None
    assert coordinator_result.recovery_plan.status == RecoveryPlanStatus.NO_FEASIBLE_PLAN
    assert "budget" in " | ".join(coordinator_result.recovery_plan.blockers)
    repo.close()
