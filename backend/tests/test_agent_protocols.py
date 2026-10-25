"""Comprehensive unit tests for Milestone 5: Agentverse communication,

typed contracts, specialist dispatchers, security validation, and coordinator integration.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic.v1 import ValidationError

from agents.compatibility.agentverse_entry import create_agent as create_compatibility_agent
from agents.coordinator.agent import run_coordinator
from agents.coordinator.langgraph_workflow import run_coordinator_graph
from agents.delivery_watch.agentverse_entry import create_agent as create_delivery_watch_agent
from agents.inventory_bom.agentverse_entry import create_agent as create_inventory_agent
from agents.job_impact.agentverse_entry import create_agent as create_job_impact_agent
from agents.procurement.agentverse_entry import create_agent as create_procurement_agent
from agents.protocols.dispatcher import SpecialistDispatcher, _clean_plan_payload
from agents.protocols.messages import (
    CorrelationMismatchError,
    MessageContractError,
    TaskErrorCode,
    TaskRequest,
    TaskResponse,
    TaskResponseStatus,
    serialize_task_payload,
    verify_response_matches_request,
)
from agents.protocols.transport import SpecialistExecutionError, SpecialistTransport
from agents.quote_risk.agentverse_entry import create_agent as create_quote_risk_agent
from agents.recovery_planner.agentverse_entry import create_agent as create_recovery_planner_agent
from agents.supplier_discovery.agentverse_entry import create_agent as create_supplier_discovery_agent
from agents.vendor_voice.agent import prepare_vendor_communication
from agents.vendor_voice.agentverse_entry import create_agent as create_vendor_voice_agent
from helpers import NOW, make_approval, make_case, make_job, make_offer
from shared.agent_contracts import AgentContext, CoordinatorOutcome
from agents.delivery_watch.agent import DeliveryEvent
from shared.models import RecoveryPlan, RecoveryPlanStatus
from tools.recovery_optimizer import plan_recovery

VALID_SEED = "agentverse-test-seed-with-at-least-16-characters-12345"


# ---------------------------------------------------------------------------
# Part A: Shared Message Contract & Validation Tests
# ---------------------------------------------------------------------------

def test_task_request_valid_creation():
    req = TaskRequest.create(
        task_type="inventory_bom",
        workflow_id="wf-1",
        trace_id="tr-1",
        sender="stockrescue-coordinator",
        recipient="stockrescue-inventory-bom",
        payload={"case_id": "CASE-100"},
        timeout_seconds=30.0,
    )
    assert req.task_type == "inventory_bom"
    assert req.workflow_id == "wf-1"
    assert req.recipient == "stockrescue-inventory-bom"
    assert req.get_payload() == {"case_id": "CASE-100"}
    assert not req.is_expired()


def test_task_request_schema_rejections():
    # Naive timestamp
    with pytest.raises((ValidationError, MessageContractError)):
        TaskRequest.create(
            task_type="inventory_bom",
            workflow_id="wf-1",
            trace_id="tr-1",
            sender="stockrescue-coordinator",
            recipient="stockrescue-inventory-bom",
            payload={"a": 1},
            created_at=datetime(2026, 1, 1),
        )

    # Unsupported task type
    with pytest.raises(ValidationError):
        TaskRequest.create(
            task_type="unsupported_task",
            workflow_id="wf-1",
            trace_id="tr-1",
            sender="stockrescue-coordinator",
            recipient="stockrescue-inventory-bom",
            payload={"a": 1},
        )

    # Invalid identifier pattern (special symbols)
    with pytest.raises(ValidationError):
        TaskRequest.create(
            task_type="inventory_bom",
            workflow_id="bad/workflow/id!",
            trace_id="tr-1",
            sender="stockrescue-coordinator",
            recipient="stockrescue-inventory-bom",
            payload={"a": 1},
        )


def test_task_request_payload_size_limits():
    giant_payload = {"pad": "x" * 70_000}
    with pytest.raises(MessageContractError, match="exceeds 65536 bytes"):
        serialize_task_payload(giant_payload)


def test_task_request_deadline_bounds():
    now = datetime.now(timezone.utc)
    # Deadline in the past / before created_at
    with pytest.raises(ValidationError):
        TaskRequest(
            message_id="m1",
            correlation_id="c1",
            workflow_id="w1",
            trace_id="t1",
            sender="coord",
            recipient="inv",
            task_type="inventory_bom",
            created_at=now,
            deadline_at=now - timedelta(seconds=1),
            payload_json="{}",
        )

    # Deadline exceeding MAX_DEADLINE_SECONDS (600s)
    with pytest.raises(ValidationError):
        TaskRequest(
            message_id="m1",
            correlation_id="c1",
            workflow_id="w1",
            trace_id="t1",
            sender="coord",
            recipient="inv",
            task_type="inventory_bom",
            created_at=now,
            deadline_at=now + timedelta(seconds=601),
            payload_json="{}",
        )


def test_task_response_status_consistency():
    req = TaskRequest.create(
        task_type="inventory_bom",
        workflow_id="wf-1",
        trace_id="tr-1",
        sender="stockrescue-coordinator",
        recipient="stockrescue-inventory-bom",
        payload={"a": 1},
    )

    # OK response requires result_json
    resp_ok = TaskResponse.ok(req, sender="stockrescue-inventory-bom", result={"status": "complete"})
    assert resp_ok.status == TaskResponseStatus.OK
    assert resp_ok.get_result() == {"status": "complete"}
    assert resp_ok.error_code is None

    # Error response requires error_code
    resp_err = TaskResponse.failure(
        req,
        sender="stockrescue-inventory-bom",
        status=TaskResponseStatus.REJECTED,
        error_code=TaskErrorCode.TASK_TYPE_FORBIDDEN,
        error_detail="Task forbidden",
    )
    assert resp_err.status == TaskResponseStatus.REJECTED
    assert resp_err.error_code == "task_type_forbidden"
    assert resp_err.result_json is None

    # Inconsistent response (OK with error_code) is rejected by schema validator
    with pytest.raises(ValidationError):
        TaskResponse(
            message_id="m1",
            correlation_id=req.correlation_id,
            workflow_id=req.workflow_id,
            trace_id=req.trace_id,
            sender="inv",
            recipient=req.sender,
            task_type="inventory_bom",
            status=TaskResponseStatus.OK,
            result_json="{}",
            error_code="oops",
            created_at=datetime.now(timezone.utc),
        )


def test_response_correlation_verification():
    req = TaskRequest.create(
        task_type="inventory_bom",
        workflow_id="wf-1",
        trace_id="tr-1",
        sender="stockrescue-coordinator",
        recipient="stockrescue-inventory-bom",
        payload={"a": 1},
    )
    resp = TaskResponse.ok(req, sender="stockrescue-inventory-bom", result={"ok": True})
    verify_response_matches_request(req, resp)

    # Correlation ID mismatch
    bad_resp = TaskResponse.ok(req, sender="stockrescue-inventory-bom", result={"ok": True})
    bad_resp.correlation_id = "diff-corr-id"
    with pytest.raises(CorrelationMismatchError):
        verify_response_matches_request(req, bad_resp)

    # Workflow ID mismatch
    bad_wf = TaskResponse.ok(req, sender="stockrescue-inventory-bom", result={"ok": True})
    bad_wf.workflow_id = "diff-wf-id"
    with pytest.raises(CorrelationMismatchError):
        verify_response_matches_request(req, bad_wf)


# ---------------------------------------------------------------------------
# Part B & C: Specialist Dispatcher & Security Tests
# ---------------------------------------------------------------------------

def test_dispatcher_empty_allowlist_fails_closed():
    dispatcher = SpecialistDispatcher(
        role="inventory_bom",
        agent_name="stockrescue-inventory-bom",
        allowed_senders=set(),  # empty allowlist
    )
    req = TaskRequest.create(
        task_type="inventory_bom",
        workflow_id="wf-1",
        trace_id="tr-1",
        sender="unauthorized-agent",
        recipient="stockrescue-inventory-bom",
        payload={"case": make_case().model_dump(mode="json"), "context": {"workflow_id": "wf-1", "trace_id": "tr-1", "case_id": "CASE-1"}},
    )
    resp = dispatcher.dispatch("unauthorized-agent", req)
    assert resp.status == TaskResponseStatus.REJECTED
    assert resp.error_code == TaskErrorCode.UNAUTHORIZED_SENDER


def test_dispatcher_unauthorized_framework_sender_rejected():
    dispatcher = SpecialistDispatcher(
        role="inventory_bom",
        agent_name="stockrescue-inventory-bom",
        allowed_senders={"trusted-coordinator-address"},
    )
    req = TaskRequest.create(
        task_type="inventory_bom",
        workflow_id="wf-1",
        trace_id="tr-1",
        sender="trusted-coordinator-address",  # claims to be trusted in payload
        recipient="stockrescue-inventory-bom",
        payload={"case": make_case().model_dump(mode="json"), "context": {"workflow_id": "wf-1", "trace_id": "tr-1", "case_id": "CASE-1"}},
    )
    # But actual framework_sender passed from uagents Context is attacker address
    resp = dispatcher.dispatch("attacker-address", req)
    assert resp.status == TaskResponseStatus.REJECTED
    assert resp.error_code == TaskErrorCode.UNAUTHORIZED_SENDER


def test_dispatcher_forbidden_task_type_rejected():
    # Supplier discovery specialist must NOT execute procurement approval or purchasing
    dispatcher = SpecialistDispatcher(
        role="supplier_discovery",
        agent_name="stockrescue-supplier-discovery",
        allowed_senders={"coordinator-addr"},
    )
    req = TaskRequest.create(
        task_type="procurement_draft",  # forbidden for supplier_discovery
        workflow_id="wf-1",
        trace_id="tr-1",
        sender="coordinator-addr",
        recipient="stockrescue-supplier-discovery",
        payload={"a": 1},
    )
    resp = dispatcher.dispatch("coordinator-addr", req)
    assert resp.status == TaskResponseStatus.REJECTED
    assert resp.error_code == TaskErrorCode.TASK_TYPE_FORBIDDEN


def test_dispatcher_expired_deadline_rejected():
    dispatcher = SpecialistDispatcher(
        role="inventory_bom",
        agent_name="stockrescue-inventory-bom",
        allowed_senders={"coord-addr"},
    )
    now = datetime.now(timezone.utc)
    req = TaskRequest.create(
        task_type="inventory_bom",
        workflow_id="wf-1",
        trace_id="tr-1",
        sender="coord-addr",
        recipient="stockrescue-inventory-bom",
        payload={"case": make_case().model_dump(mode="json"), "context": {"workflow_id": "wf-1", "trace_id": "tr-1", "case_id": "CASE-1"}},
        created_at=now - timedelta(seconds=20),
        timeout_seconds=5.0,  # expired 15 seconds ago
    )
    resp = dispatcher.dispatch("coord-addr", req)
    assert resp.status == TaskResponseStatus.REJECTED
    assert resp.error_code == TaskErrorCode.DEADLINE_EXPIRED


def test_dispatcher_idempotency_caching():
    dispatcher = SpecialistDispatcher(
        role="inventory_bom",
        agent_name="stockrescue-inventory-bom",
        allowed_senders={"coord-addr"},
    )
    case = make_case()
    ctx = {"workflow_id": "wf-1", "trace_id": "tr-1", "case_id": case.case_id, "as_of": NOW.isoformat()}
    req = TaskRequest.create(
        task_type="inventory_bom",
        workflow_id="wf-1",
        trace_id="tr-1",
        sender="coord-addr",
        recipient="stockrescue-inventory-bom",
        payload={"case": case.model_dump(mode="json"), "context": ctx},
    )
    resp1 = dispatcher.dispatch("coord-addr", req)
    assert resp1.status == TaskResponseStatus.OK

    # Re-dispatch with exact same correlation_id returns cached response
    resp2 = dispatcher.dispatch("coord-addr", req)
    assert resp2.status == TaskResponseStatus.OK
    assert resp2.message_id == resp1.message_id
    assert resp2.result_json == resp1.result_json


def test_dispatcher_execution_all_specialists():
    case = make_case()
    case_dict = case.model_dump(mode="json")
    ctx_dict = {"workflow_id": "wf-1", "trace_id": "tr-1", "case_id": case.case_id, "as_of": NOW.isoformat()}
    trusted = {"coord-addr"}

    # 1. Inventory BOM
    d_inv = SpecialistDispatcher("inventory_bom", "inv", trusted)
    req = TaskRequest.create(task_type="inventory_bom", workflow_id="w", trace_id="t", sender="coord-addr", recipient="inv",
                             payload={"case": case_dict, "context": ctx_dict})
    r_inv = d_inv.dispatch("coord-addr", req)
    assert r_inv.status == TaskResponseStatus.OK
    inv_data = r_inv.get_result()

    # 2. Job Impact
    d_job = SpecialistDispatcher("job_impact", "job", trusted)
    req = TaskRequest.create(task_type="job_impact", workflow_id="w", trace_id="t", sender="coord-addr", recipient="job",
                             payload={"case": case_dict, "inventory": inv_data, "context": ctx_dict})
    r_job = d_job.dispatch("coord-addr", req)
    assert r_job.status == TaskResponseStatus.OK

    # 3. Supplier Discovery
    d_sup = SpecialistDispatcher("supplier_discovery", "sup", trusted)
    req = TaskRequest.create(task_type="supplier_discovery", workflow_id="w", trace_id="t", sender="coord-addr", recipient="sup",
                             payload={"case": case_dict, "inventory": inv_data, "context": ctx_dict})
    r_sup = d_sup.dispatch("coord-addr", req)
    assert r_sup.status == TaskResponseStatus.OK
    sup_data = r_sup.get_result()

    # 4. Compatibility
    d_comp = SpecialistDispatcher("compatibility", "comp", trusted)
    req = TaskRequest.create(task_type="compatibility", workflow_id="w", trace_id="t", sender="coord-addr", recipient="comp",
                             payload={"case": case_dict, "supplier_discovery": sup_data, "context": ctx_dict})
    r_comp = d_comp.dispatch("coord-addr", req)
    assert r_comp.status == TaskResponseStatus.OK

    # 5. Quote Risk
    d_risk = SpecialistDispatcher("quote_risk", "risk", trusted)
    req = TaskRequest.create(task_type="quote_risk", workflow_id="w", trace_id="t", sender="coord-addr", recipient="risk",
                             payload={"case": case_dict, "required_quantity": 2, "as_of": NOW.isoformat()})
    r_risk = d_risk.dispatch("coord-addr", req)
    assert r_risk.status == TaskResponseStatus.OK

    # 6. Recovery Planner
    d_plan = SpecialistDispatcher("recovery_planner", "plan", trusted)
    req = TaskRequest.create(task_type="recovery_planner", workflow_id="w", trace_id="t", sender="coord-addr", recipient="plan",
                             payload={"case": case_dict, "as_of": NOW.isoformat()})
    r_plan = d_plan.dispatch("coord-addr", req)
    assert r_plan.status == TaskResponseStatus.OK
    plan_dict = r_plan.get_result()["recovery_plan"]
    plan = _clean_plan_payload(plan_dict)

    # 7. Procurement Draft
    approval = make_approval(plan)
    d_proc = SpecialistDispatcher("procurement", "proc", trusted)
    req = TaskRequest.create(task_type="procurement_draft", workflow_id="w", trace_id="t", sender="coord-addr", recipient="proc",
                             payload={
                                 "case": case_dict,
                                 "plan": plan.model_dump(mode="json"),
                                 "approval": approval.model_dump(mode="json"),
                                 "current_plan": plan.model_dump(mode="json"),
                                 "approval_verified": True,
                                 "as_of": NOW.isoformat(),
                             })
    r_proc = d_proc.dispatch("coord-addr", req)
    assert r_proc.status == TaskResponseStatus.OK

    # 8. Vendor Voice
    d_voice = SpecialistDispatcher("vendor_voice", "voice", trusted)
    req = TaskRequest.create(task_type="vendor_communication_prep", workflow_id="w", trace_id="t", sender="coord-addr", recipient="voice",
                             payload={"case": case_dict, "plan": plan.model_dump(mode="json"), "as_of": NOW.isoformat()})
    r_voice = d_voice.dispatch("coord-addr", req)
    assert r_voice.status == TaskResponseStatus.OK
    assert r_voice.get_result()["procurement_authorized"] is False

    # 9. Delivery Watch
    event = DeliveryEvent(
        event_id="evt-1",
        plan_id=plan.plan_id,
        status="in_transit",
        occurred_at=NOW,
        source="carrier_api",
        details="Dispatched from hub",
    )
    d_del = SpecialistDispatcher("delivery_watch", "del", trusted)
    req = TaskRequest.create(task_type="delivery_watch", workflow_id="w", trace_id="t", sender="coord-addr", recipient="del",
                             payload={"plan": plan.model_dump(mode="json"), "events": [event.model_dump(mode="json")], "as_of": NOW.isoformat()})
    r_del = d_del.dispatch("coord-addr", req)
    assert r_del.status == TaskResponseStatus.OK


# ---------------------------------------------------------------------------
# Part B: Mailbox Entrypoint Initialization Tests
# ---------------------------------------------------------------------------

def test_specialist_agents_create_successfully():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    agents = [
        create_inventory_agent(VALID_SEED, allowed_senders={"coord"}),
        create_job_impact_agent(VALID_SEED, allowed_senders={"coord"}),
        create_supplier_discovery_agent(VALID_SEED, allowed_senders={"coord"}),
        create_compatibility_agent(VALID_SEED, allowed_senders={"coord"}),
        create_quote_risk_agent(VALID_SEED, allowed_senders={"coord"}),
        create_recovery_planner_agent(VALID_SEED, allowed_senders={"coord"}),
        create_procurement_agent(VALID_SEED, allowed_senders={"coord"}),
        create_vendor_voice_agent(VALID_SEED, allowed_senders={"coord"}),
        create_delivery_watch_agent(VALID_SEED, allowed_senders={"coord"}),
    ]
    for agent in agents:
        assert agent.name.startswith("stockrescue-")
        assert agent.address is not None


# ---------------------------------------------------------------------------
# Part C: Transport Bounded Retries & Error Handling Tests
# ---------------------------------------------------------------------------

def test_transport_retries_on_timeout_and_succeeds():
    calls = []

    def mock_remote(recipient: str, request: TaskRequest) -> TaskResponse:
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            return TaskResponse.failure(
                request,
                sender=recipient,
                status=TaskResponseStatus.TIMEOUT,
                error_code=TaskErrorCode.TIMEOUT,
                error_detail="Gateway timeout",
            )
        return TaskResponse.ok(
            request,
            sender=recipient,
            result={"status": "complete", "affected_sku": "BRG-6204"},
        )

    transport = SpecialistTransport(
        mode="agentverse",
        sender_identity="stockrescue-coordinator",
        recipient_addresses={"inventory_bom": "agent1qw6xefvftxwq6dfue4h49n4gwh3hhslqwwmcd80e676f937lnpdq7mfduyd"},
        max_retries=2,
        remote_caller=mock_remote,
    )

    result = transport.invoke(
        task_type="inventory_bom",
        payload={"a": 1},
        workflow_id="wf-1",
        trace_id="tr-1",
        role="inventory_bom",
    )
    assert len(calls) == 2
    assert result["status"] == "complete"


def test_transport_does_not_retry_non_retryable_rejection():
    calls = []

    def mock_remote(recipient: str, request: TaskRequest) -> TaskResponse:
        calls.append(len(calls) + 1)
        return TaskResponse.failure(
            request,
            sender=recipient,
            status=TaskResponseStatus.REJECTED,
            error_code=TaskErrorCode.TASK_TYPE_FORBIDDEN,
            error_detail="Task forbidden",
        )

    transport = SpecialistTransport(
        mode="agentverse",
        sender_identity="stockrescue-coordinator",
        recipient_addresses={"supplier_discovery": "agent1qw6xefvftxwq6dfue4h49n4gwh3hhslqwwmcd80e676f937lnpdq7mfduyd"},
        max_retries=3,
        remote_caller=mock_remote,
    )

    with pytest.raises(SpecialistExecutionError) as exc_info:
        transport.invoke(
            task_type="procurement_draft",
            payload={"a": 1},
            workflow_id="wf-1",
            trace_id="tr-1",
            role="supplier_discovery",
        )
    assert len(calls) == 1  # No retries on REJECTED
    assert exc_info.value.status == TaskResponseStatus.REJECTED


def test_remote_mode_with_no_caller_fails_closed():
    transport = SpecialistTransport(
        mode="agentverse",
        sender_identity="stockrescue-coordinator",
        recipient_addresses={"inventory_bom": "agent1qw6xefvftxwq6dfue4h49n4gwh3hhslqwwmcd80e676f937lnpdq7mfduyd"},
        remote_caller=None,
    )
    with pytest.raises(SpecialistExecutionError) as exc_info:
        transport.invoke(
            task_type="inventory_bom",
            payload={"a": 1},
            workflow_id="wf-1",
            trace_id="tr-1",
            role="inventory_bom",
        )
    assert exc_info.value.status == TaskResponseStatus.UNAVAILABLE
    assert exc_info.value.error_code == TaskErrorCode.AGENT_UNAVAILABLE
    assert "Remote caller is missing" in str(exc_info.value)


def test_remote_mode_with_missing_or_invalid_address():
    # Missing address
    transport_missing = SpecialistTransport(
        mode="agentverse",
        sender_identity="stockrescue-coordinator",
        recipient_addresses={},
        remote_caller=lambda r, req: TaskResponse.ok(req, sender=r, result={}),
    )
    with pytest.raises(SpecialistExecutionError) as exc_info:
        transport_missing.invoke(
            task_type="inventory_bom",
            payload={"a": 1},
            workflow_id="wf-1",
            trace_id="tr-1",
            role="inventory_bom",
        )
    assert exc_info.value.status == TaskResponseStatus.INVALID_REQUEST

    # Invalid address format (not starting with agent1 or wrong length)
    transport_invalid = SpecialistTransport(
        mode="agentverse",
        sender_identity="stockrescue-coordinator",
        recipient_addresses={"inventory_bom": "not_an_agent_address"},
        remote_caller=lambda r, req: TaskResponse.ok(req, sender=r, result={}),
    )
    with pytest.raises(SpecialistExecutionError) as exc_info2:
        transport_invalid.invoke(
            task_type="inventory_bom",
            payload={"a": 1},
            workflow_id="wf-1",
            trace_id="tr-1",
            role="inventory_bom",
        )
    assert exc_info2.value.status == TaskResponseStatus.INVALID_REQUEST


def test_remote_failure_never_turns_into_local_success():
    def failing_caller(recipient: str, request: TaskRequest) -> TaskResponse:
        return TaskResponse.failure(
            request,
            sender=recipient,
            status=TaskResponseStatus.UNAVAILABLE,
            error_code=TaskErrorCode.AGENT_UNAVAILABLE,
            error_detail="Simulated outage",
        )

    transport = SpecialistTransport(
        mode="agentverse",
        sender_identity="stockrescue-coordinator",
        recipient_addresses={"inventory_bom": "agent1qw6xefvftxwq6dfue4h49n4gwh3hhslqwwmcd80e676f937lnpdq7mfduyd"},
        max_retries=1,
        remote_caller=failing_caller,
    )

    with pytest.raises(SpecialistExecutionError) as exc_info:
        transport.invoke(
            task_type="inventory_bom",
            payload={"a": 1},
            workflow_id="wf-1",
            trace_id="tr-1",
            role="inventory_bom",
            force_local=True,  # Even if force_local passed, remote mode must NOT run local code!
        )
    assert exc_info.value.status == TaskResponseStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# Part D: Coordinator LangGraph Integration Tests
# ---------------------------------------------------------------------------

def test_coordinator_local_mode_success():
    case = make_case()
    result = run_coordinator(case, as_of=NOW)
    assert result.outcome == CoordinatorOutcome.AWAITING_APPROVAL
    assert result.recovery_plan is not None
    assert result.procurement_authorized is False
    # Check messages record local execution
    for msg in result.messages:
        assert msg.payload.get("execution_mode") == "local"


def test_coordinator_handles_specialist_failure_by_blocking():
    # If inventory specialist fails in remote transport, workflow must mark BLOCKED
    def failing_remote(recipient: str, request: TaskRequest) -> TaskResponse:
        if request.task_type == "inventory_bom":
            return TaskResponse.failure(
                request,
                sender=recipient,
                status=TaskResponseStatus.UNAVAILABLE,
                error_code=TaskErrorCode.AGENT_UNAVAILABLE,
                error_detail="Mailbox unreachable",
            )
        return TaskResponse.ok(request, sender=recipient, result={})

    failing_transport = SpecialistTransport(
        mode="agentverse",
        sender_identity="stockrescue-coordinator",
        recipient_addresses={"inventory_bom": "agent1qw6xefvftxwq6dfue4h49n4gwh3hhslqwwmcd80e676f937lnpdq7mfduyd"},
        max_retries=0,
        remote_caller=failing_remote,
    )

    case = make_case()
    result = run_coordinator(case, as_of=NOW, transport=failing_transport)
    assert result.outcome == CoordinatorOutcome.BLOCKED
    assert result.recovery_plan is None
    assert "specialist failure" in result.summary.lower()
    # Ensure failure is recorded in messages
    failure_messages = [m for m in result.messages if m.message_type == "specialist.failure"]
    assert len(failure_messages) == 1
    assert "Mailbox unreachable" in failure_messages[0].payload["error"]
