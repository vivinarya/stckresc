"""Agentverse entry point for Quote Risk specialist."""

from typing import Any

AGENT_NAME = "stockrescue-quote-risk"
AGENT_PORT = 8096
SEED_ENV_VAR = "AGENTVERSE_SEED_QUOTE_RISK"


def create_agent(seed: str, allowed_senders: Any = None) -> Any:
    from agents.protocols.agentverse_helper import create_specialist_agent
    return create_specialist_agent(
        role="quote_risk",
        agent_name=AGENT_NAME,
        port=AGENT_PORT,
        seed=seed,
        allowed_senders=allowed_senders,
    )
