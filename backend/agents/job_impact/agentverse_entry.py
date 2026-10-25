"""Agentverse entry point for Job Impact specialist."""

from typing import Any

AGENT_NAME = "stockrescue-job-impact"
AGENT_PORT = 8093
SEED_ENV_VAR = "AGENTVERSE_SEED_JOB_IMPACT"


def create_agent(seed: str, allowed_senders: Any = None) -> Any:
    from agents.protocols.agentverse_helper import create_specialist_agent
    return create_specialist_agent(
        role="job_impact",
        agent_name=AGENT_NAME,
        port=AGENT_PORT,
        seed=seed,
        allowed_senders=allowed_senders,
    )
