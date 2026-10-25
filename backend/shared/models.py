"""validated data contracts for StockRescue."""

from __future__ import annotations
import hashlib
import json
import math
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    computed_field,
    field_validator,
    model_validator,
)



MAX_QUANTITY = 1_000_000
MAX_LIST_ITEMS = 1_000
MAX_AGENT_PAYLOAD_BYTES = 16 * 1024
MAX_AGENT_PAYLOAD_NODES = 512
MAX_AGENT_PAYLOAD_DEPTH = 8
MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991
SKU_FIELDS = {"sku", "affected_sku", "requested_sku", "candidate_sku"}
CURRENCY_FIELDS = {"currency"}
IDENTIFIER_FIELDS = {
    "job_id",
    "supplier_id",
    "offer_id",
    "case_id",
    "plan_id",
    "approval_id",
    "workflow_id",
    "trace_id",
    "message_id",
    "sender",
    "recipient",
    "assessed_by",
    "approved_by",
    "supplier_name",
    "location",
    "org_id",
    "profile_id",
    "membership_id",
    "user_address",
}
MONEY_FIELDS = {
    "unit_cost",
    "unit_price",
    "freight_cost",
    "budget",
    "total_cost",
    "max_total_cost",
}

EvidenceText = Annotated[str, Field(min_length=1, max_length=500)]

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def _check_json_value(value: Any, *, depth: int, counter: list[int]) -> None:
    counter[0] += 1
    if counter[0] > MAX_AGENT_PAYLOAD_NODES:
        raise ValueError("payload contains too many values")
    if depth > MAX_AGENT_PAYLOAD_DEPTH:
        raise ValueError("payload nesting is too deep")

    if value is None or isinstance(value, bool):
        return

    if isinstance(value, int):
        if abs(value) > MAX_SAFE_JSON_INTEGER:
            raise ValueError("payload integer exceeds the safe range")
        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("payload numbers must be finite")
        if abs(value) > 1e15:
            raise ValueError("payload number exceeds the allowed range")
        return

    if isinstance(value, str):
        if len(value) > 4_096:
            raise ValueError("payload string is too long")
        if any(
            unicodedata.category(char) == "Cc" and char not in "\n\r\t"
            for char in value
        ):
            raise ValueError("payload string contains control characters")
        return

    if isinstance(value, list):
        if len(value) > 100:
            raise ValueError("payload array contains too many items")
        for item in value:
            _check_json_value(item, depth=depth + 1, counter=counter)
        return

    if isinstance(value, dict):
        if len(value) > 100:
            raise ValueError("payload object contains too many keys")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("payload object keys must be strings")
            if not key or len(key) > 128 or not key.isprintable():
                raise ValueError("payload object key is invalid")
            _check_json_value(item, depth=depth + 1, counter=counter)
        return

    raise ValueError("payload must contain only JSON-compatible values")

class StockRescueModel(BaseModel):
    #validation and safe defaults for shared models.
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        validate_default=True,
    )
    @field_validator("*", mode="before")
    @classmethod
    def harden_string_and_money_inputs(
        cls, value: Any, info: ValidationInfo
    ) -> Any:
        field_name = info.field_name
        if field_name in MONEY_FIELDS and isinstance(value, float):
            raise ValueError(
                "financial amounts must be Decimal, integer, or decimal string values; floats are not accepted"
            )

        if isinstance(value, str):
            if field_name in SKU_FIELDS:
                value = value.strip().upper()
            elif field_name in CURRENCY_FIELDS:
                value = value.strip().upper()

            if field_name in IDENTIFIER_FIELDS:
                value = value.strip()
                if not value or not value.isprintable():
                    raise ValueError("identifier must be non-empty printable text")
            else:
                if any(
                    unicodedata.category(char) == "Cc"
                    and char not in "\n\r\t"
                    for char in value
                ):
                    raise ValueError("text contains disallowed control characters")
        return value

    @field_validator("*")
    @classmethod
    def require_aware_datetimes_and_finite_decimals(
        cls, value: Any, info: ValidationInfo
    ) -> Any:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("datetime must include a timezone")
        if isinstance(value, Decimal) and not value.is_finite():
            raise ValueError("financial amounts must be finite")
        return value

