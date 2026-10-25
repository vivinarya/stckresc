from __future__ import annotations
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pydantic import ValidationError
from shared.models import CompatibilityAssessment, CompatibilityStatus, MAX_LIST_ITEMS
from tools.inventory import InventoryInputError, normalize_sku


#limits
MAX_COMPATIBILITY_ASSESSMENTS = MAX_LIST_ITEMS
class CompatibilityInputError(ValueError):
    pass

@dataclass(frozen=True, slots=True)
class CompatibilityDecision:
    requested_sku: str
    candidate_sku: str
    status: CompatibilityStatus
    evidence: tuple[str, ...]
    assessed_by: str | None
    assessed_at: datetime | None

    @property
    def is_compatible(self) -> bool:
        return self.status == CompatibilityStatus.COMPATIBLE

def _validate_as_of(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise CompatibilityInputError("comparison time must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise CompatibilityInputError("comparison time must include a timezone")
    return value

def _validate_assessments(
    assessments: Sequence[CompatibilityAssessment],
) -> tuple[CompatibilityAssessment, ...]:
    if isinstance(assessments, (str, bytes, bytearray)) or not isinstance(assessments, Sequence):
        raise CompatibilityInputError("compatibility assessments must be a list or tuple")
    if len(assessments) > MAX_COMPATIBILITY_ASSESSMENTS:
        raise CompatibilityInputError("compatibility assessment limit exceeded")
    validated = []
    for index, assessment in enumerate(assessments):
        if not isinstance(assessment, CompatibilityAssessment):
            raise CompatibilityInputError(
                f"compatibility assessment at index {index} has an invalid type"
            )
        try:
            clean_assessment = CompatibilityAssessment.model_validate(
                assessment.model_dump()
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise CompatibilityInputError(
                f"compatibility assessment at index {index} failed validation"
            ) from exc
        validated.append(clean_assessment)
    return tuple(validated)

def assess_compatibility(
    requested_sku: str,
    candidate_sku: str,
    assessments: Sequence[CompatibilityAssessment],
    *,
    as_of: datetime | None = None,
) -> CompatibilityDecision:
    try:
        requested = normalize_sku(requested_sku)
        candidate = normalize_sku(candidate_sku)
    except InventoryInputError as exc:
        raise CompatibilityInputError("SKU is invalid") from exc

    comparison_time = _validate_as_of(as_of)
    records = _validate_assessments(assessments)
    if requested == candidate:
        return CompatibilityDecision(
            requested_sku=requested,
            candidate_sku=candidate,
            status=CompatibilityStatus.COMPATIBLE,
            evidence=("exact SKU match",),
            assessed_by="system",
            assessed_at=comparison_time,
        )
    matching = tuple(
        assessment
        for assessment in records
        if normalize_sku(assessment.requested_sku) == requested
        and normalize_sku(assessment.candidate_sku) == candidate
        and assessment.assessed_at <= comparison_time
    )
    if not matching:
        return CompatibilityDecision(
            requested_sku=requested,
            candidate_sku=candidate,
            status=CompatibilityStatus.UNKNOWN,
            evidence=(),
            assessed_by=None,
            assessed_at=None,
        )
    latest_time = max(assessment.assessed_at for assessment in matching)
    latest = tuple(
        assessment for assessment in matching if assessment.assessed_at == latest_time
    )
    latest_statuses = {assessment.status for assessment in latest}
    if len(latest_statuses) != 1:
        return CompatibilityDecision(
            requested_sku=requested,
            candidate_sku=candidate,
            status=CompatibilityStatus.UNKNOWN,
            evidence=tuple(
                dict.fromkeys(
                    evidence
                    for assessment in latest
                    for evidence in assessment.evidence
                )
            ),
            assessed_by=None,
            assessed_at=latest_time,
        )
    selected = latest[0]
    evidence = tuple(
        dict.fromkeys(
            evidence
            for assessment in latest
            for evidence in assessment.evidence
        )
    )
    assessors = {assessment.assessed_by for assessment in latest}
    return CompatibilityDecision(
        requested_sku=requested,
        candidate_sku=candidate,
        status=selected.status,
        evidence=evidence,
        assessed_by=next(iter(assessors)) if len(assessors) == 1 else None,
        assessed_at=latest_time,
    )
