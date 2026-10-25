from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from agents.compatibility.agent import run_compatibility
from agents.inventory_bom.agent import run_inventory_bom
from agents.job_impact.agent import run_job_impact
from agents.protocols.transport import SpecialistExecutionError, SpecialistTransport
from agents.quote_risk.agent import QuoteRiskResult, run_quote_risk
from agents.recovery_planner.agent import RecoveryPlannerResult, run_recovery_planner
from agents.supplier_discovery.agent import run_supplier_discovery
from observability.tracing import trace_span
from shared.agent_contracts import (
    AgentContext,
    AgentIssue,
    AgentResultBase,
    AgentStatus,
    CompatibilityResult,
    CoordinatorOutcome,
    CoordinatorResult,
    InventoryBomResult,
    JobImpactResult,
    MAX_QUANTITY,
    SupplierDiscoveryResult,
    build_agent_context,
    validate_case,
)
from shared.checkpointing import get_checkpointer
from shared.config import get_settings
from shared.models import AgentMessage, DisruptionCase, RecoveryPlan, RecoveryPlanStatus
from tools.recovery_optimizer import (
    NoProcurementRequired,
    RecoveryOptimizationError,
    plan_recovery,
)


import contextvars

_ACTIVE_TRANSPORT: contextvars.ContextVar[SpecialistTransport | None] = contextvars.ContextVar(
    "_ACTIVE_TRANSPORT", default=None
)


class CoordinatorState(TypedDict, total=False):
    case: DisruptionCase
    context: AgentContext
    specialist_error: str | None
    inventory: Any
    job_impact: Any
    supplier_discovery: Any
    compatibility: Any
    quote_risk: Any
    recovery_plan: RecoveryPlan | None
    outcome: CoordinatorOutcome
    messages: list[AgentMessage]
    summary: str
    result: CoordinatorResult


def _get_transport() -> SpecialistTransport:
    t = _ACTIVE_TRANSPORT.get()
    if t is not None:
        return t
    settings = get_settings()
    recipient_addresses = {
        role: settings.get_specialist_address(role)
        for role in [
            "inventory_bom",
            "job_impact",
            "supplier_discovery",
            "compatibility",
            "quote_risk",
            "recovery_planner",
            "procurement",
            "vendor_voice",
            "delivery_watch",
        ]
    }
    return SpecialistTransport(
        mode=settings.specialist_transport_mode,
        sender_identity=settings.agentverse_coordinator_address or "stockrescue-coordinator",
        recipient_addresses=recipient_addresses,
        timeout_seconds=settings.specialist_timeout_seconds,
        max_retries=settings.specialist_max_retries,
    )


def _message(
    context: AgentContext,
    *,
    sender: str,
    message_type: str,
    status: str,
    issue_codes: list[str],
    summary: str,
    extra: dict[str, Any] | None = None,
) -> AgentMessage:
    payload: dict[str, Any] = {
        "case_id": context.case_id,
        "status": status,
        "issue_codes": issue_codes[:20],
        "summary": summary[:300],
    }

    if extra:
        payload.update(extra)

    return AgentMessage(
        workflow_id=context.workflow_id,
        trace_id=context.trace_id,
        sender=sender,
        recipient="coordinator",
        message_type=message_type,
        payload=payload,
    )


def _result_message(
    context: AgentContext,
    sender: str,
    result: AgentResultBase,
    summary: str,
    extra: dict[str, Any] | None = None,
) -> AgentMessage:
    return _message(
        context,
        sender=sender,
        message_type=f"{sender}.result",
        status=result.status.value,
        issue_codes=[issue.code for issue in result.issues],
        summary=summary,
        extra=extra,
    )


def _inventory_node(state: CoordinatorState) -> dict:
    transport = _get_transport()
    case = state["case"]
    context = state["context"]
    try:
        raw = transport.invoke(
            "inventory_bom",
            {"case": case, "context": context},
            workflow_id=context.workflow_id,
            trace_id=context.trace_id,
            role="inventory_bom",
        )
        return {"inventory": InventoryBomResult.model_validate(raw)}
    except Exception as exc:
        return {
            "specialist_error": f"inventory_bom: {exc}",
            "inventory": InventoryBomResult(
                context=context,
                status=AgentStatus.BLOCKED,
                issues=[AgentIssue(code="specialist_failed", detail=str(exc)[:300])],
                affected_sku=case.affected_sku,
                demand_quantity=0,
                inventory_found=False,
                quantity_on_hand=0,
                quantity_committed=0,
                available_quantity=0,
                shortage_quantity=0,
                locations=[],
                unsupported_job_skus=[],
            ),
        }


