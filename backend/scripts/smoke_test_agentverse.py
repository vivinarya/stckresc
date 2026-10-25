"""Live smoke test for Coordinator -> Inventory Agentverse mailbox communication."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Ensure backend root on sys.path
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv
load_dotenv(BACKEND_ROOT / ".env")

from uagents import Agent
from agents.protocols.agentverse_transport import AgentverseTaskExchange
from agents.protocols.messages import TaskRequest, TaskResponseStatus
from tests.helpers import make_case
from shared.agent_contracts import build_agent_context, InventoryBomResult


async def run_live_smoke_test():
    coord_seed = os.getenv("AGENTVERSE_SEED_COORDINATOR")
    inventory_address = os.getenv("AGENTVERSE_INVENTORY_BOM_ADDRESS")
    if not coord_seed or not inventory_address:
        print(f"BLOCKER: AGENTVERSE_SEED_COORDINATOR or AGENTVERSE_INVENTORY_BOM_ADDRESS missing in .env")
        return False

    print("=" * 60)
    print("LIVE AGENTVERSE TRANSPORT SMOKE TEST: Coordinator -> Inventory")
    print("=" * 60)

    # 1. Create Coordinator Agent with mailbox enabled
    coord_agent = Agent(
        name="stockrescue-coordinator-smoke",
        seed=coord_seed,
        port=8099,
        mailbox=True,
    )
    print(f"Coordinator address: {coord_agent.address}")
    print(f"Inventory address:   {inventory_address}")

    exchange = AgentverseTaskExchange(
        agent_or_identity=coord_agent,
        allowed_specialist_addresses={inventory_address},
        default_timeout=20.0,
    )
    exchange.attach_to_agent()

    # 2. Prepare synthetic case and TaskRequest
    case = make_case()
    context = build_agent_context(case)
    correlation_id = str(uuid4())
    workflow_id = context.workflow_id
    trace_id = context.trace_id

    request = TaskRequest.create(
        task_type="inventory_bom",
        workflow_id=workflow_id,
        trace_id=trace_id,
        sender=coord_agent.address,
        recipient=inventory_address,
        payload={"case": case, "context": context},
        correlation_id=correlation_id,
        timeout_seconds=20.0,
    )

    print(f"\nSending TaskRequest:")
    print(f"  task_type:      {request.task_type}")
    print(f"  workflow_id:    {request.workflow_id}")
    print(f"  correlation_id: {request.correlation_id}")
    print(f"  trace_id:       {request.trace_id}")
    print(f"  sender:         {request.sender}")
    print(f"  recipient:      {request.recipient}")

    # 3. Start Coordinator agent background mailbox polling task
    agent_task = asyncio.create_task(coord_agent.run_async())
    # Give mailbox 2 seconds to establish session
    await asyncio.sleep(2.0)

    try:
        start_time = datetime.now(timezone.utc)
        print("\nAwaiting response via Agentverse mailbox...")
        response = await exchange.send_and_wait_async(
            destination=inventory_address,
            request=request,
            timeout=20.0,
        )
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

        print(f"\n[SUCCESS] Received response in {elapsed:.2f}s:")
        print(f"  status:         {response.status}")
        print(f"  task_type:      {response.task_type}")
        print(f"  correlation_id: {response.correlation_id}")
        print(f"  workflow_id:    {response.workflow_id}")
        print(f"  trace_id:       {response.trace_id}")
        print(f"  sender:         {response.sender}")
        print(f"  recipient:      {response.recipient}")

        if response.status == TaskResponseStatus.OK:
            result_data = response.get_result()
            bom_result = InventoryBomResult.model_validate(result_data)
            print(f"\nValidated InventoryBomResult:")
            print(f"  affected_sku:       {bom_result.affected_sku}")
            print(f"  demand_quantity:    {bom_result.demand_quantity}")
            print(f"  available_quantity: {bom_result.available_quantity}")
            print(f"  shortage_quantity:  {bom_result.shortage_quantity}")
            print(f"  status:             {bom_result.status.value}")
            return True
        else:
            print(f"\n[FAILED] Response status: {response.status}, error_code: {response.error_code}, detail: {response.error_detail}")
            return False

    except Exception as exc:
        print(f"\n[ERROR] Remote exchange failed: {type(exc).__name__}: {exc}")
        return False
    finally:
        agent_task.cancel()
        await asyncio.sleep(0.5)


if __name__ == "__main__":
    success = asyncio.run(run_live_smoke_test())
    sys.exit(0 if success else 1)
