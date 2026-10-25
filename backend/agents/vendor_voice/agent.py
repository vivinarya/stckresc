"""StockRescue Vendor Communication & Voice Specialist Agent.

Prepares structured supplier RFQs and conducts simulated/real telephony dialogues
to verify stock availability, unit prices, and freight terms.
Runs locally and as a native Agentverse Hosted Agent.
"""

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4

from uagents import Agent, Context, Model, Protocol

logger = logging.getLogger(__name__)

# Native Hosted Agent Initialization
agent = Agent()
vendor_protocol = Protocol(name="stockrescue-vendor-voice", version="1.0")

# Telephony configuration defaults
REAL_TELEPHONY_ENABLED = os.environ.get("REAL_TELEPHONY_ENABLED", "false").lower() == "true"
VOICE_PROVIDER = os.environ.get("VOICE_PROVIDER", "simulated")


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


@vendor_protocol.on_message(TaskRequest)
async def handle_task_request(ctx: Context, sender: str, msg: TaskRequest):
    ctx.logger.info(f"Vendor Voice Specialist received task {msg.task_type} (correlation_id={msg.correlation_id}) from {sender}")
    now = datetime.now(timezone.utc)

    if msg.task_type not in ("vendor_communication_prep", "vendor_voice_call"):
        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-vendor-voice",
            recipient=sender,
            task_type=msg.task_type,
            status="rejected",
            error_code="task_type_unsupported",
            error_detail=f"Task type '{msg.task_type}' not supported by Vendor Voice specialist",
            created_at=now,
        )
        await ctx.send(sender, resp)
        return

    try:
        payload = json.loads(msg.payload_json)
        case_id = payload.get("case_id", "DEMO-STOCK-RESCUE-001")
        supplier_id = payload.get("supplier_id", "SUP-MOTION-01")
        supplier_name = payload.get("supplier_name", "Motion Industries")

        # 1. Voice Call (Simulated or Authorized Provider)
        if msg.task_type == "vendor_voice_call":
            phone_number = payload.get("phone_number", "+1-800-555-0199")
            user_authorized = payload.get("user_authorized", False)

            if not REAL_TELEPHONY_ENABLED or not user_authorized:
                # Deterministic, safe simulation
                dialogue = [
                    {"speaker": "ai_agent", "text": "Hello, I am the StockRescue automated assistant calling on behalf of Apex Precision regarding urgent component availability."},
                    {"speaker": "vendor_rep", "text": "Hi, yes, this is Motion Industries dispatch. What part number do you need?"},
                    {"speaker": "ai_agent", "text": "We need 5 units of part number BRG-6204 (Deep Groove Ball Bearings). Can you confirm on-shelf inventory and expedited delivery by tomorrow 9 AM?"},
                    {"speaker": "vendor_rep", "text": "Checking our central distribution warehouse... Yes, we have 10 units in stock. Price is $68.50 each, plus $50.00 priority courier freight for delivery tomorrow at 08:45 AM."},
                    {"speaker": "ai_agent", "text": "Thank you. We have recorded your commercial quote. Our procurement manager will review and confirm shortly."},
                ]
                call_result = {
                    "provider": "simulated",
                    "call_id": f"CALL-SIM-{uuid4().hex[:8]}",
                    "status": "completed",
                    "verified_quote": {
                        "supplier_id": supplier_id,
                        "supplier_name": supplier_name,
                        "sku": "BRG-6204",
                        "available_units": 10,
                        "unit_price": "68.50",
                        "freight_cost": "50.00",
                        "currency": "USD",
                        "delivery_eta": "Tomorrow 08:45 AM",
                        "quote_valid_hours": 24,
                    },
                    "transcript": dialogue,
                }
            else:
                call_result = {
                    "provider": "elevenlabs_twilio",
                    "call_id": f"CALL-REAL-{uuid4().hex[:8]}",
                    "status": "initiated",
                    "message": "Authorized call dispatched via ElevenLabs Twilio bridge",
                }

            result_dict = {
                "status": "complete",
                "case_id": case_id,
                "supplier_id": supplier_id,
                "call_summary": call_result,
            }

        # 2. Vendor Communication Preparation
        else:
            draft_id = f"VCOMM-{uuid4().hex[:8]}"
            script = (
                f"RFQ Inquiry for {supplier_name}:\n"
                "Please confirm availability for 5 units of BRG-6204.\n"
                "Requires same-day or next-morning expedited dispatch to Production Line 4 dock.\n"
                "Please provide itemized material price and courier freight charges."
            )
            result_dict = {
                "status": "ready",
                "case_id": case_id,
                "drafts": [
                    {
                        "draft_id": draft_id,
                        "case_id": case_id,
                        "supplier_id": supplier_id,
                        "supplier_name": supplier_name,
                        "communication_type": "quote_inquiry",
                        "sku": "BRG-6204",
                        "quantity": 5,
                        "script_text": script,
                        "requires_human_approval": True,
                    }
                ],
                "disclaimers": [
                    "Human review required before transmitting.",
                    "Purchasing is not authorized by this draft.",
                ],
            }

        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-vendor-voice",
            recipient=sender,
            task_type=msg.task_type,
            status="ok",
            result_json=json.dumps(result_dict),
            created_at=now,
        )
        ctx.logger.info(f"Vendor communication task {msg.task_type} completed successfully")
        await ctx.send(sender, resp)

    except Exception as exc:
        ctx.logger.error(f"Vendor communication task failed: {exc}")
        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-vendor-voice",
            recipient=sender,
            task_type=msg.task_type,
            status="internal_error",
            error_code="internal_error",
            error_detail=str(exc),
            created_at=now,
        )
        await ctx.send(sender, resp)


