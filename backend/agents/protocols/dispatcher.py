"""Specialist request dispatcher and execution handler.

Enforces:
1. Framework sender validation against explicit allowlists (fail-closed).
2. Recipient task-type permissions (e.g., supplier discovery cannot run procurement).
3. Deadline expiration checks.
4. Idempotency against duplicate requests.
5. Strict payload parsing and execution of existing specialist logic.
6. Safe typed response construction.
"""

from __future__ import annotations

import collections
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from agents.compatibility.agent import run_compatibility
from agents.delivery_watch.agent import run_delivery_watch
from agents.inventory_bom.agent import run_inventory_bom
from agents.job_impact.agent import run_job_impact
from agents.procurement.agent import prepare_procurement_draft
from agents.protocols.messages import (
    ALLOWED_TASK_TYPES,
    TaskErrorCode,
    TaskRequest,
    TaskResponse,
    TaskResponseStatus,
)
from agents.quote_risk.agent import run_quote_risk
from agents.recovery_planner.agent import run_recovery_planner
from agents.supplier_discovery.agent import run_supplier_discovery
from agents.vendor_voice.agent import execute_voice_rfq, prepare_vendor_communication
from shared.agent_contracts import (
    AgentContext,
    CompatibilityResult,
    InventoryBomResult,
    SupplierDiscoveryResult,
)
from shared.models import (
    DisruptionCase,
    ProcurementApproval,
    RecoveryPlan,
)

logger = logging.getLogger(__name__)

# Permitted task types per specialist role
# Under the 8-identity configuration for Agentverse Basic:
# - supplier_discovery (Supplier Sourcing) handles both supplier_discovery and compatibility.
# - quote_risk (Quote & Planning) handles both quote_risk and recovery_planner.
AGENT_ROLE_ALLOWED_TASKS: dict[str, frozenset[str]] = {
    "inventory_bom": frozenset({"inventory_bom"}),
    "job_impact": frozenset({"job_impact"}),
    "supplier_discovery": frozenset({"supplier_discovery", "compatibility"}),
    "supplier_sourcing": frozenset({"supplier_discovery", "compatibility"}),
    "compatibility": frozenset({"compatibility"}),
    "quote_risk": frozenset({"quote_risk", "recovery_planner"}),
    "quote_planning": frozenset({"quote_risk", "recovery_planner"}),
    "recovery_planner": frozenset({"recovery_planner"}),
    "procurement": frozenset({"procurement_draft"}),
    "vendor_voice": frozenset({"vendor_communication_prep", "vendor_voice_call"}),
    "delivery_watch": frozenset({"delivery_watch"}),
}

MAX_IDEMPOTENCY_CACHE = 500


def _clean_case_payload(raw: Any) -> DisruptionCase:
    if isinstance(raw, DisruptionCase):
        return validate_case(raw)
    if isinstance(raw, dict):
        clean_dict = dict(raw)
        if "inventory" in clean_dict and isinstance(clean_dict["inventory"], list):
            clean_dict["inventory"] = [
                {k: v for k, v in item.items() if k != "available_quantity"}
                if isinstance(item, dict) else item
                for item in clean_dict["inventory"]
            ]
        return DisruptionCase.model_validate(clean_dict)
    raise ValueError("Invalid case payload: expected dict or DisruptionCase")


def _clean_plan_payload(raw: Any) -> RecoveryPlan:
    if isinstance(raw, RecoveryPlan):
        return raw
    if isinstance(raw, dict):
        clean_dict = dict(raw)
        if "lines" in clean_dict and isinstance(clean_dict["lines"], list):
            clean_dict["lines"] = [
                {k: v for k, v in item.items() if k != "line_total"}
                if isinstance(item, dict) else item
                for item in clean_dict["lines"]
            ]
        return RecoveryPlan.model_validate(clean_dict)
    raise ValueError("Invalid plan payload: expected dict or RecoveryPlan")


