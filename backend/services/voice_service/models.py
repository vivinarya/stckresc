"""Domain models and schemas for StockRescue supplier voice integration."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, List, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VoiceCallStatus(str, Enum):
    PLANNED = "PLANNED"
    INITIATED = "INITIATED"
    CONNECTED = "CONNECTED"
    COMPLETED = "COMPLETED"
    UNANSWERED = "UNANSWERED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class ObtainedQuoteDetails(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    part_number: str = Field(min_length=1, max_length=64)
    available_quantity: int = Field(ge=0)
    unit_price: Decimal = Field(gt=Decimal("0.00"), decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    freight_cost: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"), decimal_places=2)
    moq: int = Field(default=1, ge=1)
    lead_time_hours: float = Field(ge=0.0)
    valid_until: datetime
    quote_reference: str = Field(min_length=1, max_length=64)
    notes: str = Field(default="", max_length=500)

    @field_validator("valid_until")
    @classmethod
    def validate_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return v


class VoiceCallRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    case_id: str = Field(min_length=1, max_length=100)
    supplier_id: str = Field(min_length=1, max_length=100)
    supplier_name: str = Field(min_length=1, max_length=200)
    phone_number: str = Field(min_length=7, max_length=30)
    sku: str = Field(min_length=1, max_length=64)
    quantity_required: int = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    user_authorized: bool = Field(default=False)
    business_purpose: str = Field(
        default="Emergency component stockout quotation",
        max_length=200,
    )


class VoiceCallOutcome(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    call_id: str = Field(default_factory=lambda: f"call-{uuid4().hex[:12]}")
    case_id: str
    supplier_id: str
    supplier_name: str
    destination_number: str
    provider: str
    status: VoiceCallStatus
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: Optional[datetime] = None
    duration_seconds: int = 0
    quote: Optional[ObtainedQuoteDetails] = None
    unanswered_fields: List[str] = Field(default_factory=list)
    transcript_summary: List[str] = Field(default_factory=list)
    error_message: Optional[str] = None
    verification_badge: Optional[str] = None
