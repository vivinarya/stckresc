"""Agentverse entry point for Inventory & BOM specialist."""

from typing import Any

AGENT_NAME = "stockrescue-inventory-bom"
AGENT_PORT = 8092
SEED_ENV_VAR = "AGENTVERSE_SEED_INVENTORY_BOM"


def create_agent(seed: str, allowed_senders: Any = None) -> Any:
    from agents.protocols.agentverse_helper import create_specialist_agent
    return create_specialist_agent(
        role="inventory_bom",
        agent_name=AGENT_NAME,
        port=AGENT_PORT,
        seed=seed,
        allowed_senders=allowed_senders,
    )