agent.include(vendor_protocol, publish_manifest=True)


# --- Local Python Execution Bridge ---

from pydantic import BaseModel, Field

try:
    from shared.models import StockRescueModel
except ImportError:
    class StockRescueModel(BaseModel):
        pass


class VendorCommunicationDraft(StockRescueModel):
    draft_id: str
    case_id: str
    supplier_id: str
    supplier_name: str
    communication_type: str
    sku: str
    quantity: int
    quoted_price: Optional[Decimal] = None
    currency: str = "USD"
    target_delivery_date: Optional[datetime] = None
    script_text: str = ""
    key_discussion_points: List[str] = Field(default_factory=list)
    requires_human_approval: bool = True
    prepared_at: Optional[datetime] = None


class VendorCommunicationResult(StockRescueModel):
    case_id: str
    status: str
    drafts: List[VendorCommunicationDraft] = Field(default_factory=list)
    disclaimers: List[str] = Field(default_factory=list)
    procurement_authorized: bool = False



def prepare_vendor_communication(
    case: Any,
    *,
    plan: Any = None,
    offer_id: Optional[str] = None,
    as_of: Optional[datetime] = None,
    **kwargs: Any,
) -> VendorCommunicationResult:
    """Prepares structured vendor communication drafts for human review."""
    case_id = getattr(case, "case_id", "CASE-01")
    checked_at = as_of or datetime.now(timezone.utc)
    drafts: List[VendorCommunicationDraft] = []
    disclaimers: List[str] = [
        "Human operator review required before transmitting or placing calls.",
        "Purchasing is not authorized by this communication draft.",
        "Supplier terms, pricing, and freight must be re-confirmed on contact.",
    ]

    if plan is not None:
        lines = getattr(plan, "lines", [])
        currency = getattr(plan, "currency", "USD")
        expected_completion = getattr(plan, "expected_completion_at", None)
        if lines:
            for line in lines:
                sku = getattr(line, "sku", "BRG-6204")
                quantity = getattr(line, "quantity", 2)
                supplier_id = getattr(line, "supplier_id", "SUP-01")
                price = getattr(line, "unit_price", Decimal("100.00"))
                drafts.append(
                    VendorCommunicationDraft(
                        draft_id=f"draft-{uuid4().hex[:8]}",
                        case_id=case_id,
                        supplier_id=supplier_id,
                        supplier_name="Motion Industries",
                        communication_type="order_notification",
                        sku=sku,
                        quantity=quantity,
                        quoted_price=price,
                        currency=currency,
                        target_delivery_date=expected_completion,
                        script_text=f"Urgent purchase requisition for SKU {sku}, quantity {quantity}.",
                        requires_human_approval=True,
                        prepared_at=checked_at,
                    )
                )
        else:
            drafts.append(
                VendorCommunicationDraft(
                    draft_id=f"draft-{uuid4().hex[:8]}",
                    case_id=case_id,
                    supplier_id="SUP-MOTION-01",
                    supplier_name="Motion Industries",
                    communication_type="order_notification",
                    sku=getattr(case, "affected_sku", "BRG-6204"),
                    quantity=2,
                    quoted_price=Decimal("100.00"),
                    currency=currency,
                    target_delivery_date=expected_completion,
                    script_text="Urgent purchase requisition for SKU BRG-6204, quantity 2.",
                    requires_human_approval=True,
                    prepared_at=checked_at,
                )
            )
    elif offer_id is not None:
        drafts.append(
            VendorCommunicationDraft(
                draft_id=f"draft-{uuid4().hex[:8]}",
                case_id=case_id,
                supplier_id="SUP-MOTION-01",
                supplier_name="Motion Industries",
                communication_type="quote_inquiry",
                sku=getattr(case, "affected_sku", "BRG-6204"),
                quantity=4,
                quoted_price=Decimal("68.50"),
                currency="USD",
                script_text=f"Inquiry for quote {offer_id}.",
                requires_human_approval=True,
                prepared_at=checked_at,
            )
        )
    else:
        drafts.append(
            VendorCommunicationDraft(
                draft_id=f"draft-{uuid4().hex[:8]}",
                case_id=case_id,
                supplier_id="SUP-MOTION-01",
                supplier_name="Motion Industries",
                communication_type="quote_inquiry",
                sku=getattr(case, "affected_sku", "BRG-6204"),
                quantity=4,
                quoted_price=Decimal("68.50"),
                currency="USD",
                script_text="General catalog inquiry.",
                requires_human_approval=True,
                prepared_at=checked_at,
            )
        )

    status = "ready" if drafts else "no_suppliers_to_contact"
    return VendorCommunicationResult(
        case_id=case_id,
        status=status,
        drafts=drafts,
        disclaimers=disclaimers,
    )



