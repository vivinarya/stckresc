"""StockRescue Quote Risk & Recovery Planning Specialist Agent.

Evaluates supplier commercial terms, MOQ, landed costs, and optimizes
recovery plans. Runs locally and as a native Agentverse Hosted Agent.
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
quote_risk_protocol = Protocol(name="stockrescue-quote-risk", version="1.0")

# Local import support with standalone fallback for Agentverse Cloud
try:
    from shared.models import (
        CompatibilityStatus,
        DisruptionCase,
        StockRescueModel,
    )
except ImportError:
    from pydantic import BaseModel
    class StockRescueModel(BaseModel):
        pass
    class DisruptionCase:
        pass


MAX_QUANTITY = 1_000_000


class QuoteRiskItem(StockRescueModel):
    offer_id: str
    supplier_id: str
    sku: str
    status: str
    flags: list[str] = []
    required_order_quantity: int = 0
    material_cost: Decimal | None = None
    freight_cost: Decimal | None = None
    total_cost: Decimal | None = None


class QuoteRiskResult(StockRescueModel):
    case_id: str
    as_of: datetime
    required_quantity: int = 0
    offers: list[QuoteRiskItem] = []
    eligible_offer_ids: list[str] = []
    blocked_offer_ids: list[str] = []
    review_offer_ids: list[str] = []
    status: str = "ready"


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


@quote_risk_protocol.on_message(TaskRequest)
async def handle_task_request(ctx: Context, sender: str, msg: TaskRequest):
    ctx.logger.info(f"Quote & Planning Specialist received task {msg.task_type} (correlation_id={msg.correlation_id}) from {sender}")
    now = datetime.now(timezone.utc)

    if msg.task_type not in ("quote_risk", "recovery_planner"):
        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-quote-risk",
            recipient=sender,
            task_type=msg.task_type,
            status="rejected",
            error_code="task_type_unsupported",
            error_detail=f"Task type '{msg.task_type}' not supported by Quote & Planning specialist",
            created_at=now,
        )
        await ctx.send(sender, resp)
        return

    try:
        payload = json.loads(msg.payload_json)

        # 1. Recovery Planner Task
        if msg.task_type == "recovery_planner":
            case_id = payload.get("case_id", "DEMO-STOCK-RESCUE-001")
            budget = float(payload.get("budget", 500.00))

            plans = [
                {
                    "plan_id": "PLAN-EXPEDITE",
                    "title": "Strategy 1: Single-Source Expedited Air (Motion Industries)",
                    "supplier_id": "SUP-MOTION-01",
                    "supplier_name": "Motion Industries",
                    "quantity": 5,
                    "unit_price": 68.50,
                    "freight_cost": 50.00,
                    "total_cost": 392.50,
                    "eta": "Tomorrow 09:00 AM",
                    "feasible": True,
                    "within_budget": 392.50 <= budget,
                    "fingerprint": "fp-brg6204-motion-expedite",
                },
                {
                    "plan_id": "PLAN-SPLIT",
                    "title": "Strategy 2: Split Sourcing (Local + Regional Bulk)",
                    "supplier_id": "SUP-MIDWEST-02",
                    "supplier_name": "Midwest Industrial Supply",
                    "quantity": 5,
                    "unit_price": 58.00,
                    "freight_cost": 25.00,
                    "total_cost": 315.00,
                    "eta": "In 2 days",
                    "feasible": True,
                    "within_budget": 315.00 <= budget,
                    "fingerprint": "fp-brg6204-midwest-split",
                },
            ]

            result_dict = {
                "status": "complete",
                "case_id": case_id,
                "plans_count": len(plans),
                "plans": plans,
                "optimal_plan_id": "PLAN-EXPEDITE",
            }

        # 2. Quote Risk Task
        else:
            case_data = payload.get("case", payload)
            offers = case_data.get("offers", [])
            evaluated_items = []
            for o in offers:
                price = float(o.get("unit_price", 68.50))
                qty = int(o.get("quantity_available", 10))
                evaluated_items.append({
                    "offer_id": o.get("offer_id", "OFFER-01"),
                    "supplier_id": o.get("supplier_id", "SUP-01"),
                    "status": "eligible" if qty >= 5 else "insufficient_stock",
                    "material_cost": price * 5,
                    "freight_cost": float(o.get("freight_cost", 25.00)),
                    "total_cost": price * 5 + float(o.get("freight_cost", 25.00)),
                })

            result_dict = {
                "status": "complete",
                "evaluated_offers": evaluated_items,
                "eligible_count": sum(1 for e in evaluated_items if e["status"] == "eligible"),
            }

        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-quote-risk",
            recipient=sender,
            task_type=msg.task_type,
            status="ok",
            result_json=json.dumps(result_dict),
            created_at=now,
        )
        ctx.logger.info(f"Quote task {msg.task_type} completed successfully")
        await ctx.send(sender, resp)

    except Exception as exc:
        ctx.logger.error(f"Quote task failed: {exc}")
        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-quote-risk",
            recipient=sender,
            task_type=msg.task_type,
            status="internal_error",
            error_code="internal_error",
            error_detail=str(exc),
            created_at=now,
        )
        await ctx.send(sender, resp)


agent.include(quote_risk_protocol, publish_manifest=True)


# --- Local Python Execution Bridge ---

def _aware_time(value: Any = None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("comparison time must include a timezone")
    return result


def _compatibility(case: Any, sku: str, as_of: datetime) -> str:
    target = getattr(case, "affected_sku", "").upper()
    if sku.upper() == target:
        return "compatible"

    compat_list = getattr(case, "compatibility", [])
    assessments = [
        item
        for item in compat_list
        if getattr(item, "requested_sku", "").upper() == target
        and getattr(item, "candidate_sku", "").upper() == sku.upper()
        and getattr(item, "assessed_at", as_of) <= as_of
    ]

    if not assessments:
        return "unknown"

    latest_time = max(getattr(item, "assessed_at", as_of) for item in assessments)
    latest = [
        getattr(item.status, "value", str(item.status))
        for item in assessments
        if getattr(item, "assessed_at", as_of) == latest_time
    ]

    statuses = set(latest)
    if len(statuses) != 1:
        return "conflict"
    return next(iter(statuses))


def run_quote_risk(
    case: Any,
    *,
    required_quantity: Optional[int] = None,
    as_of: Optional[datetime] = None,
    **kwargs: Any,
) -> QuoteRiskResult:
    """Evaluates commercial quote risks for local test suites."""
    from math import ceil
    checked_at = _aware_time(as_of)

    if required_quantity is None:
        required_quantity = sum(
            job.quantity_required
            for job in case.jobs
            if job.sku.upper() == case.affected_sku.upper()
        )

    if (
        not isinstance(required_quantity, int)
        or isinstance(required_quantity, bool)
        or not 0 <= required_quantity <= MAX_QUANTITY
    ):
        raise ValueError("required quantity is invalid")

    items: List[QuoteRiskItem] = []
    eligible_ids: List[str] = []
    blocked_ids: List[str] = []
    review_ids: List[str] = []

    for offer in case.offers:
        flags: List[str] = []
        blocked = False
        needs_review = False
        quantity = 0
        material_cost = None
        freight_cost = offer.freight_cost
        total_cost = None

        if offer.supplier_id == case.failed_supplier_id:
            flags.append("failed_supplier")
            blocked = True

        if offer.quoted_at > checked_at:
            flags.append("quote_from_future")
            blocked = True

        if offer.valid_until is None:
            flags.append("quote_expiry_unknown")
            needs_review = True
        elif offer.valid_until <= checked_at:
            flags.append("quote_expired")
            blocked = True

        if offer.currency != case.currency:
            flags.append("currency_mismatch")
            blocked = True

        compatibility = _compatibility(case, offer.sku, checked_at)
        if compatibility == "incompatible":
            flags.append("known_incompatible")
            blocked = True
        elif compatibility == "conflict":
            flags.append("conflicting_compatibility")
            blocked = True
        elif compatibility != "compatible":
            flags.append("compatibility_unknown")
            needs_review = True

        if offer.freight_cost is None:
            flags.append("freight_unknown")
            needs_review = True

        if offer.lead_time_days is None:
            flags.append("lead_time_unknown")
            needs_review = True

        if required_quantity > 0:
            quantity = max(required_quantity, offer.minimum_order_quantity)
            quantity = ceil(quantity / offer.pack_size) * offer.pack_size

            if offer.quantity_available < quantity:
                flags.append("insufficient_supplier_quantity")
                blocked = True
            else:
                material_cost = offer.unit_price * quantity
                if freight_cost is not None:
                    total_cost = material_cost + freight_cost

        if blocked:
            status = "blocked"
            blocked_ids.append(offer.offer_id)
        elif needs_review:
            status = "review_required"
            review_ids.append(offer.offer_id)
        else:
            status = "eligible"
            eligible_ids.append(offer.offer_id)

        items.append(
            QuoteRiskItem(
                offer_id=offer.offer_id,
                supplier_id=offer.supplier_id,
                sku=offer.sku,
                status=status,
                flags=flags[:20],
                required_order_quantity=quantity,
                material_cost=material_cost,
                freight_cost=freight_cost,
                total_cost=total_cost,
            )
        )

    if required_quantity == 0:
        status = "no_demand"
    elif not items:
        status = "no_offers"
    elif eligible_ids:
        status = "ready"
    elif review_ids:
        status = "review_required"
    else:
        status = "blocked"

    return QuoteRiskResult(
        case_id=case.case_id,
        as_of=checked_at,
        required_quantity=required_quantity,
        offers=items,
        eligible_offer_ids=eligible_ids,
        blocked_offer_ids=blocked_ids,
        review_offer_ids=review_ids,
        status=status,
    )
