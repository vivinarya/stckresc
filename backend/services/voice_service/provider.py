"""Voice provider implementations for StockRescue supplier RFQ calls.

Provides:
- SimulatedSupplierProvider: Safe, deterministic, realistic test provider.
- ElevenLabsTwilioProvider: Official ElevenLabs Twilio Outbound Call integration.
"""

from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx

from services.voice_service.models import (
    ObtainedQuoteDetails,
    VoiceCallOutcome,
    VoiceCallRequest,
    VoiceCallStatus,
)
from shared.config import Settings, get_settings

logger = logging.getLogger(__name__)

E164_PATTERN = re.compile(r"^\+?[1-9]\d{1,14}$")


class BaseVoiceProvider(ABC):
    """Abstract base class for supplier voice outreach providers."""

    @abstractmethod
    def initiate_rfq_call(self, request: VoiceCallRequest) -> VoiceCallOutcome:
        """Initiates an RFQ call to a supplier and returns the outcome."""
        pass


class SimulatedSupplierProvider(BaseVoiceProvider):
    """Default deterministic voice call simulator for safe testing and verification."""

    def initiate_rfq_call(self, request: VoiceCallRequest) -> VoiceCallOutcome:
        logger.info(
            "Simulating supplier voice call for case=%s supplier=%s part=%s",
            request.case_id,
            request.supplier_name,
            request.sku,
        )
        now = datetime.now(timezone.utc)
        call_id = f"sim-call-{uuid4().hex[:10]}"

        # Deterministic simulation based on supplier identity
        is_motion = "motion" in request.supplier_name.lower() or "motion" in request.supplier_id.lower()
        is_fast = "fast" in request.supplier_name.lower() or "fast" in request.supplier_id.lower()

        if is_motion:
            available_qty = max(request.quantity_required, 18)
            unit_price = Decimal("42.50")
            freight = Decimal("180.00")
            lead_time = 4.5
            rep_name = "Dave Miller (Inside Sales Lead)"
            hold_ref = f"HOLD-{uuid4().hex[:6].upper()}"
        elif is_fast:
            available_qty = max(request.quantity_required, 25)
            unit_price = Decimal("35.00")
            freight = Decimal("350.00")
            lead_time = 3.0
            rep_name = "Sarah Jenkins (Senior Dispatch)"
            hold_ref = f"FAST-{uuid4().hex[:6].upper()}"
        else:
            available_qty = request.quantity_required
            unit_price = Decimal("48.00")
            freight = Decimal("120.00")
            lead_time = 6.0
            rep_name = "Alex Vance (Commercial Desk)"
            hold_ref = f"SR-HOLD-{uuid4().hex[:6].upper()}"

        valid_until = now + timedelta(hours=8)
        quote = ObtainedQuoteDetails(
            part_number=request.sku,
            available_quantity=available_qty,
            unit_price=unit_price,
            currency=request.currency,
            freight_cost=freight,
            moq=1,
            lead_time_hours=lead_time,
            valid_until=valid_until,
            quote_reference=hold_ref,
            notes=f"Confirmed with {rep_name}. Verbal stock hold active.",
        )

        transcript = [
            f"[00:05] StockRescue AI: 'Hello, this is StockRescue automated dispatch calling {request.supplier_name} regarding urgent procurement for case {request.case_id}. Is this an appropriate time to verify availability for part {request.sku}?'",
            f"[00:22] {rep_name}: 'Yes, I can check our inventory ledger right now. Looking up {request.sku}...'",
            f"[00:38] {rep_name}: 'We have {available_qty} units in stock. Unit price is {unit_price} {request.currency}, with expedited hot-shot courier available at {freight} {request.currency} for ~{lead_time} hours dispatch.'",
            f"[00:55] StockRescue AI: 'Thank you {rep_name}. Can you confirm a temporary verbal stock hold of {request.quantity_required} units under reference {hold_ref} while our plant operator approves the draft order?'",
            f"[01:10] {rep_name}: 'Confirmed. Tagged {request.quantity_required} units under {hold_ref}. Hold is good for 8 hours.'",
            f"[01:25] StockRescue AI: 'Excellent. The formal purchase order draft is being prepared. Thank you for your assistance. Goodbye.'",
        ]

        return VoiceCallOutcome(
            call_id=call_id,
            case_id=request.case_id,
            supplier_id=request.supplier_id,
            supplier_name=request.supplier_name,
            destination_number=request.phone_number,
            provider="simulated",
            status=VoiceCallStatus.COMPLETED,
            started_at=now,
            ended_at=now + timedelta(seconds=95),
            duration_seconds=95,
            quote=quote,
            unanswered_fields=[],
            transcript_summary=transcript,
            verification_badge="VERIFIED_SIMULATED_STOCK_HOLD",
        )