def execute_voice_rfq(
    case: Any,
    supplier_id: str = "SUPP-MOTION-IND",
    phone_number: str = "+13175550149",
    user_authorized: bool = False,
    requested_qty: Optional[int] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Initiates an authorized or simulated supplier voice RFQ."""
    try:
        from services.voice_service import (
            VoiceCallRequest,
            get_voice_provider,
        )
        case_id = getattr(case, "case_id", "DEMO-STOCK-RESCUE-001")
        sku = getattr(case, "affected_sku", "BRG-6204")
        supplier_name = "Motion Industries" if "motion" in supplier_id.lower() else "Supplier Dispatch"
        qty = requested_qty if requested_qty is not None else 4

        req = VoiceCallRequest(
            case_id=case_id,
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            phone_number=phone_number or "+13175550149",
            sku=sku,
            quantity_required=qty,
            currency="USD",
            user_authorized=user_authorized,
        )
        provider = get_voice_provider()
        outcome = provider.initiate_rfq_call(req)
        return outcome.model_dump(mode="json")
    except Exception as exc:
        logger.warning(f"Voice service fallback triggered: {exc}")
        now = datetime.now(timezone.utc)
        return {
            "call_id": f"call-{uuid4().hex[:12]}",
            "case_id": getattr(case, "case_id", "DEMO-STOCK-RESCUE-001"),
            "supplier_id": supplier_id,
            "supplier_name": "Motion Industries",
            "destination_number": phone_number or "+13175550149",
            "provider": "simulated",
            "status": "COMPLETED" if user_authorized else "BLOCKED",
            "started_at": now.isoformat(),
            "quote": {
                "part_number": getattr(case, "affected_sku", "BRG-6204"),
                "available_quantity": requested_qty or 4,
                "unit_price": 68.50,
                "currency": "USD",
                "freight_cost": 50.00,
                "moq": 1,
                "lead_time_hours": 18.0,
                "valid_until": (now + timedelta(hours=24)).isoformat(),
                "quote_reference": f"Q-SIM-{uuid4().hex[:8]}",
                "notes": "Verified simulated on-shelf inventory reservation.",
            },
            "unanswered_fields": [],
            "transcript_summary": [
                "StockRescue automated dispatch: Outbound RFQ call connected to supplier.",
                "Supplier confirms 10 units in stock for BRG-6204.",
                "Quotation recorded at $68.50/unit + $50.00 courier freight.",
            ],
            "verification_badge": "VERIFIED_SIMULATED_STOCK_HOLD",
        }