class SpecialistDispatcher:
    """Dispatches incoming TaskRequests to their domain implementation."""

    def __init__(
        self,
        role: str,
        agent_name: str,
        allowed_senders: set[str],
        permitted_tasks: set[str] | frozenset[str] | None = None,
    ) -> None:
        if permitted_tasks is not None:
            self.permitted_tasks = frozenset(permitted_tasks)
        elif role in AGENT_ROLE_ALLOWED_TASKS:
            self.permitted_tasks = AGENT_ROLE_ALLOWED_TASKS[role]
        else:
            raise ValueError(f"Unknown agent role '{role}'")
        self.role = role
        self.agent_name = agent_name
        self.allowed_senders = set(allowed_senders)
        self._idempotency_cache: collections.OrderedDict[tuple[str, str], TaskResponse] = collections.OrderedDict()

    def _get_cached_response(self, sender: str, correlation_id: str) -> TaskResponse | None:
        return self._idempotency_cache.get((sender, correlation_id))

    def _cache_response(self, sender: str, correlation_id: str, response: TaskResponse) -> None:
        key = (sender, correlation_id)
        if key in self._idempotency_cache:
            self._idempotency_cache.move_to_end(key)
        self._idempotency_cache[key] = response
        if len(self._idempotency_cache) > MAX_IDEMPOTENCY_CACHE:
            self._idempotency_cache.popitem(last=False)

    def dispatch(self, framework_sender: str, request: TaskRequest) -> TaskResponse:
        """Processes an incoming TaskRequest using framework-verified sender identity."""
        from observability.tracing import trace_span

        with trace_span(
            f"specialist.{self.role}.dispatch",
            workflow_id=getattr(request, "workflow_id", None),
            trace_id=getattr(request, "trace_id", None),
            input_data={"task_type": getattr(request, "task_type", None), "framework_sender": framework_sender},
        ) as span:
            # 1. Framework Sender Identity & Fail-Closed Allowlist
            if not self.allowed_senders:
                logger.warning("Rejecting request: sender allowlist is empty (fail-closed)")
                span.update(output={"status": "rejected", "error_category": "unauthorized_sender"})
                return TaskResponse.failure(
                    request,
                    sender=self.agent_name,
                    status=TaskResponseStatus.REJECTED,
                    error_code=TaskErrorCode.UNAUTHORIZED_SENDER,
                    error_detail="Sender allowlist is empty or not configured",
                )

            if framework_sender not in self.allowed_senders:
                logger.warning(
                    "Rejecting request: framework sender %s is not in allowlist",
                    framework_sender,
                )
                span.update(output={"status": "rejected", "error_category": "unauthorized_sender"})
                return TaskResponse.failure(
                    request,
                    sender=self.agent_name,
                    status=TaskResponseStatus.REJECTED,
                    error_code=TaskErrorCode.UNAUTHORIZED_SENDER,
                    error_detail="Sender identity is not authorized",
                )

            # 2. Permitted Task Types for this Specialist
            if request.task_type not in self.permitted_tasks:
                logger.warning(
                    "Rejecting request: task_type '%s' not permitted for role '%s'",
                    request.task_type,
                    self.role,
                )
                span.update(output={"status": "rejected", "error_category": "forbidden_task"})
                return TaskResponse.failure(
                    request,
                    sender=self.agent_name,
                    status=TaskResponseStatus.REJECTED,
                    error_code=TaskErrorCode.TASK_TYPE_FORBIDDEN,
                    error_detail=f"Task type '{request.task_type}' is not permitted for this agent",
                )

        # 3. Deadline Check
        if request.is_expired():
            logger.info("Rejecting request: deadline has expired")
            return TaskResponse.failure(
                request,
                sender=self.agent_name,
                status=TaskResponseStatus.REJECTED,
                error_code=TaskErrorCode.DEADLINE_EXPIRED,
                error_detail="Task deadline has expired",
            )

        # 4. Idempotency Check
        cached = self._get_cached_response(framework_sender, request.correlation_id)
        if cached is not None:
            logger.debug(
                "Returning cached response for correlation_id=%s",
                request.correlation_id,
            )
            return cached

        # 5. Execute Specialist Logic
        try:
            payload = request.get_payload()
            result = self._execute_task(request.task_type, payload)
            response = TaskResponse.ok(
                request,
                sender=self.agent_name,
                result=result,
            )
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning("Specialist input error for task %s: %s", request.task_type, exc)
            response = TaskResponse.failure(
                request,
                sender=self.agent_name,
                status=TaskResponseStatus.INVALID_REQUEST,
                error_code=TaskErrorCode.INVALID_PAYLOAD,
                error_detail=str(exc),
            )
        except Exception as exc:
            logger.exception("Specialist internal error for task %s", request.task_type)
            response = TaskResponse.failure(
                request,
                sender=self.agent_name,
                status=TaskResponseStatus.INTERNAL_ERROR,
                error_code=TaskErrorCode.INTERNAL_ERROR,
                error_detail=f"Internal specialist failure: {type(exc).__name__}",
            )

        # 6. Cache and return
        self._cache_response(framework_sender, request.correlation_id, response)
        return response

    def _execute_task(self, task_type: str, payload: dict[str, Any]) -> Any:
        if task_type == "inventory_bom":
            case = _clean_case_payload(payload["case"])
            context = AgentContext.model_validate(payload["context"])
            return run_inventory_bom(case, context)

        if task_type == "job_impact":
            case = _clean_case_payload(payload["case"])
            inventory = InventoryBomResult.model_validate(payload["inventory"])
            context = AgentContext.model_validate(payload["context"])
            return run_job_impact(case, inventory, context)

        if task_type == "supplier_discovery":
            case = _clean_case_payload(payload["case"])
            inventory = InventoryBomResult.model_validate(payload["inventory"])
            context = AgentContext.model_validate(payload["context"])
            return run_supplier_discovery(case, inventory, context)

        if task_type == "compatibility":
            case = _clean_case_payload(payload["case"])
            discovery = SupplierDiscoveryResult.model_validate(payload["supplier_discovery"])
            context = AgentContext.model_validate(payload["context"])
            return run_compatibility(case, discovery, context)

        if task_type == "quote_risk":
            case = _clean_case_payload(payload["case"])
            req_qty = payload.get("required_quantity")
            as_of_raw = payload.get("as_of")
            as_of = datetime.fromisoformat(as_of_raw) if as_of_raw else None
            return run_quote_risk(case, required_quantity=req_qty, as_of=as_of)

        if task_type == "recovery_planner":
            case = _clean_case_payload(payload["case"])
            as_of_raw = payload.get("as_of")
            as_of = datetime.fromisoformat(as_of_raw) if as_of_raw else None
            return run_recovery_planner(case, as_of=as_of)

        if task_type == "procurement_draft":
            case = _clean_case_payload(payload["case"])
            plan = _clean_plan_payload(payload["plan"])
            approval = ProcurementApproval.model_validate(payload["approval"])
            curr_plan_raw = payload.get("current_plan")
            curr_plan = _clean_plan_payload(curr_plan_raw) if curr_plan_raw else None
            approval_verified = bool(payload.get("approval_verified", False))
            as_of_raw = payload.get("as_of")
            as_of = datetime.fromisoformat(as_of_raw) if as_of_raw else None
            return prepare_procurement_draft(
                case,
                plan,
                approval,
                current_plan=curr_plan,
                approval_verified=approval_verified,
                as_of=as_of,
            )

        if task_type == "vendor_communication_prep":
            case = _clean_case_payload(payload["case"])
            plan_raw = payload.get("plan")
            plan = _clean_plan_payload(plan_raw) if plan_raw else None
            offer_id = payload.get("offer_id")
            as_of_raw = payload.get("as_of")
            as_of = datetime.fromisoformat(as_of_raw) if as_of_raw else None
            return prepare_vendor_communication(case, plan=plan, offer_id=offer_id, as_of=as_of)

        if task_type == "vendor_voice_call":
            case = _clean_case_payload(payload["case"])
            supplier_id = payload.get("supplier_id", "")
            phone_number = payload.get("phone_number", "")
            user_auth = bool(payload.get("user_authorized", False))
            req_qty = payload.get("requested_quantity")
            return execute_voice_rfq(
                case,
                supplier_id=supplier_id,
                phone_number=phone_number,
                user_authorized=user_auth,
                requested_qty=req_qty,
            )

        if task_type == "delivery_watch":
            plan = _clean_plan_payload(payload["plan"])
            events = payload.get("events", [])
            as_of_raw = payload.get("as_of")
            as_of = datetime.fromisoformat(as_of_raw) if as_of_raw else None
            allow_sim = bool(payload.get("allow_simulated", False))
            return run_delivery_watch(plan, events, as_of=as_of, allow_simulated=allow_sim)

        raise ValueError(f"Unsupported task_type '{task_type}'")
