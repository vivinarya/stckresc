from __future__ import annotations
from datetime import datetime
import re
from decimal import Decimal
from typing import Literal
from pydantic import Field, model_validator
from shared.models import (
    AuditEvent,
    CompatibilityAssessment,
    DeliveryEvent,
    DeliveryState,
    DisruptionCase,
    InventoryRecord,
    JobRequirement,
    ProcurementApproval,
    RecoveryPlan,
    StockRescueModel,
    SupplierOffer,
)

class CaseCreateRequest(StockRescueModel):
    affected_sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9._/-]{0,63}$")
    failed_supplier_id: str = Field(min_length=1, max_length=100)
    delayed_until: datetime | None = None
    budget: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    jobs: list[JobRequirement] = Field(default_factory=list, max_length=200)
    inventory: list[InventoryRecord] = Field(default_factory=list, max_length=1000)
    offers: list[SupplierOffer] = Field(default_factory=list, max_length=500)
    compatibility: list[CompatibilityAssessment] = Field(default_factory=list, max_length=1000)
    @model_validator(mode="after")
    def reject_path_like_skus(self) -> "CaseCreateRequest":
        skus = [self.affected_sku]
        skus.extend(item.sku for item in self.jobs)
        skus.extend(item.sku for item in self.inventory)
        skus.extend(item.sku for item in self.offers)
        skus.extend(item.requested_sku for item in self.compatibility)
        skus.extend(item.candidate_sku for item in self.compatibility)
        if any(re.search(r"(?:^|/)\.\.(?:/|$)", sku, flags=re.ASCII) for sku in skus):
            raise ValueError("SKU contains a prohibited path segment")
        return self

    def to_case(self) -> DisruptionCase:
        return DisruptionCase.model_validate(
            self.model_dump(exclude={"inventory": {"__all__": {"available_quantity"}}})
        )
class CaseListResponse(StockRescueModel):
    items: list[DisruptionCase] = Field(max_length=100)
    count: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=1_000_000)

class PlanListResponse(StockRescueModel):
    items: list[RecoveryPlan] = Field(max_length=100)
    count: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=1_000_000)

class ApprovalListResponse(StockRescueModel):
    items: list[ProcurementApproval] = Field(max_length=100)
    count: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=1_000_000)

class HealthResponse(StockRescueModel):
    status: Literal["ok"] = "ok"
    app_name: str = Field(min_length=1, max_length=100)
    demo_mode: bool
    real_purchasing_enabled: Literal[False] = False

class MessageResponse(StockRescueModel):
    message: str = Field(min_length=1, max_length=200)

class ApprovalCreateRequest(StockRescueModel):
    max_total_cost: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="INR", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    expires_at: datetime

    @model_validator(mode="after")
    def validate_expires_at_timezone(self) -> "ApprovalCreateRequest":
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("expires_at must include a timezone offset")
        return self

class ApprovalRevokeRequest(StockRescueModel):
    revocation_reason: str = Field(min_length=1, max_length=300)

class AuditEventListResponse(StockRescueModel):
    items: list[AuditEvent] = Field(max_length=100)
    count: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=1_000_000)

class DeliveryEventCreateRequest(StockRescueModel):
    event_id: str | None = Field(default=None, max_length=100)
    status: DeliveryState
    occurred_at: datetime
    source: Literal["carrier_api", "authorized_operator", "simulator"]
    reference: str | None = Field(default=None, max_length=100)
    details: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def validate_occurred_at_timezone(self) -> "DeliveryEventCreateRequest":
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone offset")
        return self


class DeliveryEventListResponse(StockRescueModel):
    items: list[DeliveryEvent] = Field(max_length=100)
    count: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=1_000_000)
