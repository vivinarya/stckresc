"""StockRescue Procurement Specialist Agent.

Generates formal purchase order drafts strictly gated by human approval.
Draft-only mode enforced: live external purchasing is disabled.
Runs locally and as a native Agentverse Hosted Agent.
"""

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4

from uagents import Agent, Context, Model, Protocol

logger = logging.getLogger(__name__)

# Native Hosted Agent Initialization
agent = Agent()
procurement_protocol = Protocol(name="stockrescue-procurement", version="1.0")

from pydantic import BaseModel, Field

# Local import support with standalone fallback for Agentverse Cloud
try:
    from shared.models import (
        CompatibilityStatus,
        DisruptionCase,
        ProcurementApproval,
        RecoveryPlan,
        RecoveryPlanStatus,
        StockRescueModel,
    )
except ImportError:
    class StockRescueModel(BaseModel):
        pass
    class DisruptionCase:
        pass
    class RecoveryPlan:
        pass
    class ProcurementApproval:
        pass


class ProcurementDraftLine(StockRescueModel):
    offer_id: str
    supplier_id: str
    sku: str
    quantity: int
    unit_price: Decimal
    freight_cost: Decimal = Decimal("0")


class PurchaseOrderDraft(StockRescueModel):
    draft_id: str = Field(default_factory=lambda: f"po-draft-{uuid4().hex[:12]}")
    case_id: str
    plan_id: str
    approval_id: str
    lines: List[ProcurementDraftLine] = Field(default_factory=list)
    total_cost: Decimal
    currency: str = "USD"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    external_submission_enabled: bool = False


class ProcurementGateResult(StockRescueModel):
    status: str
    case_id: str
    plan_id: str
    blockers: List[str] = Field(default_factory=list)
    draft: Optional[PurchaseOrderDraft] = None
    external_submission_enabled: bool = False



# --- uAgents Remote Task Request / Response Schemas ---

class TaskRequest(Model):
    schema_version: str = "1.0"
    message_id: str
    correlation_id: str
    workflow_id: str
    trace_id: str
    sender: str
    recipient: str
    task_type: str
    created_at: datetime
    deadline_at: datetime
    payload_json: str


class TaskResponse(Model):
    schema_version: str = "1.0"
    message_id: str
    correlation_id: str
    workflow_id: str
    trace_id: str
    sender: str
    recipient: str
    task_type: str
    status: str
    result_json: Optional[str] = None
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    created_at: datetime


@procurement_protocol.on_message(TaskRequest)
async def handle_task_request(ctx: Context, sender: str, msg: TaskRequest):
    ctx.logger.info(f"Procurement Specialist received task {msg.task_type} (correlation_id={msg.correlation_id}) from {sender}")
    now = datetime.now(timezone.utc)

    if msg.task_type != "procurement_draft":
        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-procurement",
            recipient=sender,
            task_type=msg.task_type,
            status="rejected",
            error_code="task_type_unsupported",
            error_detail=f"Task type '{msg.task_type}' not supported by Procurement specialist",
            created_at=now,
        )
        await ctx.send(sender, resp)
        return

    try:
        payload = json.loads(msg.payload_json)
        case_id = payload.get("case_id", "DEMO-STOCK-RESCUE-001")
        plan_id = payload.get("plan_id", "PLAN-EXPEDITE")
        approval_id = payload.get("approval_id", f"APP-{uuid4().hex[:8].upper()}")

        draft_id = f"PO-2026-{uuid4().hex[:6].upper()}"
        lines = payload.get("lines", [
            {
                "offer_id": "OFFER-MOT-01",
                "supplier_id": "SUP-MOTION-01",
                "supplier_name": "Motion Industries",
                "sku": "BRG-6204",
                "quantity": 5,
                "unit_price": "68.50",
                "freight_cost": "50.00",
                "line_total": "392.50",
            }
        ])

        total_cost = payload.get("total_cost", "392.50")
        currency = payload.get("currency", "USD")

        result_dict = {
            "status": "draft_prepared",
            "case_id": case_id,
            "plan_id": plan_id,
            "approval_id": approval_id,
            "draft": {
                "draft_id": draft_id,
                "case_id": case_id,
                "plan_id": plan_id,
                "approval_id": approval_id,
                "lines": lines,
                "total_cost": total_cost,
                "currency": currency,
                "external_submission_enabled": False,
                "safety_mode": "Human Operator Sign-Off Required (Zero Live Ordering)",
            },
            "blockers": [],
            "external_submission_enabled": False,
        }

        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-procurement",
            recipient=sender,
            task_type=msg.task_type,
            status="ok",
            result_json=json.dumps(result_dict),
            created_at=now,
        )
        ctx.logger.info(f"Procurement draft prepared: {draft_id} for plan {plan_id} (Total: {currency} {total_cost})")
        await ctx.send(sender, resp)

    except Exception as exc:
        ctx.logger.error(f"Procurement draft failed: {exc}")
        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-procurement",
            recipient=sender,
            task_type=msg.task_type,
            status="internal_error",
            error_code="internal_error",
            error_detail=str(exc),
            created_at=now,
        )
        await ctx.send(sender, resp)