def _job_impact_node(state: CoordinatorState) -> dict:
    if state.get("specialist_error"):
        return {}
    transport = _get_transport()
    case = state["case"]
    inventory = state["inventory"]
    context = state["context"]
    try:
        raw = transport.invoke(
            "job_impact",
            {"case": case, "inventory": inventory, "context": context},
            workflow_id=context.workflow_id,
            trace_id=context.trace_id,
            role="job_impact",
        )
        return {"job_impact": JobImpactResult.model_validate(raw)}
    except Exception as exc:
        return {
            "specialist_error": f"job_impact: {exc}",
            "job_impact": JobImpactResult(
                context=context,
                status=AgentStatus.BLOCKED,
                issues=[AgentIssue(code="specialist_failed", detail=str(exc)[:300])],
                affected_sku=case.affected_sku,
                jobs=[],
                total_demand=0,
                inventory_available=0,
                uncovered_demand=0,
                earliest_deadline=None,
                unsupported_job_skus=[],
            ),
        }


def _supplier_discovery_node(state: CoordinatorState) -> dict:
    if state.get("specialist_error"):
        return {}
    transport = _get_transport()
    case = state["case"]
    inventory = state["inventory"]
    context = state["context"]
    try:
        raw = transport.invoke(
            "supplier_discovery",
            {"case": case, "inventory": inventory, "context": context},
            workflow_id=context.workflow_id,
            trace_id=context.trace_id,
            role="supplier_discovery",
        )
        return {"supplier_discovery": SupplierDiscoveryResult.model_validate(raw)}
    except Exception as exc:
        return {
            "specialist_error": f"supplier_discovery: {exc}",
            "supplier_discovery": SupplierDiscoveryResult(
                context=context,
                status=AgentStatus.BLOCKED,
                issues=[AgentIssue(code="specialist_failed", detail=str(exc)[:300])],
                affected_sku=case.affected_sku,
                required_quantity=0,
                offers=[],
                excluded_failed_supplier_offers=0,
                excluded_unavailable_or_stale_offers=0,
            ),
        }


def _compatibility_node(state: CoordinatorState) -> dict:
    if state.get("specialist_error"):
        return {}
    transport = _get_transport()
    case = state["case"]
    discovery = state["supplier_discovery"]
    context = state["context"]
    try:
        raw = transport.invoke(
            "compatibility",
            {"case": case, "supplier_discovery": discovery, "context": context},
            workflow_id=context.workflow_id,
            trace_id=context.trace_id,
            role="compatibility",
        )
        return {"compatibility": CompatibilityResult.model_validate(raw)}
    except Exception as exc:
        return {
            "specialist_error": f"compatibility: {exc}",
            "compatibility": CompatibilityResult(
                context=context,
                status=AgentStatus.BLOCKED,
                issues=[AgentIssue(code="specialist_failed", detail=str(exc)[:300])],
                affected_sku=case.affected_sku,
                records=[],
                safe_offer_ids=[],
                excluded_offer_ids=[],
            ),
        }


def _quote_risk_node(state: CoordinatorState) -> dict:
    if state.get("specialist_error"):
        return {}
    transport = _get_transport()
    case = state["case"]
    context = state["context"]
    inventory = state.get("inventory")
    demand_qty = getattr(inventory, "demand_quantity", None) if inventory else None
    try:
        raw = transport.invoke(
            "quote_risk",
            {
                "case": case,
                "required_quantity": demand_qty,
                "as_of": context.as_of.isoformat(),
            },
            workflow_id=context.workflow_id,
            trace_id=context.trace_id,
            role="quote_risk",
        )
        return {"quote_risk": QuoteRiskResult.model_validate(raw)}
    except Exception as exc:
        return {
            "specialist_error": f"quote_risk: {exc}",
            "quote_risk": QuoteRiskResult(
                case_id=case.case_id,
                as_of=context.as_of,
                required_quantity=demand_qty or 0,
                offers=[],
                eligible_offer_ids=[],
                blocked_offer_ids=[],
                review_offer_ids=[],
                status="blocked",
            ),
        }