class ElevenLabsTwilioProvider(BaseVoiceProvider):
    """Official ElevenLabs Twilio Outbound Call Provider with strict safety controls."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def initiate_rfq_call(self, request: VoiceCallRequest) -> VoiceCallOutcome:
        call_id = f"el-call-{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        # 1. Gate: Real Telephony Enabled
        if not getattr(self.settings, "real_telephony_enabled", False):
            logger.warning("Voice outreach blocked: REAL_TELEPHONY_ENABLED is false")
            return VoiceCallOutcome(
                call_id=call_id,
                case_id=request.case_id,
                supplier_id=request.supplier_id,
                supplier_name=request.supplier_name,
                destination_number=request.phone_number,
                provider="elevenlabs_twilio",
                status=VoiceCallStatus.BLOCKED,
                started_at=now,
                error_message="Real telephony is disabled by policy (REAL_TELEPHONY_ENABLED=false). Use simulated provider.",
            )

        # 2. Gate: User Explicit Authorization
        if not request.user_authorized:
            logger.warning("Voice outreach blocked: user authorization not provided")
            return VoiceCallOutcome(
                call_id=call_id,
                case_id=request.case_id,
                supplier_id=request.supplier_id,
                supplier_name=request.supplier_name,
                destination_number=request.phone_number,
                provider="elevenlabs_twilio",
                status=VoiceCallStatus.BLOCKED,
                started_at=now,
                error_message="Voice outreach not authorized by plant operator. Explicit authorization required.",
            )

        # 3. Gate: Authorized Destination Number (Allowlist)
        allowed_destinations = getattr(self.settings, "get_voice_allowed_destinations", lambda: set())()
        clean_dest = re.sub(r"[\s\-\(\)]", "", request.phone_number)
        if allowed_destinations and clean_dest not in allowed_destinations:
            logger.warning("Voice outreach blocked: %s not in allowed destinations", clean_dest)
            return VoiceCallOutcome(
                call_id=call_id,
                case_id=request.case_id,
                supplier_id=request.supplier_id,
                supplier_name=request.supplier_name,
                destination_number=request.phone_number,
                provider="elevenlabs_twilio",
                status=VoiceCallStatus.BLOCKED,
                started_at=now,
                error_message=f"Destination number {request.phone_number} is not in the approved VOICE_ALLOWED_DESTINATIONS whitelist.",
            )

        # 4. Gate: Valid Credentials
        api_key = os.getenv("ELEVENLABS_API_KEY", "")
        agent_id = os.getenv("ELEVENLABS_AGENT_ID", "")
        agent_phone_number_id = os.getenv("ELEVENLABS_AGENT_PHONE_NUMBER_ID", "")

        if not api_key or not agent_id or not agent_phone_number_id:
            logger.error("Missing ElevenLabs configuration secrets")
            return VoiceCallOutcome(
                call_id=call_id,
                case_id=request.case_id,
                supplier_id=request.supplier_id,
                supplier_name=request.supplier_name,
                destination_number=request.phone_number,
                provider="elevenlabs_twilio",
                status=VoiceCallStatus.FAILED,
                started_at=now,
                error_message="ElevenLabs API credentials missing (ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID, or ELEVENLABS_AGENT_PHONE_NUMBER_ID).",
            )

        # 5. Execute Outbound Call via ElevenLabs API
        url = "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json",
        }
        prompt = (
            f"You are StockRescue's automated assistant calling {request.supplier_name} "
            f"to verify stock availability and pricing for part {request.sku} (quantity: {request.quantity_required}). "
            f"State the purpose of the call, ask for availability, lead time, unit price, and request a verbal hold."
        )
        payload = {
            "agent_id": agent_id,
            "agent_phone_number_id": agent_phone_number_id,
            "to_phone_number": clean_dest,
            "conversation_config_override": {
                "agent": {
                    "prompt": {"prompt": prompt},
                    "first_message": f"Hello, this is StockRescue automated dispatch calling {request.supplier_name} regarding urgent procurement of part {request.sku}.",
                }
            },
        }

        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.post(url, headers=headers, json=payload)
                if res.status_code in (200, 201):
                    data = res.json()
                    conv_id = data.get("conversation_id") or data.get("call_id") or call_id
                    return VoiceCallOutcome(
                        call_id=conv_id,
                        case_id=request.case_id,
                        supplier_id=request.supplier_id,
                        supplier_name=request.supplier_name,
                        destination_number=request.phone_number,
                        provider="elevenlabs_twilio",
                        status=VoiceCallStatus.INITIATED,
                        started_at=now,
                        transcript_summary=[f"Outbound call initiated successfully via ElevenLabs/Twilio. Conversation ID: {conv_id}"],
                    )
                else:
                    return VoiceCallOutcome(
                        call_id=call_id,
                        case_id=request.case_id,
                        supplier_id=request.supplier_id,
                        supplier_name=request.supplier_name,
                        destination_number=request.phone_number,
                        provider="elevenlabs_twilio",
                        status=VoiceCallStatus.FAILED,
                        started_at=now,
                        error_message=f"ElevenLabs API error HTTP {res.status_code}: {res.text[:200]}",
                    )
        except Exception as exc:
            logger.error("ElevenLabs API outbound call request failed: %s", exc)
            return VoiceCallOutcome(
                call_id=call_id,
                case_id=request.case_id,
                supplier_id=request.supplier_id,
                supplier_name=request.supplier_name,
                destination_number=request.phone_number,
                provider="elevenlabs_twilio",
                status=VoiceCallStatus.FAILED,
                started_at=now,
                error_message=f"Network exception calling ElevenLabs API: {exc}",
            )


def get_voice_provider(settings: Settings | None = None) -> BaseVoiceProvider:
    """Returns the configured voice outreach provider."""
    s = settings or get_settings()
    provider_type = getattr(s, "voice_provider", "simulated").strip().lower()
    if provider_type in ("elevenlabs", "twilio", "elevenlabs_twilio"):
        return ElevenLabsTwilioProvider(s)
    return SimulatedSupplierProvider()
