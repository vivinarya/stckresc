from __future__ import annotations
import re
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import uuid4
from pydantic import Field, field_validator, model_validator
from shared.models import (
    AgentMessage,
    CompatibilityStatus,
    DisruptionCase,
    RecoveryPlan,
    StockRescueModel,
    MAX_QUANTITY,
)

#limits
MAX_CASE_DEMAND = MAX_QUANTITY * 200
MAX_AGGREGATED_STOCK = MAX_QUANTITY * 1_000
MAX_AGENT_OFFERS = 100
MAX_AGENT_JOBS = 200
MAX_AGENT_LOCATIONS = 100
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$", re.ASCII)
ISSUE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$", re.ASCII)

class AgentContractError(ValueError):
    pass

class AgentStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    BLOCKED = "blocked"

class CoordinatorOutcome(str, Enum):
    AWAITING_APPROVAL = "awaiting_approval"
    NO_FEASIBLE_PLAN = "no_feasible_plan"
    NO_PROCUREMENT_REQUIRED = "no_procurement_required"
    BLOCKED = "blocked"

class AgentContext(StockRescueModel):
    workflow_id: str = Field(min_length=1, max_length=100, pattern=IDENTIFIER_PATTERN.pattern)
    trace_id: str = Field(min_length=1, max_length=100, pattern=IDENTIFIER_PATTERN.pattern)
    case_id: str = Field(min_length=1, max_length=100, pattern=IDENTIFIER_PATTERN.pattern)
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AgentIssue(StockRescueModel):
    code: str = Field(min_length=1, max_length=64, pattern=ISSUE_CODE_PATTERN.pattern)
    detail: str = Field(min_length=1, max_length=300)

class AgentResultBase(StockRescueModel):
    context: AgentContext
    status: AgentStatus
    issues: list[AgentIssue] = Field(default_factory=list, max_length=30)

class InventoryBomResult(AgentResultBase):
    affected_sku: str = Field(min_length=1, max_length=64)
    demand_quantity: int = Field(ge=0, le=MAX_CASE_DEMAND)
    inventory_found: bool
    quantity_on_hand: int = Field(ge=0, le=MAX_AGGREGATED_STOCK)
    quantity_committed: int = Field(ge=0, le=MAX_AGGREGATED_STOCK)
    available_quantity: int = Field(ge=-MAX_AGGREGATED_STOCK, le=MAX_AGGREGATED_STOCK)
    shortage_quantity: int = Field(ge=0, le=MAX_CASE_DEMAND)
    locations: list[str] = Field(default_factory=list, max_length=MAX_AGENT_LOCATIONS)
    unsupported_job_skus: list[str] = Field(default_factory=list, max_length=50)
    @model_validator(mode="after")
    def validate_quantities(self) -> "InventoryBomResult":
        if self.available_quantity != self.quantity_on_hand - self.quantity_committed:
            raise ValueError("available inventory does not match stock and commitments")
        if self.shortage_quantity != max(0, self.demand_quantity - max(0, self.available_quantity)):
            raise ValueError("shortage does not match demand and available inventory")
        return self

class AffectedJob(StockRescueModel):
    job_id: str = Field(min_length=1, max_length=100)
    sku: str = Field(min_length=1, max_length=64)
    quantity_required: int = Field(gt=0, le=MAX_QUANTITY)
    required_by: datetime
    priority: int = Field(ge=1, le=5)
    covered_from_stock: int = Field(ge=0, le=MAX_QUANTITY)
    uncovered_quantity: int = Field(ge=0, le=MAX_QUANTITY)
    deadline_risk_from_delay: bool | None = None
    @model_validator(mode="after")
    def validate_allocation(self) -> "AffectedJob":
        if self.covered_from_stock + self.uncovered_quantity != self.quantity_required:
            raise ValueError("job allocation does not equal required quantity")
        return self

class JobImpactResult(AgentResultBase):
    affected_sku: str = Field(min_length=1, max_length=64)
    jobs: list[AffectedJob] = Field(default_factory=list, max_length=MAX_AGENT_JOBS)
    total_demand: int = Field(ge=0, le=MAX_CASE_DEMAND)
    inventory_available: int = Field(ge=-MAX_AGGREGATED_STOCK, le=MAX_AGGREGATED_STOCK)
    uncovered_demand: int = Field(ge=0, le=MAX_CASE_DEMAND)
    earliest_deadline: datetime | None = None
    unsupported_job_skus: list[str] = Field(default_factory=list, max_length=50)