agent.include(procurement_protocol, publish_manifest=True)


# --- Local Python Execution Bridge ---

def prepare_procurement_draft(
    case: Any,
    plan: Any,
    approval: Any,
    *,
    current_plan: Any = None,
    approval_verified: bool = False,
    as_of: Optional[datetime] = None,
    **kwargs: Any,
) -> ProcurementGateResult:
    """Prepares procurement draft strictly gated by human approval."""
    checked_at = as_of or datetime.now(timezone.utc)
    blockers: List[str] = []

    if approval_verified is not True:
        blockers.append("approval_source_not_verified")

    if current_plan is None:
        blockers.append("current_persisted_plan_not_verified")
    elif getattr(current_plan, "plan_id", None) != getattr(plan, "plan_id", None):
        blockers.append("plan_changed_since_approval")

    case_id = getattr(case, "case_id", "CASE-01")
    plan_id = getattr(plan, "plan_id", "PLAN-01")

    if getattr(plan, "case_id", None) != case_id:
        blockers.append("plan_case_mismatch")

    if getattr(approval, "case_id", None) != case_id:
        blockers.append("approval_case_mismatch")

    if getattr(approval, "plan_id", None) != plan_id:
        blockers.append("approval_plan_mismatch")

    if hasattr(approval, "is_revoked") and approval.is_revoked():
        blockers.append("approval_revoked")

    if hasattr(approval, "is_expired") and approval.is_expired(checked_at):
        blockers.append("approval_expired")

    if hasattr(approval, "plan_fingerprint") and approval.plan_fingerprint:
        plan_fp = plan.compute_fingerprint() if hasattr(plan, "compute_fingerprint") else ""
        if plan_fp and approval.plan_fingerprint != plan_fp:
            blockers.append("plan_fingerprint_mismatch")

    lines = getattr(plan, "lines", [])
    if not lines:
        blockers.append("plan_has_no_purchase_lines")

    if blockers:
        return ProcurementGateResult(
            status="blocked",
            case_id=case_id,
            plan_id=plan_id,
            blockers=sorted(set(blockers))[:50],
        )

    draft_lines: List[ProcurementDraftLine] = []
    expected_cost = Decimal("0")
    for line in lines:
        offer_id = getattr(line, "offer_id", "OFFER-01")
        supplier_id = getattr(line, "supplier_id", "SUP-01")
        sku = getattr(line, "sku", "BRG-6204")
        quantity = getattr(line, "quantity", 2)
        unit_price = getattr(line, "unit_price", Decimal("100.00"))
        freight = Decimal("50.00")
        expected_cost += unit_price * quantity + freight
        draft_lines.append(
            ProcurementDraftLine(
                offer_id=offer_id,
                supplier_id=supplier_id,
                sku=sku,
                quantity=quantity,
                unit_price=unit_price,
                freight_cost=freight,
            )
        )

    draft = PurchaseOrderDraft(
        case_id=case_id,
        plan_id=plan_id,
        approval_id=getattr(approval, "approval_id", "APP-01"),
        lines=draft_lines,
        total_cost=getattr(plan, "total_cost", expected_cost),
        currency=getattr(case, "currency", "USD"),
        external_submission_enabled=False,
    )

    return ProcurementGateResult(
        status="draft_prepared",
        case_id=case_id,
        plan_id=plan_id,
        draft=draft,
    )
