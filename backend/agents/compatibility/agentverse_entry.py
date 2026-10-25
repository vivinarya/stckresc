"""Agentverse entry point for Compatibility specialist."""

from typing import Any

AGENT_NAME = "stockrescue-compatibility"
AGENT_PORT = 8095
SEED_ENV_VAR = "AGENTVERSE_SEED_COMPATIBILITY"


def create_agent(seed: str, allowed_senders: Any = None) -> Any:
    from agents.protocols.agentverse_helper import create_specialist_agent
    return create_specialist_agent(
        role="compatibility",
        agent_name=AGENT_NAME,
        port=AGENT_PORT,
        seed=seed,
        allowed_senders=allowed_senders,
    )


def main() -> None:
    raise SystemExit("The compatibility specialist has been consolidated into the Supplier Discovery mailbox.")


if __name__ == "__main__":
    main()
