"""Agentverse entry point for Recovery Planner specialist."""

from typing import Any

AGENT_NAME = "stockrescue-recovery-planner"
AGENT_PORT = 8097
SEED_ENV_VAR = "AGENTVERSE_SEED_RECOVERY_PLANNER"


def create_agent(seed: str, allowed_senders: Any = None) -> Any:
    from agents.protocols.agentverse_helper import create_specialist_agent
    return create_specialist_agent(
        role="recovery_planner",
        agent_name=AGENT_NAME,
        port=AGENT_PORT,
        seed=seed,
        allowed_senders=allowed_senders,
    )


def main() -> None:
    raise SystemExit("The recovery planner specialist has been consolidated into the Quote Risk mailbox.")


if __name__ == "__main__":
    main()
