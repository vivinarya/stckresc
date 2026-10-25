"""Unit tests for StockRescue monetization, Payment Protocol, and Premium Report entitlements."""

from decimal import Decimal
from datetime import datetime, timezone, timedelta
import pytest

from services.warehouse_api.repository import SQLiteWarehouseRepository
from services.payment_provider import MockPaymentProvider, SkyfirePaymentProvider
from shared.payment_models import PaymentRecord, PaymentStatus, PremiumReportEntitlement
from agents.coordinator.card_protocol import build_payment_request_card, build_premium_report_card
from agents.coordinator.card_workflow import handle_conversational_step
from agents.protocols.transport import SpecialistTransport
from helpers import make_case


@pytest.fixture
def repo(tmp_path):
    r = SQLiteWarehouseRepository(tmp_path / "payment_test.db")
    r.initialize()
    case = make_case(case_id="DEMO-STOCK-RESCUE-001")
    r.create_case(case)
    return r


@pytest.fixture
def local_transport():
    return SpecialistTransport(mode="local")


def test_payment_cards_structure():
    card = build_payment_request_card("DEMO-001", "PAY-REF-123", amount="0.50", currency="USDC")
    assert card.type == "metadata"
    assert card.metadata["card_kind"] == "detail"
    assert "0.50" in card.metadata["card_payload"]
    assert "USDC" in card.metadata["card_payload"]

    rep_card = build_premium_report_card(
        case_id="DEMO-001",
        entitlement_id="ENT-123",
        provider_ref="SKY-REF-999",
        sku="BRG-6204",
        plans_summary="Split Fast Delivery",
        risk_summary="Low Risk",
    )
    assert rep_card.metadata["card_kind"] == "detail"
    assert "PAID & VERIFIED" in rep_card.metadata["card_payload"]


def test_mock_payment_provider_verification():
    mock_p = MockPaymentProvider(should_succeed=True)
    ok, ref, details = mock_p.verify_payment(
        transaction_id="TX-TEST-001",
        expected_amount=Decimal("0.50"),
        expected_currency="USDC",
        customer_address="agent1qcustomer",
    )
    assert ok is True
    assert "SKYFIRE-MOCK" in ref
    assert details["payer"] == "agent1qcustomer"


def test_payment_entitlement_roundtrip(repo):
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    pay = PaymentRecord(
        reference="PAY-001",
        case_id="DEMO-STOCK-RESCUE-001",
        customer_address="agent1qcustomer",
        amount=Decimal("0.50"),
        currency="USDC",
        payment_method="skyfire",
        expires_at=expires_at,
    )
    repo.save_payment(pay)

    loaded = repo.get_payment("PAY-001")
    assert loaded.reference == "PAY-001"
    assert loaded.amount == Decimal("0.50")

    ent = PremiumReportEntitlement(
        entitlement_id="ENT-001",
        case_id="DEMO-STOCK-RESCUE-001",
        customer_address="agent1qcustomer",
        payment_reference="PAY-001",
        provider_transaction_id="SKY-TX-999",
    )
    repo.grant_entitlement(ent)

    loaded_ent = repo.get_entitlement("DEMO-STOCK-RESCUE-001", "agent1qcustomer")
    assert loaded_ent is not None
    assert loaded_ent.entitlement_id == "ENT-001"


def test_conversational_monetization_flow(repo, local_transport):
    # Step 1: Request premium report
    t1, c1 = handle_conversational_step("request premium report", repo, local_transport)
    assert "0.50 USDC" in t1
    assert c1.metadata["card_kind"] == "detail"

    # Step 2: Commit payment
    t2, c2 = handle_conversational_step(
        '{"action": "commit_payment", "case_id": "DEMO-STOCK-RESCUE-001", "sender_address": "agent1qtest"}',
        repo,
        local_transport,
    )
    assert "PAID & VERIFIED" in t2
    assert c2.metadata["card_kind"] == "detail"

    # Step 3: Idempotent repeat request returns unlocked report without recharge
    t3, c3 = handle_conversational_step(
        '{"action": "commit_payment", "case_id": "DEMO-STOCK-RESCUE-001", "sender_address": "agent1qtest"}',
        repo,
        local_transport,
    )
    assert "Displaying your unlocked Premium Report" in t3


@pytest.mark.anyio
async def test_agent_payment_protocol_seller_workflow(repo):
    from agents.coordinator.payment_protocol import (
        create_payment_request,
        build_coordinator_payment_protocol,
    )
    from uagents_core.contrib.protocols.payment import CommitPayment, Funds, CompletePayment, RejectPayment
    from unittest.mock import AsyncMock, MagicMock

    # 1. Create typed payment request
    req, ref = create_payment_request(
        recipient="agent1qcoordinator",
        case_id="DEMO-STOCK-RESCUE-001",
        amount=Decimal("0.50"),
        currency="USDC",
    )
    assert req.recipient == "agent1qcoordinator"
    assert float(req.accepted_funds[0].amount) == 0.50
    assert req.accepted_funds[0].currency == "USDC"
    assert req.reference == ref

    # Save pending record
    pay_record = PaymentRecord(
        reference=ref,
        case_id="DEMO-STOCK-RESCUE-001",
        customer_address="agent1qbuyer",
        amount=Decimal("0.50"),
        currency="USDC",
        payment_method="skyfire",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    repo.save_payment(pay_record)

    proto = build_coordinator_payment_protocol(repo, "agent1qcoordinator")
    from uagents import Model
    digest = Model.build_schema_digest(CommitPayment)
    handler = proto.signed_message_handlers[digest]

    # 2. Simulate valid buyer CommitPayment
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.logger = MagicMock()

    commit_msg = CommitPayment(
        funds=Funds(amount=0.50, currency="USDC", payment_method="skyfire"),
        recipient="agent1qcoordinator",
        transaction_id="TX-VALID-123",
        reference=ref,
        description="Payment for Premium Report",
        metadata={"case_id": "DEMO-STOCK-RESCUE-001"},
    )

    await handler(ctx, "agent1qbuyer", commit_msg)

    # Verify CompletePayment sent
    assert ctx.send.called
    sent_msg = ctx.send.call_args[0][1]
    assert isinstance(sent_msg, CompletePayment)
    assert sent_msg.transaction_id == "TX-VALID-123"

    # Verify entitlement persisted
    ent = repo.get_entitlement("DEMO-STOCK-RESCUE-001", "agent1qbuyer")
    assert ent is not None
    assert ent.payment_reference == ref
