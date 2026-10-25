#recovery calculations

from __future__ import annotations
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pydantic import ValidationError
from shared.models import (
    CompatibilityStatus,
    DisruptionCase,
    RecoveryLine,
    RecoveryPlan,
    RecoveryPlanStatus,
    SupplierOffer,
    MAX_QUANTITY,
)
from tools.compatibility import CompatibilityInputError, assess_compatibility
from tools.inventory import InventoryInputError, check_inventory, normalize_sku
from tools.quote_normalizer import QuoteNormalizationError, normalize_quote
from tools.supplier_catalog import SupplierCatalogError, find_supplier_offers

#limits
MAX_CASE_JOBS = 200
MAX_CASE_OFFERS = 500
MAX_PLAN_COST = Decimal("9999999999.99")

class RecoveryOptimizationError(ValueError):
    pass

class NoProcurementRequired(RecoveryOptimizationError):
    pass

@dataclass(frozen=True, slots=True)
class _Candidate:
    line: RecoveryLine
    total_cost: Decimal
    delivery_at: datetime
    supplier_id: str
    offer_id: str
    assumptions: tuple[str, ...]

def _validate_as_of(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise RecoveryOptimizationError("comparison time must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise RecoveryOptimizationError("comparison time must include a timezone")
    return value

def _validate_case(case: DisruptionCase) -> DisruptionCase:
    if not isinstance(case, DisruptionCase):
        raise RecoveryOptimizationError("disruption case has an invalid type")
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
        clean_case = DisruptionCase.model_validate(payload)
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise RecoveryOptimizationError("disruption case failed validation") from exc
    if len(clean_case.jobs) > MAX_CASE_JOBS or len(clean_case.offers) > MAX_CASE_OFFERS:
        raise RecoveryOptimizationError("disruption case exceeds planning limits")
    return clean_case

def _no_plan(case: DisruptionCase, blockers: Sequence[str], *, as_of: datetime) -> RecoveryPlan:
    cleaned = tuple(dict.fromkeys(text.strip() for text in blockers if text.strip()))
    if not cleaned:
        cleaned = ("No supplier option satisfied every required planning constraint.",)
    return RecoveryPlan(
        case_id=case.case_id,
        status=RecoveryPlanStatus.NO_FEASIBLE_PLAN,
        lines=[],
        total_cost=Decimal("0.00"),
        currency=case.currency,
        explanation="No purchase recommendation was produced because one or more safety or feasibility checks failed.",
        blockers=list(cleaned[:50]),
        generated_at=as_of,
    )

def _candidate_skus(
    case: DisruptionCase,
    offers: Sequence[SupplierOffer],
    target_sku: str,
    as_of: datetime,
    blockers: list[str],
) -> tuple[str, ...]:
    eligible = []
    assessments = case.compatibility
    for offer in offers:
        try:
            candidate_sku = normalize_sku(offer.sku)
        except InventoryInputError as exc:
            raise RecoveryOptimizationError("supplier SKU is invalid") from exc
        if candidate_sku == target_sku:
            eligible.append(candidate_sku)
            continue
        try:
            decision = assess_compatibility(
                target_sku,
                candidate_sku,
                assessments,
                as_of=as_of,
            )
        except CompatibilityInputError as exc:
            raise RecoveryOptimizationError("compatibility data failed validation") from exc
        if decision.status == CompatibilityStatus.INCOMPATIBLE:
            blockers.append("An alternative SKU is marked incompatible and was excluded.")
        elif decision.status != CompatibilityStatus.COMPATIBLE:
            blockers.append("Alternative SKU compatibility is unknown; the substitute was excluded.")
        elif decision.assessed_by in (None, "", "unverified") or not decision.evidence:
            blockers.append("An alternative SKU assessment lacks a named assessor or evidence.")
        else:
            eligible.append(candidate_sku)
    return tuple(dict.fromkeys(eligible))

def plan_recovery(
    case: DisruptionCase,
    *,
    as_of: datetime | None = None,
) -> RecoveryPlan:
    comparison_time = _validate_as_of(as_of)
    clean_case = _validate_case(case)
    try:
        target_sku = normalize_sku(clean_case.affected_sku)
    except InventoryInputError as exc:
        raise RecoveryOptimizationError("affected SKU is invalid") from exc

    if not clean_case.jobs:
        return _no_plan(clean_case, ("No affected jobs were supplied.",), as_of=comparison_time)
    try:
        job_skus = {normalize_sku(job.sku) for job in clean_case.jobs}
    except InventoryInputError as exc:
        raise RecoveryOptimizationError("job SKU is invalid") from exc
    if job_skus != {target_sku}:
        raise RecoveryOptimizationError(
            "this optimizer handles one disrupted SKU per case; split multi-SKU cases before planning"
        )
    demand = sum(job.quantity_required for job in clean_case.jobs)
    if demand < 1 or demand > MAX_QUANTITY:
        raise RecoveryOptimizationError("combined job demand is outside the allowed range")

    inventory_records = [
        item.model_copy(deep=True) for item in clean_case.inventory
    ]
    try:
        inventory = check_inventory(target_sku, demand, inventory_records)
    except InventoryInputError as exc:
        raise RecoveryOptimizationError("inventory data failed validation") from exc

    shortage = inventory.shortage_quantity
    if shortage == 0:
        raise NoProcurementRequired("available inventory covers all affected job demand")

    earliest_deadline = min(job.required_by for job in clean_case.jobs)
    blockers = []
    candidates: list[_Candidate] = []
    active_offers = [
        offer for offer in clean_case.offers
        if offer.supplier_id != clean_case.failed_supplier_id
    ]
    if not active_offers:
        return _no_plan(
            clean_case,
            ("No offers from an alternative supplier were provided.",),
            as_of=comparison_time,
        )

    offer_ids = [offer.offer_id for offer in active_offers]
    if len(offer_ids) != len(set(offer_ids)):
        raise RecoveryOptimizationError("supplier offer IDs must be unique")
    alternate_skus = _candidate_skus(
        clean_case,
        active_offers,
        target_sku,
        comparison_time,
        blockers,
    )
    for candidate_sku in alternate_skus:
        try:
            supplier_candidates = find_supplier_offers(
                candidate_sku,
                shortage,
                active_offers,
                currency=clean_case.currency,
                as_of=comparison_time,
            )
        except SupplierCatalogError as exc:
            raise RecoveryOptimizationError("supplier offer data failed validation") from exc
        for candidate in supplier_candidates:
            try:
                quote = normalize_quote(
                    candidate.offer,
                    shortage,
                    currency=clean_case.currency,
                    as_of=comparison_time,
                )
            except QuoteNormalizationError:
                blockers.append("A supplier quote failed normalization or freshness checks.")
                continue

            if not quote.freight_known or quote.total_cost is None:
                blockers.append("A supplier quote has an unknown freight cost.")
                continue
            if not quote.lead_time_known or quote.estimated_delivery_at is None:
                blockers.append("A supplier quote has no verified delivery estimate.")
                continue
            if quote.estimated_delivery_at > earliest_deadline:
                blockers.append("A supplier option cannot meet the earliest affected job deadline.")
                continue
            if quote.total_cost > MAX_PLAN_COST:
                blockers.append("A supplier option exceeds the supported monetary range.")
                continue
            if clean_case.budget is not None and quote.total_cost > clean_case.budget:
                blockers.append("A supplier option exceeds the case budget.")
                continue

            candidate_assumptions = []
            if not quote.expiry_known:
                candidate_assumptions.append(
                    "The quote has no explicit expiry; obtain a refreshed, time-limited quote before procurement."
                )
            if candidate_sku != target_sku:
                candidate_assumptions.append(
                    "The alternative SKU relies on a stored compatibility assessment that must be rechecked before procurement."
                )

            try:
                line = RecoveryLine(
                    offer_id=quote.offer_id,
                    supplier_id=quote.supplier_id,
                    sku=quote.sku,
                    quantity=quote.order_quantity,
                    unit_price=quote.unit_price,
                )
            except ValidationError:
                blockers.append("A supplier option produced an invalid recovery line.")
                continue

            candidates.append(
                _Candidate(
                    line=line,
                    total_cost=quote.total_cost,
                    delivery_at=quote.estimated_delivery_at,
                    supplier_id=quote.supplier_id,
                    offer_id=quote.offer_id,
                    assumptions=tuple(candidate_assumptions),
                )
            )
    if not candidates:
        blockers.append(
            "No single alternative supplier offer can cover the shortage and satisfy every verified constraint."
        )
        return _no_plan(clean_case, blockers, as_of=comparison_time)
    best = min(
        candidates,
        key=lambda item: (
            item.total_cost,
            item.delivery_at,
            item.supplier_id,
            item.offer_id,
        ),
    )
    assumptions = [
        f"Combined demand for {target_sku} is {demand} units.",
        f"Net available inventory is {inventory.available_quantity} units after existing commitments.",
        f"The recovery order covers a shortage of {shortage} units; pack or minimum-order rules may cause surplus.",
    ]
    assumptions.extend(best.assumptions)
    try:
        return RecoveryPlan(
            case_id=clean_case.case_id,
            status=RecoveryPlanStatus.PROPOSED,
            lines=[best.line],
            total_cost=best.total_cost,
            currency=clean_case.currency,
            expected_completion_at=best.delivery_at,
            explanation="Selected the least-cost single offer among options that passed compatibility, quote, quantity, cost, and deadline checks.",
            assumptions=list(dict.fromkeys(assumptions))[:50],
            blockers=[],
            generated_at=comparison_time,
        )
    except ValidationError as exc:
        raise RecoveryOptimizationError("recovery plan failed final validation") from exc
