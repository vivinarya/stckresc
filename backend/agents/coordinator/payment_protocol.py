"""Fetch.ai Agent Payment Protocol implementation for StockRescue Coordinator (Seller Role).

Implements:
- ProtocolSpecification: payment_protocol_spec (role='seller')
- Seller models: RequestPayment, CommitPayment, RejectPayment, CancelPayment, CompletePayment, Funds
- Provider-verified monetization workflow (Skyfire/Mock) for StockRescue Premium Recovery Report (0.50 USDC).
- Persistent transaction state, expiry, idempotency, and anti-replay protection.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

from services.payment_provider import get_payment_provider
from services.warehouse_api.repository import WarehouseRepository
from shared.payment_models import PaymentRecord, PaymentStatus, PremiumReportEntitlement

logger = logging.getLogger(__name__)

DEFAULT_REPORT_PRICE = Decimal("0.50")
DEFAULT_REPORT_CURRENCY = "USDC"
DEFAULT_PAYMENT_METHOD = "skyfire"
DEFAULT_DEADLINE_SECONDS = 900  # 15 minutes


def create_payment_request(
    recipient: str,
    case_id: str,
    amount: Decimal = DEFAULT_REPORT_PRICE,
    currency: str = DEFAULT_REPORT_CURRENCY,
    payment_method: str = DEFAULT_PAYMENT_METHOD,
    deadline_seconds: int = DEFAULT_DEADLINE_SECONDS,
    description: str = "StockRescue Premium Recovery Report",
) -> tuple[RequestPayment, str]:
    """Builds a typed RequestPayment message and returns (request_msg, unique_reference)."""
    reference = f"PAY-{uuid4().hex[:12].upper()}"
    funds = Funds(
        amount=float(amount),
        currency=currency,
        payment_method=payment_method,
    )
    request_msg = RequestPayment(
        accepted_funds=[funds],
        recipient=recipient,
        deadline_seconds=deadline_seconds,
        reference=reference,
        description=description,
        metadata={"case_id": case_id},
    )
    return request_msg, reference


def build_coordinator_payment_protocol(
    repository: WarehouseRepository,
    coordinator_address: str,
) -> Protocol:
    """Builds and returns the Seller payment protocol attached to the Coordinator."""
    protocol = Protocol(
        name="StockRescuePaymentSeller",
        version="0.1.0",
        spec=payment_protocol_spec,
        role="seller",
    )

    @protocol.on_message(CommitPayment)
    async def handle_commit_payment(
        ctx: Context,
        sender: str,
        message: CommitPayment,
    ):
        """Processes buyer's CommitPayment, executes real provider verification, and completes or rejects."""
        ref = message.reference
        tx_id = message.transaction_id
        ctx.logger.info(
            "Received CommitPayment from buyer=%s ref=%s tx_id=%s",
            sender,
            ref,
            tx_id,
        )

        # 1. Lookup or reconstruct pending payment record
        try:
            payment = repository.get_payment(ref)
        except Exception:
            payment = None

        case_id = (message.metadata or {}).get("case_id", "DEMO-STOCK-RESCUE-001")
        if payment is not None:
            case_id = payment.case_id

        # 2. Check for duplicate already-completed transaction (anti-replay)
        if payment and payment.status == PaymentStatus.COMPLETED:
            ctx.logger.warning("Duplicate CommitPayment for completed ref=%s", ref)
            await ctx.send(sender, CompletePayment(transaction_id=tx_id))
            return

        # 3. Check expiry
        now = datetime.now(timezone.utc)
        if payment and payment.expires_at and payment.expires_at < now:
            ctx.logger.error("CommitPayment arrived after expiry: ref=%s", ref)
            payment.status = PaymentStatus.EXPIRED
            repository.save_payment(payment)
            await ctx.send(
                sender,
                RejectPayment(reason=f"Payment reference {ref} expired. Request a new quote."),
            )
            return

        # 4. Genuine Provider Verification (Skyfire / Mock)
        # Never unlock report on buyer assertion alone. Provider verification is mandatory.
        provider = get_payment_provider()
        funds = message.funds
        commit_amount = Decimal(str(funds.amount)) if funds else DEFAULT_REPORT_PRICE
        commit_curr = funds.currency if funds else DEFAULT_REPORT_CURRENCY

        verified, provider_ref, details = provider.verify_payment(
            transaction_id=tx_id,
            expected_amount=commit_amount,
            expected_currency=commit_curr,
            customer_address=sender,
        )

        if not verified:
            ctx.logger.error(
                "Payment provider verification failed for tx_id=%s ref=%s: %s",
                tx_id,
                ref,
                details.get("error"),
            )
            if payment:
                payment.status = PaymentStatus.REJECTED
                repository.save_payment(payment)
            await ctx.send(
                sender,
                RejectPayment(reason=f"Provider verification rejected: {details.get('error', 'unverified')}"),
            )
            return

        # 5. Success path: update payment record, record entitlement, send CompletePayment
        if payment is None:
            payment = PaymentRecord(
                reference=ref,
                case_id=case_id,
                customer_address=sender,
                amount=commit_amount,
                currency=commit_curr,
                payment_method=funds.payment_method if funds else DEFAULT_PAYMENT_METHOD,
                expires_at=now + timedelta(minutes=15),
            )

        payment.status = PaymentStatus.COMPLETED
        payment.transaction_id = tx_id
        payment.provider_reference = provider_ref
        repository.save_payment(payment)

        # Grant persistent entitlement
        entitlement = PremiumReportEntitlement(
            case_id=case_id,
            customer_address=sender,
            payment_reference=ref,
            provider_transaction_id=provider_ref,
        )
        repository.grant_entitlement(entitlement)

        ctx.logger.info(
            "Payment verified and completed: ref=%s provider_ref=%s. Entitlement granted for case=%s",
            ref,
            provider_ref,
            case_id,
        )

        await ctx.send(sender, CompletePayment(transaction_id=tx_id))

    return protocol