def _planning_node(state: CoordinatorState) -> dict:
    transport = _get_transport()
    context = state["context"]
    case = state["case"]
    execution_mode = transport.mode

    if state.get("specialist_error"):
        error_msg = state["specialist_error"]
        summary = f"Recovery planning was blocked due to specialist failure ({error_msg})."
        messages = [
            _message(
                context,
                sender="coordinator",
                message_type="specialist.failure",
                status="blocked",
                issue_codes=["specialist_failed"],
                summary=summary,
                extra={"error": error_msg, "execution_mode": execution_mode},
            )
        ]
        return {
            "recovery_plan": None,
            "outcome": CoordinatorOutcome.BLOCKED,
            "summary": summary,
            "messages": messages,
        }

    inventory = state["inventory"]
    job_impact = state["job_impact"]
    discovery = state["supplier_discovery"]
    compatibility = state["compatibility"]
    quote_risk = state.get("quote_risk")

    messages = [
        _result_message(
            context,
            "inventory_bom",
            inventory,
            "Inventory position and net availability calculated.",
            {
                "sku": inventory.affected_sku,
                "available_quantity": inventory.available_quantity,
                "shortage_quantity": inventory.shortage_quantity,
                "execution_mode": execution_mode,
            },
        ),
        _result_message(
            context,
            "job_impact",
            job_impact,
            "Affected jobs sorted by deadline and priority.",
            {
                "job_count": len(job_impact.jobs),
                "uncovered_demand": job_impact.uncovered_demand,
                "execution_mode": execution_mode,
            },
        ),
        _result_message(
            context,
            "supplier_discovery",
            discovery,
            "Supplier offers screened against currency, quote freshness, and quantity.",
            {
                "candidate_count": len(discovery.offers),
                "required_quantity": discovery.required_quantity,
                "execution_mode": execution_mode,
            },
        ),
        _result_message(
            context,
            "compatibility",
            compatibility,
            "Candidate SKUs checked against stored compatibility evidence.",
            {
                "safe_offer_count": len(compatibility.safe_offer_ids),
                "excluded_offer_count": len(compatibility.excluded_offer_ids),
                "execution_mode": execution_mode,
            },
        ),
    ]

    if quote_risk:
        messages.append(
            _message(
                context,
                sender="quote_risk",
                message_type="quote_risk.result",
                status=quote_risk.status,
                issue_codes=quote_risk.blocked_offer_ids[:10],
                summary=f"Quote risk assessment completed with status {quote_risk.status}.",
                extra={
                    "eligible_count": len(quote_risk.eligible_offer_ids),
                    "blocked_count": len(quote_risk.blocked_offer_ids),
                    "review_count": len(quote_risk.review_offer_ids),
                    "execution_mode": execution_mode,
                },
            )
        )

    recovery_plan = None
    outcome = CoordinatorOutcome.BLOCKED
    summary = (
        "Recovery planning was blocked because required input data "
        "was not sufficiently verified."
    )

    unsupported_jobs = bool(
        inventory.unsupported_job_skus
        or job_impact.unsupported_job_skus
    )

    if not inventory.inventory_found:
        summary = (
            "Recovery planning is blocked until physical inventory "
            "for the affected SKU is verified."
        )
    elif inventory.demand_quantity < 1:
        summary = (
            "Recovery planning is blocked because no affected job "
            "demand was supplied."
        )
    elif inventory.demand_quantity > MAX_QUANTITY:
        summary = (
            "Recovery planning is blocked because demand exceeds "
            "the current optimizer limit."
        )
    elif unsupported_jobs:
        summary = (
            "Recovery planning is blocked because this version "
            "supports one disrupted SKU per case."
        )
    else:
        planner_result = run_recovery_planner(case, as_of=context.as_of)
        recovery_plan = planner_result.recovery_plan
        outcome = CoordinatorOutcome(planner_result.outcome)
        summary = planner_result.summary

    messages.append(
        _message(
            context,
            sender="recovery_planner",
            message_type="recovery_planner.result",
            status=outcome.value,
            issue_codes=[] if recovery_plan is not None else [outcome.value],
            summary=summary,
            extra={
                "plan_id": recovery_plan.plan_id if recovery_plan else None,
                "plan_status": recovery_plan.status.value if recovery_plan else None,
                "procurement_authorized": False,
                "execution_mode": execution_mode,
            },
        )
    )

    return {
        "recovery_plan": recovery_plan,
        "outcome": outcome,
        "summary": summary,
        "messages": messages,
    }


