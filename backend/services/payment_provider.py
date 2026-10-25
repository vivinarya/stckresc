"""Payment provider verification interface and Skyfire client implementation."""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any, Dict, Optional, Protocol, Tuple

logger = logging.getLogger(__name__)


class PaymentVerificationError(Exception):
    pass


class PaymentProvider(Protocol):
    def verify_payment(
        self,
        transaction_id: str,
        expected_amount: Decimal,
        expected_currency: str,
        customer_address: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Returns (success, provider_reference, verification_details)."""
        ...


class MockPaymentProvider:
    """Mock payment provider for local development and automated testing."""

    def __init__(self, should_succeed: bool = True):
        self.should_succeed = should_succeed
        self.verified_transactions: dict[str, dict[str, Any]] = {}

    def verify_payment(
        self,
        transaction_id: str,
        expected_amount: Decimal,
        expected_currency: str,
        customer_address: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        if not self.should_succeed:
            return False, "", {"error": "Mock verification rejection"}

        # Idempotent receipt
        provider_ref = f"SKYFIRE-MOCK-{transaction_id[-8:]}"
        details = {
            "status": "confirmed",
            "provider": "skyfire_mock",
            "charged_amount": str(expected_amount),
            "currency": expected_currency,
            "payer": customer_address,
            "provider_ref": provider_ref,
        }
        self.verified_transactions[transaction_id] = details
        return True, provider_ref, details


class SkyfirePaymentProvider:
    """Production Skyfire USDC verification client using Agentverse Agent Secrets."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        service_id: Optional[str] = None,
        account_id: Optional[str] = None,
        env: str = "sandbox",
    ):
        self.api_key = api_key or os.getenv("SELLER_SKYFIRE_API_KEY", "")
        self.service_id = service_id or os.getenv("SELLER_SERVICE_ID", "")
        self.account_id = account_id or os.getenv("SELLER_ACCOUNT_ID", "")
        self.env = env or os.getenv("SKYFIRE_ENV", "sandbox")
        self.tokens_api_url = os.getenv(
            "SKYFIRE_TOKENS_API_URL",
            "https://api.skyfire.xyz/v1" if self.env == "production" else "https://api-sandbox.skyfire.xyz/v1",
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.service_id)

    def verify_payment(
        self,
        transaction_id: str,
        expected_amount: Decimal,
        expected_currency: str,
        customer_address: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        if not self.is_configured:
            logger.warning(
                "Skyfire credentials not configured (SELLER_SKYFIRE_API_KEY / SELLER_SERVICE_ID). Rejecting live charge."
            )
            return False, "", {"error": "Skyfire provider credentials not configured"}

        # In production, call Skyfire /tokens or /charges verification endpoint
        import urllib.request
        import json

        endpoint = f"{self.tokens_api_url}/charges/verify"
        req_payload = {
            "service_id": self.service_id,
            "transaction_id": transaction_id,
            "expected_amount": float(expected_amount),
            "currency": expected_currency,
            "payer_address": customer_address,
        }

        try:
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(req_payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                verified = data.get("verified", False)
                provider_ref = data.get("provider_reference", f"SKY-{transaction_id[:12]}")
                return verified, provider_ref, data
        except Exception as exc:
            logger.error("Skyfire verification request failed: %s", exc)
            return False, "", {"error": str(exc)}


def get_payment_provider() -> PaymentProvider:
    """Factory selecting live Skyfire provider if configured, or mock fallback for dev/test."""
    provider = SkyfirePaymentProvider()
    if provider.is_configured:
        return provider
    return MockPaymentProvider(should_succeed=True)