#compatibility status
class CompatibilityStatus(str, Enum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"

#recovery plan status
class RecoveryPlanStatus(str, Enum):
    PROPOSED = "proposed"
    NO_FEASIBLE_PLAN = "no_feasible_plan"
    BLOCKED = "blocked"

#workflow status
class WorkflowStatus(str, Enum):
    CREATED = "created"
    INVESTIGATING = "investigating"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PROCUREMENT_SUBMITTED = "procurement_submitted"
    IN_TRANSIT = "in_transit"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"

#inventory record
class InventoryRecord(StockRescueModel):
    sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9._/-]{0,63}$")
    description: str = Field(default="", max_length=200)
    quantity_on_hand: int = Field(ge=0, le=MAX_QUANTITY)
    quantity_committed: int = Field(default=0, ge=0, le=MAX_QUANTITY)
    unit_cost: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    location: str = Field(default="main warehouse", min_length=1, max_length=100)
    @computed_field
    @property
    def available_quantity(self) -> int:
        return self.quantity_on_hand - self.quantity_committed

#job requirement
class JobRequirement(StockRescueModel):
    job_id: str = Field(min_length=1, max_length=100)
    sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9._/-]{0,63}$")
    quantity_required: int = Field(gt=0, le=MAX_QUANTITY)
    required_by: datetime
    priority: int = Field(default=3, ge=1, le=5)
    description: str = Field(default="", max_length=300)

#supplier offer
class SupplierOffer(StockRescueModel):
    offer_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=100)
    supplier_id: str = Field(min_length=1, max_length=100)
    supplier_name: str = Field(min_length=1, max_length=200)
    sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9._/-]{0,63}$")
    quantity_available: int = Field(ge=0, le=MAX_QUANTITY)
    unit_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    freight_cost: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    minimum_order_quantity: int = Field(default=1, ge=1, le=MAX_QUANTITY)
    pack_size: int = Field(default=1, ge=1, le=MAX_QUANTITY)
    lead_time_days: int | None = Field(default=None, ge=0, le=3650)
    quoted_at: datetime = Field(default_factory=utc_now)
    valid_until: datetime | None = None
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def validate_quote_expiry(self) -> "SupplierOffer":
        if self.valid_until is not None and self.valid_until <= self.quoted_at:
            raise ValueError("valid_until must be later than quoted_at")
        return self

#compatibility assessment
class CompatibilityAssessment(StockRescueModel):
    requested_sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9._/-]{0,63}$")
    candidate_sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9._/-]{0,63}$")
    status: CompatibilityStatus = CompatibilityStatus.UNKNOWN
    evidence: list[EvidenceText] = Field(default_factory=list, max_length=20)
    assessed_by: str = Field(default="unverified", min_length=1, max_length=100)
    assessed_at: datetime = Field(default_factory=utc_now)

#disruption case
class DisruptionCase(StockRescueModel):
    case_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=100)
    affected_sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9._/-]{0,63}$")
    failed_supplier_id: str = Field(min_length=1, max_length=100)
    reported_at: datetime = Field(default_factory=utc_now)
    delayed_until: datetime | None = None
    budget: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    jobs: list[JobRequirement] = Field(default_factory=list, max_length=200)
    inventory: list[InventoryRecord] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    offers: list[SupplierOffer] = Field(default_factory=list, max_length=500)
    compatibility: list[CompatibilityAssessment] = Field(
        default_factory=list, max_length=MAX_LIST_ITEMS
    )

    @model_validator(mode="after")
    def validate_unique_jobs_and_offer_currency(self) -> "DisruptionCase":
        job_ids = [job.job_id for job in self.jobs]
        if len(job_ids) != len(set(job_ids)):
            raise ValueError("job_id values must be unique within a disruption case")

        offer_ids = [offer.offer_id for offer in self.offers]
        if len(offer_ids) != len(set(offer_ids)):
            raise ValueError("offer_id values must be unique within a disruption case")
        mismatched = [offer.offer_id for offer in self.offers if offer.currency != self.currency]
        if mismatched:
            raise ValueError(
                "supplier offers must be normalized to the case currency before planning"
            )
        return self

