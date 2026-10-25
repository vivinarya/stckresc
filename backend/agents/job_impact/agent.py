"""StockRescue Job Impact Specialist Agent.

Assesses at-risk production jobs, priority allocations, uncovered demand,
and delivery deadline risks. Runs locally and as a native Agentverse Hosted Agent.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from uagents import Agent, Context, Model, Protocol

logger = logging.getLogger(__name__)

# Native Hosted Agent Initialization
agent = Agent()
job_impact_protocol = Protocol(name="stockrescue-job-impact", version="1.0")

# Local import support with standalone fallback for Agentverse Cloud
try:
    from shared.agent_contracts import (
        AffectedJob,
        AgentContext,
        AgentIssue,
        AgentStatus,
        InventoryBomResult,
        JobImpactResult,
        MAX_CASE_DEMAND,
        validate_case,
        validate_context,
    )
    from shared.models import DisruptionCase
    from tools.inventory import normalize_sku
except ImportError:
    MAX_CASE_DEMAND = 100_000

    def normalize_sku(value: str) -> str:
        return value.strip().upper() if isinstance(value, str) else ""

    class DisruptionCase:
        pass


# --- uAgents Remote Task Request / Response Schemas ---

class TaskRequest(Model):
    schema_version: str = "1.0"
    message_id: str
    correlation_id: str
    workflow_id: str
    trace_id: str
    sender: str
    recipient: str
    task_type: str
    created_at: datetime
    deadline_at: datetime
    payload_json: str


class TaskResponse(Model):
    schema_version: str = "1.0"
    message_id: str
    correlation_id: str
    workflow_id: str
    trace_id: str
    sender: str
    recipient: str
    task_type: str
    status: str
    result_json: Optional[str] = None
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    created_at: datetime


@job_impact_protocol.on_message(TaskRequest)
async def handle_task_request(ctx: Context, sender: str, msg: TaskRequest):
    ctx.logger.info(f"Job Impact Specialist received task {msg.task_type} (correlation_id={msg.correlation_id}) from {sender}")
    now = datetime.now(timezone.utc)

    if msg.task_type != "job_impact":
        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-job-impact",
            recipient=sender,
            task_type=msg.task_type,
            status="rejected",
            error_code="task_type_unsupported",
            error_detail=f"Task type '{msg.task_type}' not supported by Job Impact specialist",
            created_at=now,
        )
        await ctx.send(sender, resp)
        return

    try:
        payload = json.loads(msg.payload_json)
        case_data = payload.get("case", payload)
        sku = case_data.get("affected_sku", "BRG-6204")
        inv_data = payload.get("inventory", {})
        available_stock = inv_data.get("available_quantity", 3)
        jobs = case_data.get("jobs", [])

        remaining = available_stock
        output_jobs = []
        total_demand = 0
        uncovered_total = 0

        for j in jobs:
            req = j.get("quantity_required", 0)
            total_demand += req
            covered = min(req, remaining)
            remaining -= covered
            uncovered = req - covered
            uncovered_total += uncovered
            output_jobs.append({
                "job_id": j.get("job_id"),
                "sku": sku,
                "quantity_required": req,
                "required_by": str(j.get("required_by")),
                "priority": j.get("priority", 1),
                "covered_from_stock": covered,
                "uncovered_quantity": uncovered,
                "deadline_risk_from_delay": uncovered > 0,
            })

        result_dict = {
            "status": "complete",
            "affected_sku": sku,
            "jobs": output_jobs,
            "total_demand": total_demand,
            "inventory_available": available_stock,
            "uncovered_demand": uncovered_total,
            "earliest_deadline": output_jobs[0]["required_by"] if output_jobs else None,
            "issues": [],
        }

        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-job-impact",
            recipient=sender,
            task_type=msg.task_type,
            status="ok",
            result_json=json.dumps(result_dict),
            created_at=now,
        )
        ctx.logger.info(f"Job impact analysis complete: {len(output_jobs)} jobs analyzed, uncovered={uncovered_total}")
        await ctx.send(sender, resp)

    except Exception as exc:
        ctx.logger.error(f"Job impact analysis failed: {exc}")
        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-job-impact",
            recipient=sender,
            task_type=msg.task_type,
            status="internal_error",
            error_code="internal_error",
            error_detail=str(exc),
            created_at=now,
        )
        await ctx.send(sender, resp)


agent.include(job_impact_protocol, publish_manifest=True)


# --- Local Python Execution Bridge (Maintained for full backward-compatibility) ---

def run_job_impact(
    case: Any,
    inventory: Any,
    context: Any,
) -> Any:
    """Evaluates production job priorities and stock coverage for local workflow / tests."""
    from shared.agent_contracts import (
        AffectedJob,
        AgentIssue,
        AgentStatus,
        JobImpactResult,
        MAX_CASE_DEMAND,
        validate_case,
        validate_context,
    )
    from tools.inventory import normalize_sku

    clean_case = validate_case(case)
    clean_context = validate_context(context, clean_case)
    sku = normalize_sku(clean_case.affected_sku)
    issues = []
    supported_jobs = []
    unsupported_skus = []

    for job in clean_case.jobs:
        job_sku = normalize_sku(job.sku)
        if job_sku != sku:
            if job_sku not in unsupported_skus:
                unsupported_skus.append(job_sku)
            continue
        supported_jobs.append(job)

    if not supported_jobs:
        issues.append(AgentIssue(code="affected_jobs_missing", detail="No jobs require the affected SKU."))
    if unsupported_skus:
        issues.append(AgentIssue(code="multiple_skus_unsupported", detail="Jobs requiring other SKUs need separate recovery cases."))

    ordered_jobs = sorted(
        supported_jobs,
        key=lambda job: (job.required_by, -job.priority, job.job_id),
    )
    remaining_stock = max(0, inventory.available_quantity) if hasattr(inventory, "available_quantity") else 0
    output_jobs = []
    total_demand = 0
    uncovered_total = 0

    for job in ordered_jobs:
        total_demand += job.quantity_required
        covered = min(job.quantity_required, remaining_stock)
        remaining_stock -= covered
        uncovered = job.quantity_required - covered
        uncovered_total += uncovered
        delay_risk = None
        if clean_case.delayed_until is not None:
            delay_risk = uncovered > 0 and clean_case.delayed_until > job.required_by
        output_jobs.append(
            AffectedJob(
                job_id=job.job_id,
                sku=sku,
                quantity_required=job.quantity_required,
                required_by=job.required_by,
                priority=job.priority,
                covered_from_stock=covered,
                uncovered_quantity=uncovered,
                deadline_risk_from_delay=delay_risk,
            )
        )

    status = AgentStatus.COMPLETE if supported_jobs else AgentStatus.BLOCKED
    return JobImpactResult(
        context=clean_context,
        status=status,
        issues=issues[:30],
        affected_sku=sku,
        jobs=output_jobs,
        total_demand=total_demand,
        inventory_available=inventory.available_quantity if hasattr(inventory, "available_quantity") else 0,
        uncovered_demand=uncovered_total,
        earliest_deadline=min((job.required_by for job in supported_jobs), default=None),
        unsupported_job_skus=unsupported_skus[:50],
    )
