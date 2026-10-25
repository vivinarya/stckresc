"""Helper to configure and launch independent Agentverse specialist entry points."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Set

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol

from agents.protocols.dispatcher import SpecialistDispatcher
from agents.protocols.messages import TaskRequest, TaskResponse

logger = logging.getLogger(__name__)


def create_specialist_agent(
    role: str,
    agent_name: str,
    port: int,
    seed: str,
    allowed_senders: Set[str] | None = None,
    permitted_tasks: Set[str] | None = None,
) -> Agent:
    """Creates a configured uAgents Agent with a typed TaskRequest protocol."""
    if not isinstance(seed, str) or not 16 <= len(seed) <= 256 or not seed.isprintable():
        raise ValueError(f"Seed for {agent_name} is invalid (must be 16-256 printable chars)")

    if allowed_senders is None:
        raw_allowed = os.getenv("AGENTVERSE_SPECIALIST_ALLOWED_SENDERS", "")
        allowed_senders = {s.strip() for s in raw_allowed.split(",") if s.strip()}

    dispatcher = SpecialistDispatcher(
        role=role,
        agent_name=agent_name,
        allowed_senders=allowed_senders,
        permitted_tasks=permitted_tasks,
    )

    agent = Agent(
        name=agent_name,
        seed=seed,
        port=port,
        mailbox=True,
        publish_agent_details=True,
    )

    protocol = Protocol(name=f"stockrescue-{role}", version="1.0")

    @protocol.on_message(TaskRequest)
    async def handle_task_request(ctx: Context, sender: str, msg: TaskRequest) -> None:
        ctx.logger.info(
            "Received task request %s from framework sender=%s (correlation_id=%s)",
            msg.task_type,
            sender,
            msg.correlation_id,
        )
        response = dispatcher.dispatch(sender, msg)
        await ctx.send(sender, response)

    agent.include(protocol, publish_manifest=True)
    return agent


def run_specialist_main(
    role: str,
    agent_name: str,
    default_port: int,
    seed_env_var: str,
) -> None:
    """Standard CLI entry point loading environment variables and starting the agent."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(env_path)

    seed = os.getenv(seed_env_var, "")
    if not seed:
        raise SystemExit(
            f"Set {seed_env_var} in backend/.env before starting {agent_name}."
        )

    port = int(os.getenv(f"{seed_env_var}_PORT", str(default_port)))
    agent = create_specialist_agent(role, agent_name, port, seed)
    agent.run()
