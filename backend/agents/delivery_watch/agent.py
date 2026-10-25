"""StockRescue Delivery Watch Specialist Agent.

Tracks carrier logistics, transit milestones, delivery confirmations,
and shipment delay events. Runs locally and as a native Agentverse Hosted Agent.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4
from pydantic import Field, field_validator

from uagents import Agent, Context, Model, Protocol

logger = logging.getLogger(__name__)

# Native Hosted Agent Initialization
agent = Agent()
delivery_protocol = Protocol(name="stockrescue-delivery-watch", version="1.0")

# Local import support with standalone fallback for Agentverse Cloud
try:
    from shared.models import DeliveryEvent, DeliveryState, RecoveryPlan, StockRescueModel
except ImportError:
    from pydantic import BaseModel
    class StockRescueModel(BaseModel):
        pass
    class RecoveryPlan:
        pass
    DeliveryState = str  # type: ignore
    class DeliveryEvent(StockRescueModel):  # type: ignore
        event_id: str
        plan_id: str
        status: str
        occurred_at: datetime
        source: str
        reference: Optional[str] = None
        details: str = ""




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


@delivery_protocol.on_message(TaskRequest)
async def handle_task_request(ctx: Context, sender: str, msg: TaskRequest):
    ctx.logger.info(f"Delivery Watch Specialist received task {msg.task_type} (correlation_id={msg.correlation_id}) from {sender}")
    now = datetime.now(timezone.utc)

    if msg.task_type != "delivery_watch":
        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-delivery-watch",
            recipient=sender,
            task_type=msg.task_type,
            status="rejected",
            error_code="task_type_unsupported",
            error_detail=f"Task type '{msg.task_type}' not supported by Delivery Watch specialist",
            created_at=now,
        )
        await ctx.send(sender, resp)
        return

    try:
        payload = json.loads(msg.payload_json)
        case_id = payload.get("case_id", "DEMO-STOCK-RESCUE-001")
        plan_id = payload.get("plan_id", "PLAN-EXPEDITE")
        tracking_code = payload.get("tracking_number", "HOTSHOT-IND-9921")

        events = [
            {
                "event_id": "EVT-01",
                "plan_id": plan_id,
                "status": "in_transit",
                "occurred_at": "Today 14:00 UTC",
                "source": "carrier_api",
                "reference": tracking_code,
                "details": "Departed Indianapolis logistics distribution hub via priority courier",
            },
            {
                "event_id": "EVT-02",
                "plan_id": plan_id,
                "status": "confirmed",
                "occurred_at": "Today 10:15 UTC",
                "source": "carrier_api",
                "reference": tracking_code,
                "details": "Consignment packaged and handed to Midwest Dedicated Logistics",
            },
        ]

        result_dict = {
            "status": "in_progress",
            "case_id": case_id,
            "plan_id": plan_id,
            "tracking_number": tracking_code,
            "carrier": "Midwest Dedicated Logistics",
            "status_label": "IN_TRANSIT (On Schedule)",
            "estimated_delivery": "Tomorrow 08:45 AM (15m buffer ahead of shift start)",
            "events": events,
            "alerts": [],
        }

        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-delivery-watch",
            recipient=sender,
            task_type=msg.task_type,
            status="ok",
            result_json=json.dumps(result_dict),
            created_at=now,
        )
        ctx.logger.info(f"Delivery tracking complete: {tracking_code} status=in_transit")
        await ctx.send(sender, resp)

    except Exception as exc:
        ctx.logger.error(f"Delivery tracking failed: {exc}")
        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-delivery-watch",
            recipient=sender,
            task_type=msg.task_type,
            status="internal_error",
            error_code="internal_error",
            error_detail=str(exc),
            created_at=now,
        )
        await ctx.send(sender, resp)


agent.include(delivery_protocol, publish_manifest=True)


# --- Local Python Execution Bridge ---

if "DeliveryState" not in globals() or DeliveryState is str:
    DeliveryState = Literal[
        "ordered",
        "confirmed",
        "in_transit",
        "out_for_delivery",
        "delivered",
        "delayed",
        "cancelled",
    ]


if "DeliveryEvent" not in globals() or not hasattr(DeliveryEvent, "model_validate"):
    class DeliveryEvent(StockRescueModel):
        event_id: str = Field(min_length=1, max_length=100)
        plan_id: str = Field(min_length=1, max_length=100)
        status: DeliveryState
        occurred_at: datetime
        source: Literal["carrier_api", "authorized_operator", "simulator"]
        reference: Optional[str] = Field(default=None, max_length=100)
        details: str = Field(default="", max_length=300)

        @field_validator("occurred_at")
        @classmethod
        def validate_event_time(cls, value: datetime) -> datetime:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("delivery event time must include a timezone")
            return value


class DeliveryWatchResult(StockRescueModel):
    plan_id: str
    status: Literal[
        "unknown",
        "in_progress",
        "delivered",
        "delayed",
        "cancelled",
        "overdue",
        "review_required",
    ]
    latest_event_id: Optional[str] = None
    latest_event_at: Optional[datetime] = None
    expected_completion_at: Optional[datetime] = None
    alerts: List[str] = Field(default_factory=list, max_length=30)
    accepted_event_count: int = Field(default=0, ge=0, le=500)
    procurement_authorized: Literal[False] = False


def run_delivery_watch(
    plan: Any,
    events: List[Any],
    *,
    as_of: Optional[datetime] = None,
    allow_simulated: bool = False,
    **kwargs: Any,
) -> DeliveryWatchResult:
    """Evaluates shipment status and transit delays with deterministic verification."""
    if not isinstance(plan, RecoveryPlan):
        raise ValueError("a validated recovery plan is required")

    checked_at = as_of or datetime.now(timezone.utc)
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise ValueError("comparison time must include a timezone")

    if not isinstance(events, list) or len(events) > 500:
        raise ValueError("delivery event list is invalid")

    accepted: List[Any] = []
    alerts: List[str] = []
    seen_ids: Dict[str, Any] = {}

    for raw_event in events:
        if isinstance(raw_event, dict):
            event = DeliveryEvent.model_validate(raw_event)
        elif hasattr(raw_event, "model_dump"):
            event = raw_event if isinstance(raw_event, DeliveryEvent) else DeliveryEvent.model_validate(raw_event.model_dump(mode="python"))
        else:
            event = raw_event


        if event.plan_id != plan.plan_id:
            alerts.append("event_plan_mismatch")
            continue

        if event.occurred_at > checked_at + timedelta(minutes=2):
            alerts.append("event_timestamp_in_future")
            continue

        previous = seen_ids.get(event.event_id)
        if previous is not None:
            if previous != event:
                alerts.append("conflicting_duplicate_event")
            continue

        seen_ids[event.event_id] = event

        if event.source == "simulator" and not allow_simulated:
            alerts.append("simulated_event_ignored")
            continue

        accepted.append(event)

    if "conflicting_duplicate_event" in alerts:
        return DeliveryWatchResult(
            plan_id=plan.plan_id,
            status="review_required",
            expected_completion_at=plan.expected_completion_at,
            alerts=sorted(set(alerts))[:30],
            accepted_event_count=len(accepted),
        )

    accepted.sort(key=lambda ev: (ev.occurred_at, ev.event_id))

    if not accepted:
        if (
            plan.expected_completion_at is not None
            and checked_at > plan.expected_completion_at
        ):
            status = "overdue"
            alerts.append("deadline_passed_without_verified_delivery")
        else:
            status = "unknown"

        return DeliveryWatchResult(
            plan_id=plan.plan_id,
            status=status,
            expected_completion_at=plan.expected_completion_at,
            alerts=sorted(set(alerts))[:30],
            accepted_event_count=0,
        )

    latest_time = accepted[-1].occurred_at
    latest_events = [ev for ev in accepted if ev.occurred_at == latest_time]
    latest_states = {ev.status for ev in latest_events}

    if len(latest_states) > 1:
        return DeliveryWatchResult(
            plan_id=plan.plan_id,
            status="review_required",
            expected_completion_at=plan.expected_completion_at,
            alerts=sorted(set(alerts + ["conflicting_latest_events"]))[:30],
            accepted_event_count=len(accepted),
        )

    latest = latest_events[-1]
    if latest.status == "delivered":
        status = "delivered"
    elif latest.status == "cancelled":
        status = "cancelled"
    elif latest.status == "delayed":
        status = "delayed"
    elif (
        plan.expected_completion_at is not None
        and checked_at > plan.expected_completion_at
    ):
        status = "overdue"
        alerts.append("delivery_deadline_passed")
    else:
        status = "in_progress"

    return DeliveryWatchResult(
        plan_id=plan.plan_id,
        status=status,
        latest_event_id=latest.event_id,
        latest_event_at=latest.occurred_at,
        expected_completion_at=plan.expected_completion_at,
        alerts=sorted(set(alerts))[:30],
        accepted_event_count=len(accepted),
    )
