"""Data models for StockRescue Payment Protocol and Premium Recovery Report entitlements."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import Field

from shared.models import StockRescueModel, utc_now


class PaymentStatus(str, Enum):
    PENDING = "pending"
    COMMITTED = "committed"
    VERIFIED = "verified"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PaymentRecord(StockRescueModel):
    reference: str = Field(
        default_factory=lambda: f"PAY-{uuid4().hex[:12].upper()}",
        min_length=1,
        max_length=100,
    )
    case_id: str = Field(min_length=1, max_length=100)
    customer_address: str = Field(min_length=1, max_length=150)
    amount: Decimal = Field(default=Decimal("0.50"), ge=Decimal("0.01"))
    currency: str = Field(default="USDC", min_length=2, max_length=10)
    payment_method: str = Field(default="skyfire", min_length=2, max_length=50)
    status: PaymentStatus = Field(default=PaymentStatus.PENDING)
    transaction_id: Optional[str] = Field(default=None, max_length=200)
    provider_reference: Optional[str] = Field(default=None, max_length=200)
    description: str = Field(default="StockRescue Premium Multi-Supplier Recovery Report", max_length=300)
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime = Field(...)
    verified_at: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PremiumReportEntitlement(StockRescueModel):
    entitlement_id: str = Field(
        default_factory=lambda: f"ENT-{uuid4().hex[:12].upper()}",
        min_length=1,
        max_length=100,
    )
    case_id: str = Field(min_length=1, max_length=100)
    customer_address: str = Field(min_length=1, max_length=150)
    payment_reference: str = Field(min_length=1, max_length=100)
    provider_transaction_id: str = Field(min_length=1, max_length=200)
    granted_at: datetime = Field(default_factory=utc_now)
    expires_at: Optional[datetime] = None
    report_fingerprint: str = Field(default="", max_length=128)
