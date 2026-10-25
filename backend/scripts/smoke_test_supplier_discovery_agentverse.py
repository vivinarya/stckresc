"""Live smoke test for Coordinator -> Supplier Discovery Agentverse mailbox communication.

IMPORTANT SAFETY NOTE ON AGENT CONCURRENCY:
Before running this test, ensure that no standalone Coordinator agent process
(e.g., `python -m agents.coordinator.agentverse_entry`, port 8091) is running.
Two live processes sharing the same `AGENTVERSE_SEED_COORDINATOR` will race
for incoming messages on the Agentverse mailbox, potentially stealing the response
before the smoke test receives it.
"""

from __future__ import annotations

import asyncio
import os
import socket
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
from agents.inventory_bom.agent import run_inventory_bom
from agents.protocols.agentverse_transport import AgentverseTaskExchange
from agents.protocols.messages import TaskRequest, TaskResponseStatus
from shared.agent_contracts import (
    SupplierDiscoveryResult,
    build_agent_context,
)
from shared.config import get_settings
from tests.helpers import make_case


def _is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


async def run_live_supplier_discovery_smoke_test() -> bool:
    settings = get_settings()

    coord_seed = os.getenv("AGENTVERSE_SEED_COORDINATOR")
    supplier_discovery_address = os.getenv("AGENTVERSE_SUPPLIER_DISCOVERY_ADDRESS")
    configured_coord_addr = os.getenv("AGENTVERSE_COORDINATOR_ADDRESS")
    specialist_allowed = settings.get_specialist_allowed_senders()

    print("=" * 65)
    print("LIVE AGENTVERSE TRANSPORT SMOKE TEST: Coordinator -> Supplier Discovery")
    print("=" * 65)

    # 1. Environment & Config Validation
    if not coord_seed:
        print("[BLOCKER] AGENTVERSE_SEED_COORDINATOR is not set in backend/.env")
        return False

    if not supplier_discovery_address:
        print("[BLOCKER] AGENTVERSE_SUPPLIER_DISCOVERY_ADDRESS is not set in backend/.env")
        return False

    # 2. Check for conflicting live coordinator process on port 8091
    if _is_port_in_use(8091):
        print(
            "\n[BLOCKER] Port 8091 is active: a live Coordinator process is currently running.\n"
            "Stop the running Coordinator process before running this smoke test to avoid\n"
            "competing mailbox consumers racing for the same response.\n"
        )
        return False

    # 3. Create Coordinator Agent with mailbox enabled
    coord_agent = Agent(
        name="stockrescue-coordinator-supplier-discovery-smoke",
        seed=coord_seed,
        port=8099,
        mailbox=True,
    )

    print(f"\nAgentverse Identities:")
    print(f"  Coordinator Address:        {coord_agent.address}")
    print(f"  Supplier Discovery Address: {supplier_discovery_address}")

    if configured_coord_addr and coord_agent.address != configured_coord_addr:
        print(
            f"[BLOCKER] Derived coordinator address ({coord_agent.address}) does not "
            f"match AGENTVERSE_COORDINATOR_ADDRESS ({configured_coord_addr})"
        )
        return False

    # 4. Verify specialist allowlist permits coordinator
    if specialist_allowed and coord_agent.address not in specialist_allowed:
        print(
            f"[BLOCKER] Coordinator address '{coord_agent.address}' is NOT permitted by "
            f"AGENTVERSE_SPECIALIST_ALLOWED_SENDERS ({specialist_allowed})"
        )
        return False
    print(f"  Allowlist check:            Coordinator is authorized in specialist allowlist.")

    # 5. Attach AgentverseTaskExchange
    exchange = AgentverseTaskExchange(
        agent_or_identity=coord_agent,
        allowed_specialist_addresses={supplier_discovery_address},
        default_timeout=20.0,
    )
    exchange.attach_to_agent()

    # 6. Prepare synthetic case and calculate inventory result locally
    print("\nPreparing synthetic test data...")
    case = make_case()
    context = build_agent_context(case)

    # Compute inventory deterministically with existing local specialist
    inventory = run_inventory_bom(case, context)
    print(f"  Local Inventory calculated: demand={inventory.demand_quantity}, "
          f"available={inventory.available_quantity}, shortage={inventory.shortage_quantity}")

    correlation_id = str(uuid4())
    workflow_id = context.workflow_id
    trace_id = context.trace_id

    # 7. Construct typed TaskRequest matching dispatcher contract
    request = TaskRequest.create(
        task_type="supplier_discovery",
        workflow_id=workflow_id,
        trace_id=trace_id,
        sender=coord_agent.address,
        recipient=supplier_discovery_address,
        payload={
            "case": case,
            "inventory": inventory,
            "context": context,
        },
        correlation_id=correlation_id,
        timeout_seconds=20.0,
    )

    print(f"\nDispatching TaskRequest:")
    print(f"  task_type:      {request.task_type}")
    print(f"  workflow_id:    {request.workflow_id}")
    print(f"  correlation_id: {request.correlation_id}")
    print(f"  trace_id:       {request.trace_id}")
    print(f"  sender:         {request.sender}")
    print(f"  recipient:      {request.recipient}")

    # 8. Start Coordinator agent background mailbox polling task
    agent_task = asyncio.create_task(coord_agent.run_async())
    # Allow mailbox session to establish
    await asyncio.sleep(2.0)

    try:
        start_time = datetime.now(timezone.utc)
        print("\nAwaiting typed response from Supplier Discovery agent via Agentverse mailbox...")

        response = await exchange.send_and_wait_async(
            destination=supplier_discovery_address,
            request=request,
            timeout=20.0,
        )
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

        print(f"\n[RECEIVED] Remote response received in {elapsed:.2f}s:")
        print(f"  status:         {response.status}")
        print(f"  task_type:      {response.task_type}")
        print(f"  correlation_id: {response.correlation_id}")
        print(f"  workflow_id:    {response.workflow_id}")
        print(f"  trace_id:       {response.trace_id}")
        print(f"  sender:         {response.sender}")
        print(f"  recipient:      {response.recipient}")

        # 9. Verify response status and correlation integrity
        if response.status != TaskResponseStatus.OK:
            print(
                f"\n[FAILED] Response returned error status: {response.status} "
                f"(code: {response.error_code}): {response.error_detail}"
            )
            return False

        if response.task_type != "supplier_discovery":
            print(f"\n[FAILED] Wrong task type returned: {response.task_type}")
            return False

        if response.correlation_id != correlation_id:
            print(f"\n[FAILED] Correlation ID mismatch: {response.correlation_id} != {correlation_id}")
            return False

        if response.workflow_id != workflow_id:
            print(f"\n[FAILED] Workflow ID mismatch: {response.workflow_id} != {workflow_id}")
            return False

        if response.trace_id != trace_id:
            print(f"\n[FAILED] Trace ID mismatch: {response.trace_id} != {trace_id}")
            return False

        if response.recipient != coord_agent.address:
            print(f"\n[FAILED] Reciprocal recipient mismatch: {response.recipient} != {coord_agent.address}")
            return False

        if response.sender != supplier_discovery_address:
            print(f"\n[FAILED] Reciprocal sender mismatch: {response.sender} != {supplier_discovery_address}")
            return False

        result_data = response.get_result()
        if not result_data:
            print("\n[FAILED] Response payload is empty")
            return False

        discovery_result = SupplierDiscoveryResult.model_validate(result_data)
        print(f"\n[SUCCESS] Validated SupplierDiscoveryResult from remote Supplier Discovery specialist:")
        print(f"  affected_sku:                        {discovery_result.affected_sku}")
        print(f"  required_quantity:                   {discovery_result.required_quantity}")
        print(f"  discovered_offers_count:             {len(discovery_result.offers)}")
        print(f"  excluded_failed_supplier_offers:     {discovery_result.excluded_failed_supplier_offers}")
        print(f"  excluded_unavailable_or_stale_offers: {discovery_result.excluded_unavailable_or_stale_offers}")
        print(f"  status:                              {discovery_result.status.value}")
        print(f"  issues:                              {[i.code for i in discovery_result.issues]}")

        print("\nVerified: Real remote Agentverse request/response exchange succeeded.")
        return True

    except Exception as exc:
        print(f"\n[ERROR] Remote Supplier Discovery exchange failed: {type(exc).__name__}: {exc}")
        return False

    finally:
        agent_task.cancel()
        await asyncio.sleep(0.5)


if __name__ == "__main__":
    success = asyncio.run(run_live_supplier_discovery_smoke_test())
    sys.exit(0 if success else 1)
