#purchase order calculations

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pydantic import ValidationError

from shared.idempotency import make_idempotency_key
from shared.models import DisruptionCase, ProcurementApproval, RecoveryPlan, RecoveryPlanStatus
from tools.recovery_optimizer import NoProcurementRequired, RecoveryOptimizationError, plan_recovery


#limits
MAX_PURCHASE_LINES = 100
MAX_TOTAL_COST = Decimal("9999999999.99")

class PurchaseOrderError(ValueError):
    pass

@dataclass(frozen=True, slots=True)
class PurchaseOrderLine:
    offer_id: str
    supplier_id: str
    sku: str
    quantity: int
    unit_price: Decimal

@dataclass(frozen=True, slots=True)
class PurchaseOrderDraft:
    draft_id: str
    idempotency_key: str
    case_id: str
    plan_id: str
    approval_id: str
    supplier_id: str
    currency: str
    lines: tuple[PurchaseOrderLine, ...]
    total_cost: Decimal
    prepared_at: datetime
    status: str = "draft"

def _validate_as_of(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise PurchaseOrderError("comparison time must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise PurchaseOrderError("comparison time must include a timezone")
    return value

def _clean_plan(plan: RecoveryPlan) -> RecoveryPlan:
    if not isinstance(plan, RecoveryPlan):
        raise PurchaseOrderError("recovery plan has an invalid type")
    try:
        return RecoveryPlan.model_validate(plan.model_dump(exclude={"lines": {"__all__": {"line_total"}}}))
    except (TypeError, ValueError, ValidationError) as exc:
        raise PurchaseOrderError("recovery plan failed validation") from exc

def _clean_case(case: DisruptionCase) -> DisruptionCase:
    if not isinstance(case, DisruptionCase):
        raise PurchaseOrderError("a freshly loaded disruption case is required")
    try:
        payload = {
            "case_id": case.case_id,
            "affected_sku": case.affected_sku,
            "failed_supplier_id": case.failed_supplier_id,
            "reported_at": case.reported_at,
            "delayed_until": case.delayed_until,
            "budget": case.budget,
            "currency": case.currency,
            "jobs": [item.model_dump() for item in case.jobs],
            "inventory": [item.model_dump(exclude={"available_quantity"}) for item in case.inventory],
            "offers": [item.model_dump() for item in case.offers],
            "compatibility": [item.model_dump() for item in case.compatibility],
        }
        return DisruptionCase.model_validate(payload)
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise PurchaseOrderError("current disruption case failed validation") from exc

def _clean_approval(approval: ProcurementApproval) -> ProcurementApproval:
    if not isinstance(approval, ProcurementApproval):
        raise PurchaseOrderError("procurement approval has an invalid type")
    try:
        return ProcurementApproval.model_validate(approval.model_dump())
    except (TypeError, ValueError, ValidationError) as exc:
        raise PurchaseOrderError("procurement approval failed validation") from exc

def prepare_purchase_order(
    plan: RecoveryPlan,
    approval: ProcurementApproval,
    current_case: DisruptionCase,
    *,
    as_of: datetime | None = None,
) -> PurchaseOrderDraft:
    comparison_time = _validate_as_of(as_of)
    clean_plan = _clean_plan(plan)
    clean_approval = _clean_approval(approval)
    clean_case = _clean_case(current_case)
    if len(clean_plan.lines) == 0 or len(clean_plan.lines) > MAX_PURCHASE_LINES:
        raise PurchaseOrderError("recovery plan has an invalid number of purchase lines")
    if clean_plan.status != RecoveryPlanStatus.PROPOSED:
        raise PurchaseOrderError("only a proposed recovery plan can become a purchase draft")
    if clean_plan.blockers:
        raise PurchaseOrderError("recovery plan contains unresolved blockers")
    if clean_plan.generated_at > comparison_time:
        raise PurchaseOrderError("recovery plan timestamp is in the future")
    if clean_plan.case_id != clean_case.case_id:
        raise PurchaseOrderError("recovery plan does not match the current case")
    if clean_approval.case_id != clean_plan.case_id or clean_approval.plan_id != clean_plan.plan_id:
        raise PurchaseOrderError("approval does not match the exact recovery plan")
    if clean_approval.is_revoked():
        raise PurchaseOrderError("procurement approval has been revoked")
    if clean_approval.approved_at > comparison_time or clean_approval.is_expired(comparison_time):
        raise PurchaseOrderError("procurement approval is expired or not yet valid")
    if clean_approval.plan_fingerprint and clean_approval.plan_fingerprint != clean_plan.compute_fingerprint():
        raise PurchaseOrderError("procurement approval fingerprint does not match the recovery plan")
    if clean_approval.approved_at < clean_plan.generated_at:
        raise PurchaseOrderError("approval predates the recovery plan")
    if clean_approval.currency != clean_plan.currency:
        raise PurchaseOrderError("approval currency does not match the plan")
    if clean_plan.total_cost > clean_approval.max_total_cost:
        raise PurchaseOrderError("recovery plan exceeds the approved cost ceiling")
    if clean_plan.total_cost > MAX_TOTAL_COST:
        raise PurchaseOrderError("recovery plan exceeds the supported monetary range")
    try:
        refreshed_plan = plan_recovery(clean_case, as_of=comparison_time)
    except (NoProcurementRequired, RecoveryOptimizationError) as exc:
        raise PurchaseOrderError("current case no longer supports the approved recovery plan") from exc
    if refreshed_plan.status != RecoveryPlanStatus.PROPOSED:
        raise PurchaseOrderError("current case no longer has a feasible recovery plan")
    if refreshed_plan.total_cost != clean_plan.total_cost or refreshed_plan.currency != clean_plan.currency:
        raise PurchaseOrderError("current recovery total differs from the approved plan")
    if refreshed_plan.assumptions != clean_plan.assumptions:
        raise PurchaseOrderError("inventory or planning assumptions changed; replan and obtain a new approval")
    if clean_plan.expected_completion_at is None or refreshed_plan.expected_completion_at is None:
        raise PurchaseOrderError("a verified delivery estimate is required before procurement")
    approved_lead_window = clean_plan.expected_completion_at - clean_plan.generated_at
    current_lead_window = refreshed_plan.expected_completion_at - comparison_time
    if current_lead_window > approved_lead_window:
        raise PurchaseOrderError("delivery estimate worsened; replan and obtain a new approval")
    plan_lines = tuple(sorted(
        (line.offer_id, line.supplier_id, line.sku, line.quantity, line.unit_price)
        for line in clean_plan.lines
    ))
    refreshed_lines = tuple(sorted(
        (line.offer_id, line.supplier_id, line.sku, line.quantity, line.unit_price)
        for line in refreshed_plan.lines
    ))
    if refreshed_lines != plan_lines:
        raise PurchaseOrderError("supplier, SKU, or quantity changed; replan and obtain a new approval")
    offers = {offer.offer_id: offer for offer in clean_case.offers}
    lines = []
    recalculated_total = Decimal("0.00")
    freight_added: set[str] = set()
    supplier_ids: set[str] = set()
    fingerprint_lines = []

    for line in clean_plan.lines:
        offer = offers.get(line.offer_id)
        if offer is None:
            raise PurchaseOrderError("a selected offer is missing from the current case")
        if offer.supplier_id == clean_case.failed_supplier_id:
            raise PurchaseOrderError("the disrupted supplier cannot fulfil the recovery order")
        if offer.supplier_id != line.supplier_id or offer.sku != line.sku:
            raise PurchaseOrderError("a selected offer no longer matches the approved line")
        if offer.currency != clean_plan.currency:
            raise PurchaseOrderError("selected offer currency differs from the approved currency")
        if offer.valid_until is None:
            raise PurchaseOrderError("a fresh, time-limited quote is required before procurement")
        if offer.quoted_at > comparison_time or comparison_time >= offer.valid_until:
            raise PurchaseOrderError("a selected supplier quote is not currently valid")
        if offer.freight_cost is None or offer.lead_time_days is None:
            raise PurchaseOrderError("current freight and delivery estimates are required")
        if line.quantity > offer.quantity_available:
            raise PurchaseOrderError("current supplier quantity is insufficient")
        if line.quantity < offer.minimum_order_quantity or line.quantity % offer.pack_size != 0:
            raise PurchaseOrderError("approved quantity no longer matches supplier order rules")
        if line.unit_price != offer.unit_price:
            raise PurchaseOrderError("supplier price changed; replan and obtain a new approval")
        recalculated_total += line.unit_price * line.quantity
        if offer.offer_id not in freight_added:
            recalculated_total += offer.freight_cost
            freight_added.add(offer.offer_id)
        supplier_ids.add(offer.supplier_id)
        lines.append(
            PurchaseOrderLine(
                offer_id=line.offer_id,
                supplier_id=line.supplier_id,
                sku=line.sku,
                quantity=line.quantity,
                unit_price=line.unit_price,
            )
        )
        fingerprint_lines.append({
            "offer_id": line.offer_id,
            "sku": line.sku,
            "quantity": line.quantity,
            "unit_price": str(line.unit_price),
        })
    if recalculated_total != clean_plan.total_cost:
        raise PurchaseOrderError("current supplier total differs from the approved plan; replan and reapprove")
    if recalculated_total > clean_approval.max_total_cost:
        raise PurchaseOrderError("refreshed purchase total exceeds the approved cost ceiling")
    if len(supplier_ids) != 1:
        raise PurchaseOrderError("a purchase draft must contain lines for one supplier")
    if not recalculated_total.is_finite() or recalculated_total < 0:
        raise PurchaseOrderError("purchase total is invalid")
    idempotency_key = make_idempotency_key(
        "purchase-order",
        {
            "case_id": clean_plan.case_id,
            "plan_id": clean_plan.plan_id,
            "approval_id": clean_approval.approval_id,
            "currency": clean_plan.currency,
            "total_cost": str(clean_plan.total_cost),
            "lines": fingerprint_lines,
        },
    )
    return PurchaseOrderDraft(
        draft_id=idempotency_key.rsplit(":", 1)[-1],
        idempotency_key=idempotency_key,
        case_id=clean_plan.case_id,
        plan_id=clean_plan.plan_id,
        approval_id=clean_approval.approval_id,
        supplier_id=next(iter(supplier_ids)),
        currency=clean_plan.currency,
        lines=tuple(lines),
        total_cost=recalculated_total,
        prepared_at=comparison_time,
    )
