from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pydantic import ValidationError
from shared.models import ProcurementApproval, RecoveryPlan, RecoveryPlanStatus, WorkflowStatus

#limits
MAX_ACTOR_LENGTH = 100
MAX_REASON_LENGTH = 500
ACTOR_PATTERN = re.compile(r"^[\w.@:-]{1,100}$", re.ASCII)

class WorkflowTransitionError(ValueError):
    pass

@dataclass(frozen=True, slots=True)
class WorkflowTransition:
    previous: WorkflowStatus
    current: WorkflowStatus
    actor: str
    reason: str
    at: datetime

_ALLOWED_TRANSITIONS = {
    WorkflowStatus.CREATED: {WorkflowStatus.INVESTIGATING, WorkflowStatus.BLOCKED, WorkflowStatus.CANCELLED},
    WorkflowStatus.INVESTIGATING: {WorkflowStatus.PLANNING, WorkflowStatus.BLOCKED, WorkflowStatus.CANCELLED},
    WorkflowStatus.PLANNING: {WorkflowStatus.AWAITING_APPROVAL, WorkflowStatus.BLOCKED, WorkflowStatus.CANCELLED},
    WorkflowStatus.AWAITING_APPROVAL: {WorkflowStatus.APPROVED, WorkflowStatus.PLANNING, WorkflowStatus.BLOCKED, WorkflowStatus.CANCELLED},
    WorkflowStatus.APPROVED: {WorkflowStatus.PLANNING, WorkflowStatus.BLOCKED, WorkflowStatus.CANCELLED},
    WorkflowStatus.PROCUREMENT_SUBMITTED: {WorkflowStatus.IN_TRANSIT, WorkflowStatus.COMPLETED, WorkflowStatus.BLOCKED},
    WorkflowStatus.IN_TRANSIT: {WorkflowStatus.COMPLETED, WorkflowStatus.BLOCKED},
    WorkflowStatus.BLOCKED: {WorkflowStatus.INVESTIGATING, WorkflowStatus.PLANNING, WorkflowStatus.CANCELLED},
    WorkflowStatus.COMPLETED: set(),
    WorkflowStatus.CANCELLED: set(),
}

def _status(value: WorkflowStatus | str) -> WorkflowStatus:
    try:
        return value if isinstance(value, WorkflowStatus) else WorkflowStatus(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionError("workflow status is invalid") from exc

def _clean_actor(value: str) -> str:
    if not isinstance(value, str):
        raise WorkflowTransitionError("actor must be text")
    actor = value.strip()
    if not actor or len(actor) > MAX_ACTOR_LENGTH or ACTOR_PATTERN.fullmatch(actor) is None:
        raise WorkflowTransitionError("actor identifier is invalid")
    return actor

def _clean_reason(value: str) -> str:
    if not isinstance(value, str):
        raise WorkflowTransitionError("transition reason must be text")
    reason = value.strip()
    if not reason or len(reason) > MAX_REASON_LENGTH:
        raise WorkflowTransitionError("transition reason is outside the allowed range")
    if any(unicodedata.category(char) == "Cc" for char in reason):
        raise WorkflowTransitionError("transition reason contains control characters")
    return reason

def _validate_approval(
    plan: RecoveryPlan | None,
    approval: ProcurementApproval | None,
    case_id: str | None,
    at: datetime,
) -> None:
    if not isinstance(plan, RecoveryPlan) or not isinstance(approval, ProcurementApproval):
        raise WorkflowTransitionError("a plan and procurement approval are required")
    if not isinstance(case_id, str) or not case_id.strip():
        raise WorkflowTransitionError("the active case ID is required")
    try:
        clean_plan = RecoveryPlan.model_validate(
            plan.model_dump(exclude={"lines": {"__all__": {"line_total"}}})
        )
        clean_approval = ProcurementApproval.model_validate(approval.model_dump())
    except (TypeError, ValueError, ValidationError) as exc:
        raise WorkflowTransitionError("plan or approval failed validation") from exc
    if clean_plan.case_id != case_id or clean_approval.case_id != case_id:
        raise WorkflowTransitionError("approval does not match the active case")
    if clean_plan.plan_id != clean_approval.plan_id:
        raise WorkflowTransitionError("approval does not match the exact recovery plan")
    if clean_plan.status != RecoveryPlanStatus.PROPOSED or clean_plan.blockers or not clean_plan.lines:
        raise WorkflowTransitionError("only an unblocked proposed plan can be approved")
    if clean_plan.currency != clean_approval.currency:
        raise WorkflowTransitionError("approval currency does not match the recovery plan")
    if clean_plan.total_cost > clean_approval.max_total_cost:
        raise WorkflowTransitionError("recovery plan exceeds the approved cost ceiling")
    if clean_plan.generated_at > clean_approval.approved_at:
        raise WorkflowTransitionError("approval predates the recovery plan")
    if clean_approval.is_revoked():
        raise WorkflowTransitionError("approval has been revoked")
    if clean_approval.approved_at > at or clean_approval.is_expired(at):
        raise WorkflowTransitionError("approval is expired or not yet valid")
    if clean_approval.plan_fingerprint and clean_approval.plan_fingerprint != clean_plan.compute_fingerprint():
        raise WorkflowTransitionError("approval fingerprint does not match the recovery plan")

def transition_workflow(
    current: WorkflowStatus | str,
    target: WorkflowStatus | str,
    *,
    actor: str,
    reason: str,
    at: datetime | None = None,
    case_id: str | None = None,
    plan: RecoveryPlan | None = None,
    approval: ProcurementApproval | None = None,
) -> WorkflowTransition:
    previous_status = _status(current)
    target_status = _status(target)
    actor_value = _clean_actor(actor)
    reason_value = _clean_reason(reason)
    timestamp = at or datetime.now(timezone.utc)
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise WorkflowTransitionError("transition timestamp must include a timezone")
    if timestamp > datetime.now(timezone.utc):
        raise WorkflowTransitionError("transition timestamp cannot be in the future")
    if target_status == WorkflowStatus.PROCUREMENT_SUBMITTED:
        raise WorkflowTransitionError("live procurement submission is not implemented")
    if target_status not in _ALLOWED_TRANSITIONS[previous_status]:
        raise WorkflowTransitionError("workflow transition is not allowed")
    if previous_status == WorkflowStatus.AWAITING_APPROVAL and target_status == WorkflowStatus.APPROVED:
        _validate_approval(plan, approval, case_id, timestamp)
    return WorkflowTransition(
        previous=previous_status,
        current=target_status,
        actor=actor_value,
        reason=reason_value,
        at=timestamp.astimezone(timezone.utc),
    )
