from __future__ import annotations
from shared.agent_contracts import (
    AgentContext,
    AgentIssue,
    AgentStatus,
    CompatibilityRecord,
    CompatibilityResult,
    SupplierDiscoveryResult,
    validate_case,
    validate_context,
)
from shared.models import CompatibilityStatus, DisruptionCase
from tools.compatibility import CompatibilityInputError, assess_compatibility
from tools.inventory import normalize_sku

def run_compatibility(
    case: DisruptionCase,
    discovery: SupplierDiscoveryResult,
    context: AgentContext,
) -> CompatibilityResult:
    clean_case = validate_case(case)
    clean_context = validate_context(context, clean_case)
    if discovery.context.case_id != clean_context.case_id or discovery.context.workflow_id != clean_context.workflow_id or discovery.context.trace_id != clean_context.trace_id:
        raise ValueError("supplier result does not match the active workflow")
    requested_sku = normalize_sku(clean_case.affected_sku)
    offers_by_sku: dict[str, list[str]] = {}
    for offer in discovery.offers:
        offers_by_sku.setdefault(normalize_sku(offer.sku), []).append(offer.offer_id)
    issues: list[AgentIssue] = []
    records: list[CompatibilityRecord] = []
    safe_offer_ids: list[str] = []
    excluded_offer_ids: list[str] = []
    for candidate_sku in sorted(offers_by_sku):
        try:
            decision = assess_compatibility(
                requested_sku,
                candidate_sku,
                clean_case.compatibility,
                as_of=clean_context.as_of,
            )
        except CompatibilityInputError as exc:
            raise ValueError("compatibility data failed validation") from exc
        exact_match = candidate_sku == requested_sku
        safe = exact_match or (
            decision.status == CompatibilityStatus.COMPATIBLE
            and decision.assessed_by not in (None, "", "unverified")
            and bool(decision.evidence)
        )
        offer_ids = sorted(set(offers_by_sku[candidate_sku]))
        records.append(
            CompatibilityRecord(
                requested_sku=requested_sku,
                candidate_sku=candidate_sku,
                status=decision.status,
                evidence=list(decision.evidence[:20]),
                assessed_by=decision.assessed_by,
                assessed_at=decision.assessed_at,
                offer_ids=offer_ids,
                safe_for_planning=safe,
            )
        )
        if safe:
            safe_offer_ids.extend(offer_ids)
        else:
            excluded_offer_ids.extend(offer_ids)
            if decision.status == CompatibilityStatus.INCOMPATIBLE:
                issues.append(AgentIssue(code="part_incompatible", detail="An alternative part is marked incompatible and was excluded."))
            elif decision.status == CompatibilityStatus.UNKNOWN:
                issues.append(AgentIssue(code="compatibility_unknown", detail="An alternative part has no verified compatibility assessment and was excluded."))
            else:
                issues.append(AgentIssue(code="compatibility_evidence_insufficient", detail="An alternative part lacks a named assessor or evidence and was excluded."))
    if not records:
        status = AgentStatus.BLOCKED
        issues.append(AgentIssue(code="no_supplier_candidates", detail="No supplier candidates were available for compatibility review."))
    elif not safe_offer_ids:
        status = AgentStatus.BLOCKED
    elif excluded_offer_ids:
        status = AgentStatus.PARTIAL
    else:
        status = AgentStatus.COMPLETE
    return CompatibilityResult(
        context=clean_context,
        status=status,
        issues=issues[:30],
        affected_sku=requested_sku,
        records=records,
        safe_offer_ids=sorted(set(safe_offer_ids)),
        excluded_offer_ids=sorted(set(excluded_offer_ids)),
    )