#recovery line
class RecoveryLine(StockRescueModel):
    offer_id: str = Field(min_length=1, max_length=100)
    supplier_id: str = Field(min_length=1, max_length=100)
    sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9._/-]{0,63}$")
    quantity: int = Field(gt=0, le=MAX_QUANTITY)
    unit_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)

    @computed_field
    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.quantity

#recovery plan
class RecoveryPlan(StockRescueModel):
    plan_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=100)
    case_id: str = Field(min_length=1, max_length=100)
    status: RecoveryPlanStatus
    lines: list[RecoveryLine] = Field(default_factory=list, max_length=100)
    total_cost: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    expected_completion_at: datetime | None = None
    explanation: str = Field(default="", max_length=2_000)
    assumptions: list[EvidenceText] = Field(default_factory=list, max_length=50)
    blockers: list[EvidenceText] = Field(default_factory=list, max_length=50)
    generated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_status_and_lines(self) -> "RecoveryPlan":
        if self.status == RecoveryPlanStatus.PROPOSED and not self.lines:
            raise ValueError("a proposed recovery plan must contain at least one line")
        if self.status == RecoveryPlanStatus.NO_FEASIBLE_PLAN and self.lines:
            raise ValueError("a no-feasible-plan result cannot contain purchase lines")
        return self

    def compute_fingerprint(self) -> str:
        return compute_plan_fingerprint(self)


def compute_plan_fingerprint(plan: RecoveryPlan) -> str:
    """Compute a canonical SHA-256 fingerprint of all decision-relevant plan fields.

    Binds:
    - case_id, plan_id, status, currency, total_cost
    - delivery commitment: expected_completion_at
    - suppliers, SKUs, quantities, and prices in canonical sorted order
    """
    sorted_lines = sorted(
        plan.lines,
        key=lambda l: (l.offer_id, l.supplier_id, l.sku.upper(), l.quantity, str(l.unit_price)),
    )
    canonical_payload = {
        "case_id": plan.case_id,
        "status": plan.status.value,
        "currency": plan.currency,
        "total_cost": f"{Decimal(str(plan.total_cost)):.2f}",
        "expected_completion_at": (
            plan.expected_completion_at.isoformat()
            if plan.expected_completion_at is not None
            else None
        ),
        "lines": [
            {
                "offer_id": line.offer_id,
                "supplier_id": line.supplier_id,
                "sku": line.sku.upper(),
                "quantity": line.quantity,
                "unit_price": f"{Decimal(str(line.unit_price)):.2f}",
            }
            for line in sorted_lines
        ],
    }
    encoded = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


#procurement approval
class ProcurementApproval(StockRescueModel):
    approval_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=100)
    case_id: str = Field(min_length=1, max_length=100)
    plan_id: str = Field(min_length=1, max_length=100)
    plan_fingerprint: str = Field(default="", max_length=64)
    approved_by: str = Field(min_length=1, max_length=200)
    approved_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    max_total_cost: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    revoked: bool = False
    revoked_at: datetime | None = None
    revoked_by: str | None = Field(default=None, max_length=200)
    revocation_reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_expiry_and_revocation(self) -> "ProcurementApproval":
        if self.expires_at <= self.approved_at:
            raise ValueError("expires_at must be later than approved_at")
        if self.revoked and self.revoked_at is None:
            self.revoked_at = utc_now()
        if self.revoked_at is not None and not self.revoked:
            self.revoked = True
        return self

    def is_revoked(self) -> bool:
        """Return True when this approval has been revoked."""
        return self.revoked or self.revoked_at is not None

    def is_expired(self, at: datetime | None = None) -> bool:
        """Return True when this approval is no longer valid."""
        check_time = at or utc_now()
        if check_time.tzinfo is None or check_time.utcoffset() is None:
            raise ValueError("comparison time must include a timezone")
        return check_time >= self.expires_at

    def verify_against_plan(self, plan: RecoveryPlan, at: datetime | None = None) -> list[str]:
        """Verify this approval against a target plan and check time. Returns list of blockers/violations."""
        violations = []
        check_time = at or utc_now()
        if check_time.tzinfo is None or check_time.utcoffset() is None:
            raise ValueError("comparison time must include a timezone")

        if self.is_revoked():
            violations.append("approval_revoked")
        if self.is_expired(check_time):
            violations.append("approval_expired")
        if self.approved_at > check_time:
            violations.append("approval_from_future")
        if self.case_id != plan.case_id:
            violations.append("approval_case_mismatch")
        if self.plan_id != plan.plan_id:
            violations.append("approval_plan_mismatch")
        if self.currency != plan.currency:
            violations.append("currency_mismatch")
        if plan.total_cost > self.max_total_cost:
            violations.append("approval_cost_limit_exceeded")

        expected_fp = compute_plan_fingerprint(plan)
        if not self.plan_fingerprint or self.plan_fingerprint != expected_fp:
            violations.append("plan_fingerprint_mismatch")

        return violations


