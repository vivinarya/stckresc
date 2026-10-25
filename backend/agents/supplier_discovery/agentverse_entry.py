"""Agentverse entry point for Supplier Discovery specialist."""

from typing import Any

AGENT_NAME = "stockrescue-supplier-discovery"
AGENT_PORT = 8094
SEED_ENV_VAR = "AGENTVERSE_SEED_SUPPLIER_DISCOVERY"


def create_agent(seed: str, allowed_senders: Any = None) -> Any:
    from agents.protocols.agentverse_helper import create_specialist_agent
    return create_specialist_agent(
        role="supplier_discovery",
        agent_name=AGENT_NAME,
        port=AGENT_PORT,
        seed=seed,
        allowed_senders=allowed_senders,
    )
