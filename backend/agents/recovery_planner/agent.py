
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import Field

from agents.quote_risk.agent import QuoteRiskResult, run_quote_risk
from shared.models import (
    DisruptionCase,
    RecoveryPlan,
    RecoveryPlanStatus,
    StockRescueModel,
)
from tools.recovery_optimizer import (
    NoProcurementRequired,
    RecoveryOptimizationError,
    plan_recovery,
)


class RecoveryPlannerResult(StockRescueModel):
    case_id: str
    outcome: Literal[
        "awaiting_approval",
        "no_feasible_plan",
        "no_procurement_required",
        "blocked",
    ]
    recovery_plan: RecoveryPlan | None = None
    quote_risk: QuoteRiskResult
    summary: str = Field(min_length=1, max_length=1000)
    blockers: list[str] = Field(default_factory=list, max_length=50)
    procurement_authorized: Literal[False] = False


def run_recovery_planner(
    case: DisruptionCase,
    *,
    as_of: datetime | None = None,
) -> RecoveryPlannerResult:
    if not isinstance(case, DisruptionCase):
        raise ValueError("a validated disruption case is required")

    checked_at = as_of or datetime.now(timezone.utc)

    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise ValueError("comparison time must include a timezone")

    risk = run_quote_risk(case, as_of=checked_at)

    try:
        plan = plan_recovery(case, as_of=checked_at)

    except NoProcurementRequired:
        return RecoveryPlannerResult(
            case_id=case.case_id,
            outcome="no_procurement_required",
            quote_risk=risk,
            summary=(
                "Verified inventory covers the affected demand. "
                "No purchase is required."
            ),
        )

    except RecoveryOptimizationError:
        return RecoveryPlannerResult(
            case_id=case.case_id,
            outcome="blocked",
            quote_risk=risk,
            summary=(
                "Recovery planning stopped because validation "
                "or feasibility checks failed."
            ),
            blockers=["recovery_optimizer_blocked"],
        )

    if plan.case_id != case.case_id:
        return RecoveryPlannerResult(
            case_id=case.case_id,
            outcome="blocked",
            quote_risk=risk,
            summary="The optimizer returned a plan for a different case.",
            blockers=["case_id_mismatch"],
        )

    if plan.status == RecoveryPlanStatus.PROPOSED:
        return RecoveryPlannerResult(
            case_id=case.case_id,
            outcome="awaiting_approval",
            recovery_plan=plan,
            quote_risk=risk,
            summary=(
                "A recovery plan is ready for explicit human review. "
                "No purchase is authorized."
            ),
        )

    if plan.status == RecoveryPlanStatus.NO_FEASIBLE_PLAN:
        blockers = plan.blockers or ["no_feasible_plan"]

        return RecoveryPlannerResult(
            case_id=case.case_id,
            outcome="no_feasible_plan",
            recovery_plan=plan,
            quote_risk=risk,
            summary=(
                "No supplier option satisfied all verified "
                "cost, compatibility, quantity, and deadline constraints."
            ),
            blockers=blockers[:50],
        )

    return RecoveryPlannerResult(
        case_id=case.case_id,
        outcome="blocked",
        recovery_plan=plan,
        quote_risk=risk,
        summary="The recovery plan is blocked and needs further investigation.",
        blockers=(plan.blockers or ["plan_blocked"])[:50],
    )
