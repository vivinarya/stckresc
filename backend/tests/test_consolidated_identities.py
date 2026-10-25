"""Unit tests for Milestone 5 consolidated 8-identity Agentverse architecture.

Validates:
1. Supplier Discovery identity executes both supplier_discovery and compatibility tasks.
2. Quote Risk identity executes both quote_risk and recovery_planner tasks.
3. Incorrect/forbidden task types are rejected for each consolidated identity.
4. Correct correlation, schema validation, and reciprocal linkage across shared addresses.
5. Fail-closed behavior: unavailable or unconfigured remote agents fail explicitly.
6. Procurement gates still enforce approval verification, fingerprinting, and expiry.
7. Local workflow compatibility and determinism remain preserved.
8. Retirement notices for standalone Compatibility and Recovery Planner entry points.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
import pytest

from agents.compatibility.agent import run_compatibility
from agents.coordinator.langgraph_workflow import run_coordinator_graph
from agents.inventory_bom.agent import run_inventory_bom
from agents.procurement.agent import prepare_procurement_draft
from agents.protocols.dispatcher import SpecialistDispatcher, _clean_plan_payload
from agents.protocols.messages import (
    CorrelationMismatchError,
    TaskErrorCode,
    TaskRequest,
    TaskResponse,
    TaskResponseStatus,
    verify_response_matches_request,
)
from agents.protocols.transport import SpecialistExecutionError, SpecialistTransport
from agents.quote_risk.agent import QuoteRiskResult, run_quote_risk
from agents.recovery_planner.agent import RecoveryPlannerResult, run_recovery_planner
from agents.supplier_discovery.agent import run_supplier_discovery
from helpers import NOW, make_approval, make_case, make_offer
from shared.agent_contracts import (
    AgentContext,
    CompatibilityResult,
    CoordinatorOutcome,
    InventoryBomResult,
    SupplierDiscoveryResult,
    build_agent_context,
)
from shared.config import Settings, get_settings
from shared.models import (
    CompatibilityAssessment,
    CompatibilityStatus,
    ProcurementApproval,
    RecoveryPlan,
    RecoveryPlanStatus,
)


TRUSTED_COORD_ADDR = "agent1qd9dvpagmp3n0lvfmvs4ys932k3wfxcklrkj4c6z6d6alhlpnuq27lns6rv"
SUPPLIER_SOURCING_ADDR = "agent1qtzpjpmwgwmd9s6upsz4rceqz0ljdjl72wxta86ensufgpy5y6zjxpvm6l4"
QUOTE_PLANNING_ADDR = "agent1q0u6t022pc9jv6v3xxr2m64fkhvzqpkj54lg8224e2ct8sfcf3gxumvy0mv"


# ---------------------------------------------------------------------------
# 1. Supplier Discovery Identity Consolidates Discovery + Compatibility
# ---------------------------------------------------------------------------

def test_supplier_discovery_identity_executes_both_tasks():
    dispatcher = SpecialistDispatcher(
        role="supplier_discovery",
        agent_name="stockrescue-supplier-discovery",
        allowed_senders={TRUSTED_COORD_ADDR},
    )

    case = make_case()
    context = build_agent_context(case, as_of=NOW)
    inv_result = run_inventory_bom(case, context)

    # A: supplier_discovery task
    req_discovery = TaskRequest.create(
        task_type="supplier_discovery",
        workflow_id=context.workflow_id,
        trace_id=context.trace_id,
        sender=TRUSTED_COORD_ADDR,
        recipient=SUPPLIER_SOURCING_ADDR,
        payload={
            "case": case.model_dump(mode="json"),
            "inventory": inv_result.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
        },
    )
    resp_discovery = dispatcher.dispatch(TRUSTED_COORD_ADDR, req_discovery)
    assert resp_discovery.status == TaskResponseStatus.OK
    assert resp_discovery.task_type == "supplier_discovery"
    discovery_result = SupplierDiscoveryResult.model_validate(resp_discovery.get_result())
    assert discovery_result.affected_sku == case.affected_sku
    assert len(discovery_result.offers) >= 1

    # B: compatibility task
    req_compat = TaskRequest.create(
        task_type="compatibility",
        workflow_id=context.workflow_id,
        trace_id=context.trace_id,
        sender=TRUSTED_COORD_ADDR,
        recipient=SUPPLIER_SOURCING_ADDR,
        payload={
            "case": case.model_dump(mode="json"),
            "supplier_discovery": discovery_result.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
        },
    )
    resp_compat = dispatcher.dispatch(TRUSTED_COORD_ADDR, req_compat)
    assert resp_compat.status == TaskResponseStatus.OK
    assert resp_compat.task_type == "compatibility"
    compat_result = CompatibilityResult.model_validate(resp_compat.get_result())
    assert compat_result.affected_sku == case.affected_sku
    assert len(compat_result.records) >= 1


def test_supplier_discovery_identity_rejects_forbidden_tasks():
    dispatcher = SpecialistDispatcher(
        role="supplier_discovery",
        agent_name="stockrescue-supplier-discovery",
        allowed_senders={TRUSTED_COORD_ADDR},
    )

    case = make_case()
    context = build_agent_context(case)

    forbidden_tasks = [
        "procurement_draft",
        "quote_risk",
        "recovery_planner",
        "inventory_bom",
        "job_impact",
        "vendor_communication_prep",
        "delivery_watch",
    ]

    for task in forbidden_tasks:
        req = TaskRequest.create(
            task_type=task,
            workflow_id=context.workflow_id,
            trace_id=context.trace_id,
            sender=TRUSTED_COORD_ADDR,
            recipient=SUPPLIER_SOURCING_ADDR,
            payload={"case": case.model_dump(mode="json")},
        )
        resp = dispatcher.dispatch(TRUSTED_COORD_ADDR, req)
        assert resp.status == TaskResponseStatus.REJECTED
        assert resp.error_code == TaskErrorCode.TASK_TYPE_FORBIDDEN


# ---------------------------------------------------------------------------
# 2. Quote Risk Identity Consolidates Quote Risk + Recovery Planner
# ---------------------------------------------------------------------------

def test_quote_risk_identity_executes_both_tasks():
    dispatcher = SpecialistDispatcher(
        role="quote_risk",
        agent_name="stockrescue-quote-risk",
        allowed_senders={TRUSTED_COORD_ADDR},
    )

    case = make_case()
    context = build_agent_context(case)

    # A: quote_risk task
    req_risk = TaskRequest.create(
        task_type="quote_risk",
        workflow_id=context.workflow_id,
        trace_id=context.trace_id,
        sender=TRUSTED_COORD_ADDR,
        recipient=QUOTE_PLANNING_ADDR,
        payload={
            "case": case.model_dump(mode="json"),
            "required_quantity": 2,
            "as_of": NOW.isoformat(),
        },
    )
    resp_risk = dispatcher.dispatch(TRUSTED_COORD_ADDR, req_risk)
    assert resp_risk.status == TaskResponseStatus.OK
    assert resp_risk.task_type == "quote_risk"
    risk_result = QuoteRiskResult.model_validate(resp_risk.get_result())
    assert risk_result.case_id == case.case_id

    # B: recovery_planner task
    req_plan = TaskRequest.create(
        task_type="recovery_planner",
        workflow_id=context.workflow_id,
        trace_id=context.trace_id,
        sender=TRUSTED_COORD_ADDR,
        recipient=QUOTE_PLANNING_ADDR,
        payload={
            "case": case.model_dump(mode="json"),
            "as_of": NOW.isoformat(),
        },
    )
    resp_plan = dispatcher.dispatch(TRUSTED_COORD_ADDR, req_plan)
    assert resp_plan.status == TaskResponseStatus.OK
    assert resp_plan.task_type == "recovery_planner"
    raw_result = resp_plan.get_result()
    assert raw_result["case_id"] == case.case_id
    assert raw_result["outcome"] in ("awaiting_approval", "no_procurement_required")
    clean_plan = _clean_plan_payload(raw_result["recovery_plan"])
    assert clean_plan.plan_id is not None


def test_quote_risk_identity_rejects_forbidden_tasks():
    dispatcher = SpecialistDispatcher(
        role="quote_risk",
        agent_name="stockrescue-quote-risk",
        allowed_senders={TRUSTED_COORD_ADDR},
    )

    case = make_case()
    context = build_agent_context(case)

    forbidden_tasks = [
        "supplier_discovery",
        "compatibility",
        "procurement_draft",
        "inventory_bom",
        "job_impact",
        "vendor_communication_prep",
        "delivery_watch",
    ]

    for task in forbidden_tasks:
        req = TaskRequest.create(
            task_type=task,
            workflow_id=context.workflow_id,
            trace_id=context.trace_id,
            sender=TRUSTED_COORD_ADDR,
            recipient=QUOTE_PLANNING_ADDR,
            payload={"case": case.model_dump(mode="json")},
        )
        resp = dispatcher.dispatch(TRUSTED_COORD_ADDR, req)
        assert resp.status == TaskResponseStatus.REJECTED
        assert resp.error_code == TaskErrorCode.TASK_TYPE_FORBIDDEN


# ---------------------------------------------------------------------------
# 3. Response Correlation and Reciprocal Verification Across Shared Addresses
# ---------------------------------------------------------------------------

def test_shared_address_response_correlation_integrity():
    """Confirms that distinct logical tasks sent to the same shared address
    are correlated and verified without cross-talk or identity collision."""
    case = make_case()
    context = build_agent_context(case)

    req1 = TaskRequest.create(
        task_type="supplier_discovery",
        workflow_id=context.workflow_id,
        trace_id=context.trace_id,
        sender=TRUSTED_COORD_ADDR,
        recipient=SUPPLIER_SOURCING_ADDR,
        payload={"case": case.model_dump(mode="json")},
    )
    req2 = TaskRequest.create(
        task_type="compatibility",
        workflow_id=context.workflow_id,
        trace_id=context.trace_id,
        sender=TRUSTED_COORD_ADDR,
        recipient=SUPPLIER_SOURCING_ADDR,
        payload={"case": case.model_dump(mode="json")},
    )

    resp1 = TaskResponse.ok(req1, sender=SUPPLIER_SOURCING_ADDR, result={"ok": 1})
    resp2 = TaskResponse.ok(req2, sender=SUPPLIER_SOURCING_ADDR, result={"ok": 2})

    # Legitimate reciprocal match
    verify_response_matches_request(req1, resp1)
    verify_response_matches_request(req2, resp2)

    # Cross-talk rejection: resp2 cannot satisfy req1 even though recipient/sender address matches
    with pytest.raises(CorrelationMismatchError):
        verify_response_matches_request(req1, resp2)


# ---------------------------------------------------------------------------
# 4. Fail-Closed Remote Execution (No Silent Fallback)
# ---------------------------------------------------------------------------

def test_transport_remote_mode_fails_closed_without_caller():
    transport = SpecialistTransport(
        mode="agentverse",
        sender_identity=TRUSTED_COORD_ADDR,
        recipient_addresses={"supplier_discovery": SUPPLIER_SOURCING_ADDR},
        remote_caller=None,  # No caller configured
    )

    case = make_case()
    with pytest.raises(SpecialistExecutionError) as exc_info:
        transport.invoke(
            task_type="supplier_discovery",
            payload={"case": case.model_dump(mode="json")},
            workflow_id="wf-1",
            trace_id="tr-1",
            role="supplier_discovery",
        )
    assert exc_info.value.status == TaskResponseStatus.UNAVAILABLE
    assert exc_info.value.error_code == TaskErrorCode.AGENT_UNAVAILABLE


def test_transport_remote_mode_fails_closed_with_missing_recipient():
    mock_caller = lambda dest, req: TaskResponse.ok(req, sender=dest, result={})
    transport = SpecialistTransport(
        mode="agentverse",
        sender_identity=TRUSTED_COORD_ADDR,
        recipient_addresses={},  # missing recipient for role
        remote_caller=mock_caller,
    )

    case = make_case()
    with pytest.raises(SpecialistExecutionError) as exc_info:
        transport.invoke(
            task_type="supplier_discovery",
            payload={"case": case.model_dump(mode="json")},
            workflow_id="wf-1",
            trace_id="tr-1",
            role="supplier_discovery",
        )
    assert exc_info.value.status == TaskResponseStatus.INVALID_REQUEST
    assert exc_info.value.error_code == TaskErrorCode.INVALID_PAYLOAD


# ---------------------------------------------------------------------------
# 5. Settings Address Resolution for 8-Identity Configuration
# ---------------------------------------------------------------------------

def test_settings_get_specialist_address_resolves_consolidated_mailboxes():
    settings = Settings(
        agentverse_supplier_discovery_address=SUPPLIER_SOURCING_ADDR,
        agentverse_compatibility_address="",  # unpopulated
        agentverse_quote_risk_address=QUOTE_PLANNING_ADDR,
        agentverse_recovery_planner_address="",  # unpopulated
    )

    # Compatibility falls back to shared Supplier Discovery address
    assert settings.get_specialist_address("supplier_discovery") == SUPPLIER_SOURCING_ADDR
    assert settings.get_specialist_address("compatibility") == SUPPLIER_SOURCING_ADDR

    # Recovery Planner falls back to shared Quote Risk address
    assert settings.get_specialist_address("quote_risk") == QUOTE_PLANNING_ADDR
    assert settings.get_specialist_address("recovery_planner") == QUOTE_PLANNING_ADDR


# ---------------------------------------------------------------------------
# 6. Procurement Gate Security Invariants
# ---------------------------------------------------------------------------

def test_procurement_gate_rejects_untrusted_approval_verified_flag():
    """Client-supplied approval_verified=True is not trusted if plan or approval is invalid."""
    case = make_case()
    plan = run_recovery_planner(case, as_of=NOW).recovery_plan
    assert plan is not None

    approval = make_approval(plan)

    # 1. approval_verified=False blocks immediately
    res_unverified = prepare_procurement_draft(
        case,
        plan,
        approval,
        current_plan=plan,
        approval_verified=False,
        as_of=NOW,
    )
    assert res_unverified.status == "blocked"
    assert "approval_source_not_verified" in res_unverified.blockers

    # 2. approval_verified=True with expired approval is STILL BLOCKED
    res_expired = prepare_procurement_draft(
        case,
        plan,
        approval,
        current_plan=plan,
        approval_verified=True,
        as_of=NOW + timedelta(days=10),
    )
    assert res_expired.status == "blocked"
    assert "approval_expired" in res_expired.blockers

    # 3. approval_verified=True with tampered fingerprint is STILL BLOCKED
    tampered_approval = approval.model_copy(update={"plan_fingerprint": "0" * 64})
    res_tampered = prepare_procurement_draft(
        case,
        plan,
        tampered_approval,
        current_plan=plan,
        approval_verified=True,
        as_of=NOW,
    )
    assert res_tampered.status == "blocked"
    assert "plan_fingerprint_mismatch" in res_tampered.blockers

    # 4. External submission is always disabled (draft-only)
    assert res_expired.external_submission_enabled is False


# ---------------------------------------------------------------------------
# 7. Local Workflow Determinism Preserved
# ---------------------------------------------------------------------------

def test_local_coordinator_workflow_end_to_end():
    case = make_case()
    result = run_coordinator_graph(case, as_of=NOW)
    assert result.outcome == CoordinatorOutcome.AWAITING_APPROVAL
    assert result.procurement_authorized is False
    assert result.recovery_plan is not None
    assert result.recovery_plan.status == RecoveryPlanStatus.PROPOSED
    assert result.inventory.shortage_quantity == 2
    assert len(result.supplier_discovery.offers) >= 1
    assert len(result.compatibility.safe_offer_ids) >= 1
    assert result.compatibility.records[0].safe_for_planning is True


# ---------------------------------------------------------------------------
# 8. Standalone Entry Point Retirement Notices
# ---------------------------------------------------------------------------

def test_compatibility_standalone_main_exits_with_retirement_notice():
    from agents.compatibility.agentverse_entry import main as compat_main
    with pytest.raises(SystemExit) as exc_info:
        compat_main()
    assert "consolidated into the Supplier Discovery mailbox" in str(exc_info.value)


def test_recovery_planner_standalone_main_exits_with_retirement_notice():
    from agents.recovery_planner.agentverse_entry import main as plan_main
    with pytest.raises(SystemExit) as exc_info:
        plan_main()
    assert "consolidated into the Quote Risk mailbox" in str(exc_info.value)
