"""Agentverse entry point for Procurement specialist."""

from typing import Any

AGENT_NAME = "stockrescue-procurement"
AGENT_PORT = 8098
SEED_ENV_VAR = "AGENTVERSE_SEED_PROCUREMENT"


def create_agent(seed: str, allowed_senders: Any = None) -> Any:
    from agents.protocols.agentverse_helper import create_specialist_agent
    return create_specialist_agent(
        role="procurement",
        agent_name=AGENT_NAME,
        port=AGENT_PORT,
        seed=seed,
        allowed_senders=allowed_senders,
    )