def _finalize_node(state: CoordinatorState) -> dict:
    context = state["context"]
    case = state["case"]
    inventory = state.get("inventory") or InventoryBomResult(
        context=context,
        status=AgentStatus.BLOCKED,
        issues=[AgentIssue(code="not_executed", detail="Specialist was not executed")],
        affected_sku=case.affected_sku,
        demand_quantity=0,
        inventory_found=False,
        quantity_on_hand=0,
        quantity_committed=0,
        available_quantity=0,
        shortage_quantity=0,
        locations=[],
        unsupported_job_skus=[],
    )
    job_impact = state.get("job_impact") or JobImpactResult(
        context=context,
        status=AgentStatus.BLOCKED,
        issues=[AgentIssue(code="not_executed", detail="Specialist was not executed")],
        affected_sku=case.affected_sku,
        jobs=[],
        total_demand=0,
        inventory_available=0,
        uncovered_demand=0,
        earliest_deadline=None,
        unsupported_job_skus=[],
    )
    supplier_discovery = state.get("supplier_discovery") or SupplierDiscoveryResult(
        context=context,
        status=AgentStatus.BLOCKED,
        issues=[AgentIssue(code="not_executed", detail="Specialist was not executed")],
        affected_sku=case.affected_sku,
        required_quantity=0,
        offers=[],
        excluded_failed_supplier_offers=0,
        excluded_unavailable_or_stale_offers=0,
    )
    compatibility = state.get("compatibility") or CompatibilityResult(
        context=context,
        status=AgentStatus.BLOCKED,
        issues=[AgentIssue(code="not_executed", detail="Specialist was not executed")],
        affected_sku=case.affected_sku,
        records=[],
        safe_offer_ids=[],
        excluded_offer_ids=[],
    )

    result = CoordinatorResult(
        context=context,
        outcome=state["outcome"],
        inventory=inventory,
        job_impact=job_impact,
        supplier_discovery=supplier_discovery,
        compatibility=compatibility,
        recovery_plan=state.get("recovery_plan"),
        messages=state.get("messages", []),
        procurement_authorized=False,
        summary=state.get("summary", "Workflow completed."),
    )

    return {"result": result}


def build_coordinator_graph(checkpointer: Any = None):
    builder = StateGraph(CoordinatorState)

    builder.add_node("inventory", _inventory_node)
    builder.add_node("job_impact", _job_impact_node)
    builder.add_node("supplier_discovery", _supplier_discovery_node)
    builder.add_node("compatibility", _compatibility_node)
    builder.add_node("quote_risk", _quote_risk_node)
    builder.add_node("planning", _planning_node)
    builder.add_node("finalize", _finalize_node)

    builder.add_edge(START, "inventory")
    builder.add_edge("inventory", "job_impact")
    builder.add_edge("job_impact", "supplier_discovery")
    builder.add_edge("supplier_discovery", "compatibility")
    builder.add_edge("compatibility", "quote_risk")
    builder.add_edge("quote_risk", "planning")
    builder.add_edge("planning", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)


def run_coordinator_graph(
    case: DisruptionCase,
    *,
    workflow_id: str | None = None,
    trace_id: str | None = None,
    as_of: datetime | None = None,
    transport: SpecialistTransport | None = None,
) -> CoordinatorResult:
    clean_case = validate_case(case)
    context = build_agent_context(
        clean_case,
        workflow_id=workflow_id or str(uuid4()),
        trace_id=trace_id or str(uuid4()),
        as_of=as_of,
    )

    token = _ACTIVE_TRANSPORT.set(transport)
    try:
        with trace_span(
            "agents.coordinator.langgraph",
            workflow_id=context.workflow_id,
            metadata={
                "case_id": context.case_id,
                "stage": "langgraph_specialists",
            },
        ) as observation:
            checkpointer = get_checkpointer()
            graph = build_coordinator_graph(checkpointer=checkpointer)

            initial_state: dict[str, Any] = {
                "case": clean_case,
                "context": context,
            }

            final_state = graph.invoke(
                initial_state,
                config={
                    "configurable": {
                        "thread_id": context.workflow_id,
                    }
                },
            )
            result = final_state["result"]

            observation.update(
                metadata={
                    "outcome": result.outcome.value,
                    "agent_message_count": len(result.messages),
                    "procurement_authorized": False,
                }
            )

        return result
    finally:
        _ACTIVE_TRANSPORT.reset(token)


def get_coordinator_workflow_state(workflow_id: str) -> dict[str, Any] | None:
    checkpointer = get_checkpointer()
    if checkpointer is None:
        return None
    graph = build_coordinator_graph(checkpointer=checkpointer)
    snapshot = graph.get_state(config={"configurable": {"thread_id": workflow_id}})
    if snapshot and snapshot.values:
        return snapshot.values
    return None
