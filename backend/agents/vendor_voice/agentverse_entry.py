"""Agentverse entry point for Vendor Voice specialist."""

from typing import Any

AGENT_NAME = "stockrescue-vendor-voice"
AGENT_PORT = 8099
SEED_ENV_VAR = "AGENTVERSE_SEED_VENDOR_VOICE"


def create_agent(seed: str, allowed_senders: Any = None) -> Any:
    from agents.protocols.agentverse_helper import create_specialist_agent
    return create_specialist_agent(
        role="vendor_voice",
        agent_name=AGENT_NAME,
        port=AGENT_PORT,
        seed=seed,
        allowed_senders=allowed_senders,
    )
