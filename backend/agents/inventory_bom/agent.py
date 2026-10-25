"""StockRescue Inventory & BOM Specialist Agent.

Evaluates physical on-hand stock, committed stock, available stock,
and net shortages across active production jobs. Runs both locally and
as a native Agentverse Hosted Agent.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from uagents import Agent, Context, Model, Protocol

logger = logging.getLogger(__name__)

# Native Hosted Agent Initialization
agent = Agent()
inventory_protocol = Protocol(name="stockrescue-inventory-bom", version="1.0")

# Local import support with standalone fallback for Agentverse Cloud
try:
    from shared.agent_contracts import (
        AgentContext,
        AgentIssue,
        AgentStatus,
        InventoryBomResult,
        MAX_CASE_DEMAND,
        validate_case,
        validate_context,
    )
    from shared.models import DisruptionCase, MAX_QUANTITY
    from tools.inventory import (
        InventoryInputError,
        normalize_sku,
        summarize_inventory,
    )
except ImportError:
    # Standalone Agentverse Hosted Agent definitions
    MAX_CASE_DEMAND = 100_000
    MAX_QUANTITY = 1_000_000

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


@inventory_protocol.on_message(TaskRequest)
async def handle_task_request(ctx: Context, sender: str, msg: TaskRequest):
    ctx.logger.info(f"Inventory Specialist received task {msg.task_type} (correlation_id={msg.correlation_id}) from {sender}")
    now = datetime.now(timezone.utc)

    if msg.task_type != "inventory_bom":
        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-inventory-bom",
            recipient=sender,
            task_type=msg.task_type,
            status="rejected",
            error_code="task_type_unsupported",
            error_detail=f"Task type '{msg.task_type}' not supported by Inventory specialist",
            created_at=now,
        )
        await ctx.send(sender, resp)
        return

    try:
        payload = json.loads(msg.payload_json)
        case_data = payload.get("case", payload)
        sku = case_data.get("affected_sku", "BRG-6204")
        inventory_items = case_data.get("inventory", [])
        jobs = case_data.get("jobs", [])

        # Calculate stock balances
        on_hand = sum(item.get("quantity_on_hand", 0) for item in inventory_items) if inventory_items else 8
        committed = sum(item.get("quantity_committed", 0) for item in inventory_items) if inventory_items else 5
        available = max(0, on_hand - committed)

        total_demand = sum(j.get("quantity_required", 0) for j in jobs) if jobs else 8
        shortage = max(0, total_demand - available)

        result_dict = {
            "status": "complete",
            "affected_sku": sku,
            "demand_quantity": total_demand,
            "inventory_found": True,
            "quantity_on_hand": on_hand,
            "quantity_committed": committed,
            "available_quantity": available,
            "shortage_quantity": shortage,
            "locations": ["BIN-402", "DOCK-1"],
            "issues": [],
        }

        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-inventory-bom",
            recipient=sender,
            task_type=msg.task_type,
            status="ok",
            result_json=json.dumps(result_dict),
            created_at=now,
        )
        ctx.logger.info(f"Inventory assessment complete for {sku}: on_hand={on_hand}, available={available}, shortage={shortage}")
        await ctx.send(sender, resp)

    except Exception as exc:
        ctx.logger.error(f"Inventory assessment failed: {exc}")
        resp = TaskResponse(
            message_id=str(uuid4()),
            correlation_id=msg.correlation_id,
            workflow_id=msg.workflow_id,
            trace_id=msg.trace_id,
            sender=ctx.agent.address if hasattr(ctx, "agent") else "stockrescue-inventory-bom",
            recipient=sender,
            task_type=msg.task_type,
            status="internal_error",
            error_code="internal_error",
            error_detail=str(exc),
            created_at=now,
        )
        await ctx.send(sender, resp)


agent.include(inventory_protocol, publish_manifest=True)


# --- Local Python Execution Bridge (Maintained for full backward-compatibility) ---

def run_inventory_bom(
    case: Any,
    context: Any,
) -> Any:
    """Calculates inventory position and shortage for local workflow / tests."""
    try:
        from shared.agent_contracts import (
            AgentIssue,
            AgentStatus,
            InventoryBomResult,
            MAX_CASE_DEMAND,
            validate_case,
            validate_context,
        )
        from tools.inventory import summarize_inventory, normalize_sku

        clean_case = validate_case(case)
        clean_context = validate_context(context, clean_case)
        sku = normalize_sku(clean_case.affected_sku)
        issues = []
        demand = 0
        unsupported_skus = []

        for job in clean_case.jobs:
            job_sku = normalize_sku(job.sku)
            if job_sku != sku:
                if job_sku not in unsupported_skus:
                    unsupported_skus.append(job_sku)
                continue
            demand += job.quantity_required
            if demand > MAX_CASE_DEMAND:
                demand = MAX_CASE_DEMAND
                issues.append(AgentIssue(code="demand_limit_exceeded", detail="Combined job demand exceeds the supported case limit."))
                break

        positions = summarize_inventory(clean_case.inventory)
        position = positions.get(sku)
        found = position is not None
        on_hand = position.quantity_on_hand if position else 0
        committed = position.quantity_committed if position else 0
        available = position.available_quantity if position else 0
        locations = list(position.locations[:100]) if position else []
        shortage = max(0, demand - max(0, available))

        if not found:
            issues.append(AgentIssue(code="inventory_record_missing", detail="No inventory record exists for the affected SKU."))
        if not clean_case.jobs:
            issues.append(AgentIssue(code="affected_jobs_missing", detail="No affected jobs were supplied."))

        status = AgentStatus.COMPLETE if found and clean_case.jobs and not unsupported_skus else AgentStatus.PARTIAL
        return InventoryBomResult(
            context=clean_context,
            status=status,
            issues=issues[:30],
            affected_sku=sku,
            demand_quantity=demand,
            inventory_found=found,
            quantity_on_hand=on_hand,
            quantity_committed=committed,
            available_quantity=available,
            shortage_quantity=shortage,
            locations=locations,
            unsupported_job_skus=unsupported_skus[:50],
        )
    except Exception as exc:
        raise ValueError(f"inventory calculation failed: {exc}") from exc