class OperatorRole(str, Enum):
    PROCUREMENT_APPROVER = "procurement_approver"
    ADMIN = "admin"
    VIEWER = "viewer"



DeliveryState = Literal[
    "ordered",
    "confirmed",
    "in_transit",
    "out_for_delivery",
    "delivered",
    "delayed",
    "cancelled",
]


class DeliveryEvent(StockRescueModel):
    event_id: str = Field(min_length=1, max_length=100)
    plan_id: str = Field(min_length=1, max_length=100)
    status: DeliveryState
    occurred_at: datetime
    source: Literal["carrier_api", "authorized_operator", "simulator"]
    reference: str | None = Field(default=None, max_length=100)
    details: str = Field(default="", max_length=300)

    @field_validator("occurred_at")
    @classmethod
    def validate_event_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("delivery event time must include a timezone")
        return value


class AuditEvent(StockRescueModel):
    event_id: str = Field(
        default_factory=lambda: f"AUD-{uuid4().hex[:12].upper()}",
        min_length=1,
        max_length=100,
    )
    event_type: str = Field(min_length=1, max_length=100)
    entity_type: str = Field(min_length=1, max_length=100)
    entity_id: str = Field(min_length=1, max_length=100)
    actor_id: str = Field(min_length=1, max_length=200)
    occurred_at: datetime = Field(default_factory=utc_now)
    details: dict[str, Any] = Field(default_factory=dict)


BusinessSize = Literal["small", "medium", "large"]


class Organization(StockRescueModel):
    org_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    business_type: str = Field(min_length=1, max_length=100)
    location: str = Field(min_length=1, max_length=200)
    country: str = Field(default="US", min_length=2, max_length=100)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    business_size: BusinessSize = "small"
    primary_objective: str = Field(default="minimize_downtime", max_length=300)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BusinessProfile(StockRescueModel):
    profile_id: str = Field(min_length=1, max_length=100)
    org_id: str = Field(min_length=1, max_length=100)
    budget_threshold: Decimal = Field(default=Decimal("5000.00"), ge=0, max_digits=12, decimal_places=2)
    approval_policy: Literal["strict", "auto_under_threshold", "manual"] = "strict"
    approved_suppliers: list[str] = Field(default_factory=list, max_length=100)
    restricted_suppliers: list[str] = Field(default_factory=list, max_length=100)
    warehouses: list[str] = Field(default_factory=lambda: ["main"], max_length=50)
    operating_currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class OrganizationMembership(StockRescueModel):
    membership_id: str = Field(min_length=1, max_length=100)
    org_id: str = Field(min_length=1, max_length=100)
    user_address: str = Field(min_length=1, max_length=200)
    role: Literal["admin", "buyer", "viewer"] = "admin"
    created_at: datetime = Field(default_factory=utc_now)


#agent message
class AgentMessage(StockRescueModel):
    schema_version: Literal["1.0"] = "1.0"
    message_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=100)
    workflow_id: str = Field(min_length=1, max_length=100)
    trace_id: str = Field(min_length=1, max_length=100)
    sender: str = Field(min_length=1, max_length=100)
    recipient: str = Field(min_length=1, max_length=100)
    message_type: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.-]{0,99}$")
    payload: dict[str, Any] = Field(max_length=100)
    created_at: datetime = Field(default_factory=utc_now)
    @field_validator("payload", mode="before")
    @classmethod
    def validate_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("payload must be a JSON object")
        _check_json_value(value, depth=0, counter=[0])
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ValueError("payload is not valid JSON data") from exc
        if len(encoded) > MAX_AGENT_PAYLOAD_BYTES:
            raise ValueError("payload exceeds the 16 KiB limit")
        return value
