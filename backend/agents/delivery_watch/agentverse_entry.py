"""Agentverse entry point for Delivery Watch specialist."""

from typing import Any

AGENT_NAME = "stockrescue-delivery-watch"
AGENT_PORT = 8100
SEED_ENV_VAR = "AGENTVERSE_SEED_DELIVERY_WATCH"


def create_agent(seed: str, allowed_senders: Any = None) -> Any:
    from agents.protocols.agentverse_helper import create_specialist_agent
    return create_specialist_agent(
        role="delivery_watch",
        agent_name=AGENT_NAME,
        port=AGENT_PORT,
        seed=seed,
        allowed_senders=allowed_senders,
    )
