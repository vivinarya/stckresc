# StockRescue Coordinator Agent

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Autonomous multi-agent orchestrator for industrial supply disruptions, part shortages, and critical factory downtime mitigation.

---

## 1. Executive Overview & Problem Statement
Modern manufacturing facilities, automated assembly plants, and critical infrastructure systems face devastating losses when a key mechanical or electrical component fails and no replacement is on hand. Plant downtime frequently costs between $10,000 and $250,000 per hour. Traditional manual procurement—involving phone calls, email chains, distributor catalog inquiries, and offline spreadsheet comparisons—requires days to resolve what must be addressed in hours.

The **StockRescue Coordinator** is an autonomous, decentralized orchestrator built on the Fetch.ai uAgents framework and native Agentverse cloud hosting. Acting as the operational brain of the StockRescue swarm, it:
1. Ingests natural language disruption reports or structured emergency tickets directly from plant managers via **ASI:One**.
2. Coordinates seven autonomous specialist agents to assess inventory, calculate downtime penalties, discover certified replacement suppliers, score quote risk, generate purchase orders, log supplier communications, and track logistics.
3. Synthesizes multi-criteria recovery options (Fast-Track Expedited, Balanced Recommended, Cost-Optimized) rendered as interactive, single-click decision cards.
4. Executes non-custodial, cryptographic monetization via the **Agent Payment Protocol** (0.50 USDC per verified recovery audit pack).

---

## 2. Architecture & Event-Driven Execution Model
The Coordinator operates strictly on an **event-driven, zero-idle sleep model**:
- **0% Idle CPU Consumption**: Does not utilize polling loops (`@agent.on_interval`). The agent's micro-container remains dormant until an inbound protocol message triggers the runtime.
- **Fast Execution & Sub-Second Latency**: Wakes up in ~50ms upon message arrival, executes deterministic graph orchestration or LLM intent parsing via `asi1-mini`, emits outbound messages/cards, and immediately returns to sleep.
- **Quota Conservation**: Maximizes the Fetch.ai free tier quota (~17.2 compute hours), supporting over 100,000 full recovery triage cycles on a single allocation.

### Swarm Interaction Flow
```
                   +-----------------------------+
                   |     ASI:One Chat / User     |
                   +--------------+--------------+
                                  |
                                  | (Agent Chat Protocol / ACP)
                                  v
                   +-----------------------------+
                   |   StockRescue Coordinator   |
                   +--------------+--------------+
                                  |
         +------------------------+------------------------+
         |                        |                        |
         v                        v                        v
+-----------------+      +-----------------+      +-----------------+
|  Inventory BOM  |      |   Job Impact    |      |    Suppliers    |
|   Specialist    |      |   Specialist    |      |   Specialist    |
+--------+--------+      +--------+--------+      +--------+--------+
         |                        |                        |
         +------------------------+------------------------+
                                  |
         +------------------------+------------------------+
         |                        |                        |
         v                        v                        v
+-----------------+      +-----------------+      +-----------------+
|   Quote Risk    |      |   Procurement   |      |  Delivery Watch |
|   Specialist    |      |   Specialist    |      |   Specialist    |
+-----------------+      +-----------------+      +-----------------+
```

---

## 3. Communication Protocols & Message Schemas

### A. Agent Chat Protocol (ACP)
- **Namespace**: `uagents_core.contrib.protocols.chat`
- **Inbound Message**: `ChatMessage` containing `TextContent(text="recover DEMO-STOCK-RESCUE-001")`.
- **Outbound Acknowledgement**: `ChatAcknowledgement` confirming receipt and processing status.
- **Outbound Metadata Cards**: Emitted as `MetadataContent` with typed payloads:
  - `DetailCard`: Executive triage summary displaying impacted line, estimated downtime cost, shortage count, and lead-time constraints.
  - `CarouselCard`: Comparative multi-strategy cards (Expedite vs Balanced vs Cost-Save) with action buttons.
  - `ReviewCard`: Verified purchase order and audit report details.

### B. Agent Payment Protocol (USDC Monetization)
- **Namespace**: `agent_payment_protocol` (Role: `seller`)
- **Protocol Flow**:
  1. Client sends payment initialization request or clicks "Unlock Verified Audit Pack".
  2. Coordinator issues `RequestPayment`:
     - Amount: `0.50 USDC`
     - Network / Token: Multi-chain Base / Polygon / Arbitrum USDC
     - Expiry: 3600 seconds (1 hour)
  3. Client wallet executes transaction and returns `CommitPayment` with transaction signature.
  4. Coordinator verifies cryptographic proof, updates entitlement records in Supabase PostgreSQL, and issues `CompletePayment` along with the full unredacted audit report.

### C. Inter-Specialist Task Protocol
- **Inbound/Outbound Envelope**: `TaskRequest` and `TaskResponse`
- **Fields**:
  - `task_id` (str): Unique UUID correlation tracker.
  - `task_type` (str): Specific specialist subtask (`inventory_bom`, `job_impact`, `supplier_discovery`, `quote_risk`, `procurement_plan`, `delivery_watch`).
  - `payload` (dict): Domain-specific parameters.

---

## 4. Deterministic Logic & Decision Synthesis
1. **Shortage Calculation**: Verifies net deficit: $\text{Shortage} = \max(0, \text{Required} - \text{Warehouse Stock})$.
2. **Impact Severity Scoring**: Evaluates production line priority (Tier 1 Automotive/Aerospace vs Tier 3 Packaging), calculating hourly penalty costs:
   $$\text{Financial Risk} = \text{Downtime Hours} \times \text{Hourly Rate} + \text{SLA Penalties}$$
3. **Multi-Plan Optimization**:
   - **Plan A (Speed Expedite)**: Lowest lead time, premium freight, guaranteed OEM-certified compatibility.
   - **Plan B (Balanced Recommended)**: Optimal cost-to-lead-time ratio with verified Tier-1 distributor stock.
   - **Plan C (Budget Optimized)**: Standard ground transit, bulk pricing discounts, secondary qualified manufacturer.

---

## 5. Security, Provenance & Operational Guardrails
- **Human-in-the-Loop Safeguard**: The Coordinator autonomously aggregates and evaluates plans, but **never executes live financial commitments** without explicit operator confirmation.
- **Strict Role Enforcement**: Agent Payment Protocol explicitly operates with `role="seller"`, ensuring deterministic protocol state-machine transitions.
- **Non-Custodial Escrow**: Uses verified Skyfire / Web3 payment channels without holding custodial private keys.
- **Data Privacy**: Enterprise ERP database credentials and private network addresses are stored strictly in protected environment secrets, never exposed over public chat channels.

---

## 6. Starter Prompts & Operator Usage
Operators interacting with `@stockrescue` in ASI:One can use the following starter prompts:
1. `recover DEMO-STOCK-RESCUE-001`: Triggers end-to-end triage for sample industrial pump failure.
2. `Check stockout risk and find replacement suppliers for pump bearings`: Analyzes current inventory shortfalls and discovers candidate suppliers.
3. `Show available recovery plans for part shortage on Production Line 4`: Synthesizes 3 actionable recovery options with comparative cards.
4. `Unlock verified audit report with purchase order draft`: Initiates the 0.50 USDC payment protocol to unlock the complete procurement audit pack.
5. `Triage urgent factory equipment breakdown and prioritize replacement parts`: Evaluates work-order urgency and assembly line downtime penalties.