class DiscoveredOffer(StockRescueModel):
    offer_id: str = Field(min_length=1, max_length=100)
    supplier_id: str = Field(min_length=1, max_length=100)
    supplier_name: str = Field(min_length=1, max_length=200)
    sku: str = Field(min_length=1, max_length=64)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    quantity_available: int = Field(ge=0, le=MAX_QUANTITY)
    required_quantity: int = Field(gt=0, le=MAX_QUANTITY)
    order_quantity: int = Field(gt=0, le=MAX_QUANTITY)
    unit_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    material_cost: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    freight_cost: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    total_cost: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    quoted_at: datetime
    valid_until: datetime | None = None
    lead_time_days: int | None = Field(default=None, ge=0, le=3650)
    estimated_delivery_at: datetime | None = None
    meets_earliest_deadline: bool | None = None
    @model_validator(mode="after")
    def validate_costs_and_timing(self) -> "DiscoveredOffer":
        if self.order_quantity < self.required_quantity and self.quantity_available >= self.required_quantity:
            raise ValueError("order quantity is smaller than required demand")
        expected_total = self.material_cost + self.freight_cost if self.freight_cost is not None else None
        if self.total_cost != expected_total:
            raise ValueError("total cost does not match material and freight costs")
        if self.lead_time_days is None and self.estimated_delivery_at is not None:
            raise ValueError("delivery time requires a known lead time")
        return self

class SupplierDiscoveryResult(AgentResultBase):
    affected_sku: str = Field(min_length=1, max_length=64)
    required_quantity: int = Field(ge=0, le=MAX_CASE_DEMAND)
    offers: list[DiscoveredOffer] = Field(default_factory=list, max_length=MAX_AGENT_OFFERS)
    excluded_failed_supplier_offers: int = Field(ge=0, le=500)
    excluded_unavailable_or_stale_offers: int = Field(ge=0, le=500)

class CompatibilityRecord(StockRescueModel):
    requested_sku: str = Field(min_length=1, max_length=64)
    candidate_sku: str = Field(min_length=1, max_length=64)
    status: CompatibilityStatus
    evidence: list[str] = Field(default_factory=list, max_length=20)
    assessed_by: str | None = Field(default=None, max_length=100)
    assessed_at: datetime | None = None
    offer_ids: list[str] = Field(default_factory=list, max_length=500)
    safe_for_planning: bool

class CompatibilityResult(AgentResultBase):
    affected_sku: str = Field(min_length=1, max_length=64)
    records: list[CompatibilityRecord] = Field(default_factory=list, max_length=MAX_AGENT_OFFERS)
    safe_offer_ids: list[str] = Field(default_factory=list, max_length=500)
    excluded_offer_ids: list[str] = Field(default_factory=list, max_length=500)

class CoordinatorResult(StockRescueModel):
    context: AgentContext
    outcome: CoordinatorOutcome
    inventory: InventoryBomResult
    job_impact: JobImpactResult
    supplier_discovery: SupplierDiscoveryResult
    compatibility: CompatibilityResult
    recovery_plan: RecoveryPlan | None = None
    messages: list[AgentMessage] = Field(default_factory=list, max_length=20)
    procurement_authorized: Literal[False] = False
    summary: str = Field(min_length=1, max_length=500)

def validate_case(case: DisruptionCase) -> DisruptionCase:
    if not isinstance(case, DisruptionCase):
        raise AgentContractError("a validated disruption case is required")
    try:
        return DisruptionCase.model_validate(
            case.model_dump(exclude={"inventory": {"__all__": {"available_quantity"}}})
        )
    except (TypeError, ValueError) as exc:
        raise AgentContractError("disruption case failed validation") from exc

def build_agent_context(
    case: DisruptionCase,
    *,
    workflow_id: str | None = None,
    trace_id: str | None = None,
    as_of: datetime | None = None,
) -> AgentContext:
    clean_case = validate_case(case)
    timestamp = as_of or datetime.now(timezone.utc)
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise AgentContractError("agent timestamp must include a timezone")
    return AgentContext(
        workflow_id=str(uuid4()) if workflow_id is None else workflow_id,
        trace_id=str(uuid4()) if trace_id is None else trace_id,
        case_id=clean_case.case_id,
        as_of=timestamp,
    )

def validate_context(context: AgentContext, case: DisruptionCase) -> AgentContext:
    clean_case = validate_case(case)
    if not isinstance(context, AgentContext):
        raise AgentContractError("a validated agent context is required")
    try:
        clean_context = AgentContext.model_validate(context.model_dump())
    except (TypeError, ValueError) as exc:
        raise AgentContractError("agent context failed validation") from exc
    if clean_context.case_id != clean_case.case_id:
        raise AgentContractError("agent context does not match the active case")
    return clean_context
