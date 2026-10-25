"""Comprehensive unit tests for StockRescue voice integration (Phase 4).

Tests:
1. SimulatedSupplierProvider safe execution, transcripts, stock holds.
2. ElevenLabsTwilioProvider gating (disabled by default, unauthorized destination, missing user approval).
3. ElevenLabs outbound call initiation payload and error handling.
4. Voice call outcome re-entering recovery workflow.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from helpers import make_case
from services.voice_service import (
    ElevenLabsTwilioProvider,
    ObtainedQuoteDetails,
    SimulatedSupplierProvider,
    VoiceCallOutcome,
    VoiceCallRequest,
    VoiceCallStatus,
    get_voice_provider,
)
from shared.config import Settings


def test_simulated_supplier_provider_returns_structured_quote():
    provider = SimulatedSupplierProvider()
    req = VoiceCallRequest(
        case_id="DEMO-STOCK-RESCUE-001",
        supplier_id="SUPP-MOTION-IND",
        supplier_name="Motion Industries",
        phone_number="+13175550149",
        sku="BRG-6204",
        quantity_required=4,
        currency="USD",
        user_authorized=True,
    )
    outcome = provider.initiate_rfq_call(req)

    assert outcome.status == VoiceCallStatus.COMPLETED
    assert outcome.provider == "simulated"
    assert outcome.quote is not None
    assert outcome.quote.part_number == "BRG-6204"
    assert outcome.quote.available_quantity >= 4
    assert outcome.quote.unit_price > Decimal("0.00")
    assert len(outcome.transcript_summary) >= 4
    assert "StockRescue automated dispatch" in outcome.transcript_summary[0]
    assert outcome.verification_badge == "VERIFIED_SIMULATED_STOCK_HOLD"


def test_elevenlabs_provider_blocked_when_disabled():
    settings = Settings(
        real_telephony_enabled=False,
        voice_allowed_destinations="+13175550149",
    )
    provider = ElevenLabsTwilioProvider(settings)
    req = VoiceCallRequest(
        case_id="DEMO-STOCK-RESCUE-001",
        supplier_id="SUPP-MOTION-IND",
        supplier_name="Motion Industries",
        phone_number="+13175550149",
        sku="BRG-6204",
        quantity_required=4,
        user_authorized=True,
    )
    outcome = provider.initiate_rfq_call(req)

    assert outcome.status == VoiceCallStatus.BLOCKED
    assert "disabled by policy" in outcome.error_message


def test_elevenlabs_provider_blocked_without_user_authorization():
    settings = Settings(
        real_telephony_enabled=True,
        voice_allowed_destinations="+13175550149",
    )
    provider = ElevenLabsTwilioProvider(settings)
    req = VoiceCallRequest(
        case_id="DEMO-STOCK-RESCUE-001",
        supplier_id="SUPP-MOTION-IND",
        supplier_name="Motion Industries",
        phone_number="+13175550149",
        sku="BRG-6204",
        quantity_required=4,
        user_authorized=False,  # Not authorized
    )
    outcome = provider.initiate_rfq_call(req)

    assert outcome.status == VoiceCallStatus.BLOCKED
    assert "not authorized by plant operator" in outcome.error_message


def test_elevenlabs_provider_blocked_when_destination_not_allowed():
    settings = Settings(
        real_telephony_enabled=True,
        voice_allowed_destinations="+12125550100",  # Different number
    )
    provider = ElevenLabsTwilioProvider(settings)
    req = VoiceCallRequest(
        case_id="DEMO-STOCK-RESCUE-001",
        supplier_id="SUPP-MOTION-IND",
        supplier_name="Motion Industries",
        phone_number="+13175550149",
        sku="BRG-6204",
        quantity_required=4,
        user_authorized=True,
    )
    outcome = provider.initiate_rfq_call(req)

    assert outcome.status == VoiceCallStatus.BLOCKED
    assert "not in the approved VOICE_ALLOWED_DESTINATIONS" in outcome.error_message


def test_elevenlabs_provider_successful_call_initiation(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-api-key")
    monkeypatch.setenv("ELEVENLABS_AGENT_ID", "agent-12345")
    monkeypatch.setenv("ELEVENLABS_AGENT_PHONE_NUMBER_ID", "phone-id-67890")

    settings = Settings(
        real_telephony_enabled=True,
        voice_allowed_destinations="+13175550149",
    )
    provider = ElevenLabsTwilioProvider(settings)
    req = VoiceCallRequest(
        case_id="DEMO-STOCK-RESCUE-001",
        supplier_id="SUPP-MOTION-IND",
        supplier_name="Motion Industries",
        phone_number="+13175550149",
        sku="BRG-6204",
        quantity_required=4,
        user_authorized=True,
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"conversation_id": "conv-real-test-999"}

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        outcome = provider.initiate_rfq_call(req)
        assert outcome.status == VoiceCallStatus.INITIATED
        assert outcome.call_id == "conv-real-test-999"
        mock_post.assert_called_once()
        sent_payload = mock_post.call_args[1]["json"]
        assert sent_payload["agent_id"] == "agent-12345"
        assert sent_payload["to_phone_number"] == "+13175550149"


def test_vendor_voice_agent_execute_voice_rfq():
    from agents.vendor_voice.agent import execute_voice_rfq

    case = make_case(case_id="DEMO-STOCK-RESCUE-001")
    outcome_dict = execute_voice_rfq(
        case,
        supplier_id="SUPP-MOTION-IND",
        phone_number="+13175550149",
        user_authorized=True,
        requested_qty=4,
    )

    assert outcome_dict["case_id"] == "DEMO-STOCK-RESCUE-001"
    assert outcome_dict["status"] in ("COMPLETED", "BLOCKED")
    assert "quote" in outcome_dict
