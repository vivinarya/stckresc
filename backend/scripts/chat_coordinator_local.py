"""CLI to interact with the StockRescue Coordinator directly in your terminal.

Runs locally without requiring Agentverse mailboxes or network relays.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Ensure backend root on sys.path
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv
load_dotenv(BACKEND_ROOT / ".env")

from agents.coordinator.agent import run_coordinator
from agents.protocols.transport import SpecialistTransport
from services.intent.resolve_intent import resolve_intent
from services.warehouse_api.repository import RepositoryNotFound, get_repository

CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


def main():
    print(f"\n{BOLD}{CYAN}========================================================================={RESET}")
    print(f"{BOLD}{GREEN}       STOCKRESCUE — LOCAL TERMINAL COORDINATOR CHAT                      {RESET}")
    print(f"{BOLD}{CYAN}========================================================================={RESET}")
    print(f"Modes: Local in-process execution (Bypasses Agentverse network delays)")
    print(f"Commands you can try:")
    print(f"  - {BOLD}help{RESET}")
    print(f"  - {BOLD}cases{RESET} (list stored disruption cases in DB)")
    print(f"  - {BOLD}recover DEMO-STOCK-RESCUE-001{RESET}")
    print(f"  - {BOLD}exit{RESET} or {BOLD}quit{RESET}\n")

    repo = get_repository()
    # Create local transport that directly runs all specialists deterministically
    local_transport = SpecialistTransport(
        mode="local",
        sender_identity="local-terminal-coordinator",
        timeout_seconds=15.0,
    )

    while True:
        try:
            user_input = input(f"{BOLD}{YELLOW}You > {RESET}").strip()
            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "q"):
                print(f"{CYAN}Goodbye!{RESET}")
                break

            if user_input.lower() == "cases":
                cases, total = repo.list_cases()
                print(f"\n{BOLD}Found {total} disruption case(s):{RESET}")
                for c in cases:
                    print(f"  • {BOLD}{c.case_id}{RESET} (SKU: {c.affected_sku}, Budget: {c.budget} {c.currency})")
                print()
                continue

            # Resolve intent (using local rule-based or ASI:One LLM classifier)
            decision = resolve_intent(user_input)

            if decision.intent == "help":
                print(f"\n{GREEN}[Coordinator]{RESET} StockRescue can analyze a stored disruption case.")
                print("Send `recover <case_id>` to run a full multi-agent recovery assessment.\n")

            elif decision.intent == "recover_case" and decision.case_id:
                case_id = decision.case_id
                print(f"\n{CYAN}[Coordinator]{RESET} Loading case '{case_id}' and initiating swarm...")
                try:
                    case = repo.get_case(case_id)
                except RepositoryNotFound:
                    print(f"{RED}[Coordinator Error]{RESET} No stored case matches identifier '{case_id}'.\n")
                    continue

                start_time = time.time()
                try:
                    # Run full LangGraph coordinator workflow locally
                    result = run_coordinator(case, transport=local_transport)
                    elapsed = time.time() - start_time

                    print(f"{GREEN}[Coordinator Result - {elapsed:.2f}s]{RESET}")
                    print(f"  • Case: {BOLD}{case.case_id}{RESET}")
                    print(f"  • Outcome: {BOLD}{result.outcome.value.upper()}{RESET}")
                    print(f"  • Summary: {result.summary}")
                    print(f"  • Actions: {len(result.actions)} step(s) generated")
                    print(f"  • Procurement Authorized: {BOLD}NO{RESET} (Human approval required)\n")

                except Exception as exc:
                    print(f"{RED}[Coordinator Error]{RESET} Workflow failed: {exc}\n")

            else:
                print(f"\n{YELLOW}[Coordinator]{RESET} I couldn't identify a supported StockRescue request.")
                print("Type `help` or `recover <case_id>`.\n")

        except (KeyboardInterrupt, EOFError):
            print(f"\n{CYAN}Session ended.{RESET}")
            break


if __name__ == "__main__":
    main()
