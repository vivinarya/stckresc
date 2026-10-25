"""StockRescue Coordinator Agent — Agentverse Entry Point."""

try:
    from agents.coordinator.agent import agent
except ImportError:
    try:
        from agent import agent
    except ImportError:
        agent = None

if __name__ == "__main__" and agent is not None:
    agent.run()
