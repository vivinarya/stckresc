from __future__ import annotations
import sys
import time
from decimal import Decimal
from pathlib import Path

CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

def run_demo():
    print(f"\n{BOLD}{CYAN}========================================================================={RESET}")
    print(f"{BOLD}{GREEN}       STOCKRESCUE — AUTONOMOUS MULTI-AGENT PROCUREMENT DEMO              {RESET}")
    print(f"{BOLD}{CYAN}========================================================================={RESET}\n")
    print(f"{BOLD}Customer Case:{RESET} Priya's Bridal Boutique (Chennai)")
    print(f"{BOLD}Incident:{RESET} Primary weaver cancelled (Loom breakdown)")
    print(f"{BOLD}Requirement:{RESET} 30 Authentic Kanchipuram Silk Sarees (BIS / Silk Mark Certified)")
    print(f"{BOLD}Hard Deadline:{RESET} Friday 12:00 PM (Wedding Event)")
    print(f"{BOLD}Budget Ceiling:{RESET} INR 2,00,000\n")
    print(f"{YELLOW}Initiating Autonomous Multi-Agent Swarm...{RESET}\n")
    time.sleep(1.0)

    # Step 1: Coordinator & Inventory/BOM
    print(f"{BOLD}[1/4] Coordinator & Inventory Agent{RESET}")
    print(f"  +- Status: Analyzing inventory & BOM shortfall...")
    time.sleep(0.7)
    print(f"  +- Affected SKU: {BOLD}SILK-KANCHI-001{RESET}")
    print(f"  +- Stock on Hand: {RED}0 units{RESET} (100% shortage)")
    print(f"  \- Demand Target: {GREEN}30 units needed by Friday 12:00 PM{RESET}\n")
    time.sleep(0.8)

    # Step 2: Scout Agent (Supplier Discovery)
    print(f"{BOLD}[2/4] Supplier Discovery Agent ('Scout'){RESET}")
    print(f"  +- Status: Querying distributed supplier catalogs & regional hubs...")
    time.sleep(0.9)
    print(f"  +- Found {BOLD}4 candidate supplier offers:{RESET}")
    print(f"  |   1. {BOLD}Mumbai Silk House:{RESET} 100 units | INR 4,500/unit | 1-day express air delivery")
    print(f"  |   2. {BOLD}Surat Textile Wholesalers:{RESET} 40 units | INR 3,800/unit | 2-day delivery (MOQ: 50)")
    print(f"  |   3. {BOLD}Delhi Wholesale Hub:{RESET} 200 units | INR 4,200/unit | 3-day surface delivery")
    print(f"  |   4. {BOLD}Local Chennai Bazaar:{RESET} 60 units | INR 5,500/unit | Same-day courier (No BIS)")
    print(f"  \- Passing candidate options to Validator Agent...\n")
    time.sleep(0.9)

    # Step 3: Validator Agent (Compatibility & Deterministic Risk)
    print(f"{BOLD}[3/4] Compatibility & Risk Validator ('Validator'){RESET}")
    print(f"  +- Status: Applying deterministic business rules & hard constraints...")
    time.sleep(0.8)
    print(f"  +- {RED}[X] Delhi Wholesale Hub:{RESET} {BOLD}REJECTED{RESET} (Delivery Timeline Breach)")
    print(f"  |    \- Reason: Lead time 3 days arrives Saturday. Misses Friday wedding deadline!")
    time.sleep(0.6)
    print(f"  +- {RED}[X] Local Chennai Bazaar:{RESET} {BOLD}REJECTED{RESET} (Quality Certification Failure)")
    print(f"  |    \- Reason: No Silk Mark / BIS verification. Unacceptable counterfeit risk.")
    time.sleep(0.6)
    print(f"  +- {RED}[X] Surat Textile Wholesalers:{RESET} {BOLD}REJECTED{RESET} (MOQ Violation)")
    print(f"  |    \- Reason: Minimum Order Quantity is 50. Buyer only authorized 30 units.")
    time.sleep(0.6)
    print(f"  \- {GREEN}[OK] Mumbai Silk House:{RESET} {BOLD}VERIFIED & APPROVED{RESET}")
    print(f"       \- Certified: Silk Mark Grade-A | Lead time: 24h (Arrives Thursday 4 PM) | MOQ: 1\n")
    time.sleep(0.9)

    # Step 4: Recovery Planner Agent
    print(f"{BOLD}[4/4] Quote & Recovery Planner Agent ('Planner'){RESET}")
    print(f"  +- Status: Generating cost model & purchase order draft...")
    time.sleep(0.7)
    qty = 30
    unit_price = Decimal("4500.00")
    total_cost = qty * unit_price
    budget = Decimal("200000.00")
    savings = budget - total_cost

    print(f"  +- Selected Supplier: {BOLD}Mumbai Silk House{RESET}")
    print(f"  +- Quantity to Order: {qty} sarees")
    print(f"  +- Unit Price: INR {unit_price:,.2f}")
    print(f"  +- Total Purchase Commitment: {GREEN}INR {total_cost:,.2f}{RESET}")
    print(f"  +- Budget Savings: {GREEN}INR {savings:,.2f} under budget{RESET}")
    print(f"  +- Estimated Arrival: {BOLD}Thursday 4:00 PM{RESET} (20 hours ahead of event)")
    print(f"  \- Risk Score: {GREEN}0.02 (Minimal Risk){RESET}\n")
    time.sleep(0.5)

    print(f"{BOLD}{GREEN}========================================================================={RESET}")
    print(f"{BOLD}{GREEN}                     FINAL RECOVERY PLAN READY                            {RESET}")
    print(f"{BOLD}{GREEN}========================================================================={RESET}")
    print(f"{BOLD}Action Required:{RESET} Click Approve to execute cryptographic Purchase Order")
    print(f"Outcome: {BOLD}SUCCESSFUL DISRUPTION RECOVERY{RESET}\n")

if __name__ == '__main__':
    run_demo()
