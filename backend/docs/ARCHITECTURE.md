# StockRescue Architecture Overview

StockRescue is an intelligent, multi-agent business recovery platform designed to assist industrial facilities and manufacturing operations during critical component shortages, supplier delivery delays, and production line disruptions.

The system interacts natively with users over the **Agent Chat Protocol (ACP)** via **ASI:One** and orchestrates autonomous specialist agents hosted on **Fetch.ai Agentverse Cloud Infrastructure**.

---

## 1. System Topology & Component Model

```
                          ┌──────────────────────────┐
                          │   ASI:One Client / ACP   │
                          └────────────┬─────────────┘
                                       │
                                       ▼
                          ┌──────────────────────────┐
                          │ stockrescue-coordinator  │ (ASI1 Mini + LangGraph)
                          └────────────┬─────────────┘
                                       │
       ┌────────────────┬──────────────┼───────────────┬────────────────┐
       ▼                ▼              ▼               ▼                ▼
┌──────────────┐ ┌──────────────┐ ┌─────────────┐ ┌──────────────┐ ┌──────────────┐
│  Inventory & │ │  Job Impact  │ │  Supplier   │ │ Quote & Plan │ │ Procurement  │
│     BOM      │ │  Assessment  │ │  Discovery  │ │ Optimization │ │    Draft     │
└──────────────┘ └──────────────┘ └──────┬──────┘ └──────┬───────┘ └──────────────┘
                                         │               │
                                         ▼               ▼
                                  ┌─────────────┐ ┌──────────────┐
                                  │ Compatibility│ │ Recovery    │
                                  │ Verification│ │ Planner      │
                                  └─────────────┘ └──────────────┘
                                         │               │
                                         ▼               ▼
                                  ┌─────────────┐ ┌──────────────┐
                                  │ Vendor Comms│ │   Delivery   │
                                  │ & Voice RFQ │ │    Watch     │
                                  └─────────────┘ └──────────────┘
```

---

## 2. Core Subsystems

### 2.1 The Coordinator (`stockrescue-coordinator`)
- **Reasoning Model**: ASI1 Mini (`asi1-mini` via `https://api.asi1.ai/v1/chat/completions`) configured with `enable_thinking: True`.
- **Controlled Tool Calling Loop**: Calls up to 14 approved specialist functions with strict argument validation before returning conversational text or cards.
- **Interactive ACP Cards**: Dynamically emits rich native UI cards into the ASI:One conversation stream:
  - `WelcomeCard`: Onboarding greetings and prompt suggestions.
  - `CaseIntakeCard`: Disruption intake form confirmation.
  - `ImpactDetailCard`: Line downtime loss and urgency summary.
  - `PlanCarouselCard`: Comparative multi-strategy recovery options (Fast-Track, Balanced, Cost-Save).
  - `PlanDetailCard`: Comprehensive line-item and distributor breakdown.
  - `ProcurementReviewCard`: Formal purchase order draft with cryptographic approval token.
  - `NoFeasiblePlanCard`: Mathematical proof when constraints cannot be satisfied.
  - `PaymentRequestCard`: 0.50 USDC invoice for verified audit packs.
  - `PremiumReportCard`: Unlocked verified contingency audit documentation.
  - `DeliveryTrackingCard`: Inbound courier telematics and milestone progress.
- **Zero-Idle Sleep Model**: Operates without polling loops (`@agent.on_interval`), activating upon message arrival and returning to sleep to conserve Agentverse quota.

### 2.2 Approved Coordinator Tool Suite (14 Specialist Tools)
1. `check_business_profile_and_case_context`: Verifies business profile and organization authorization.
2. `get_case_context`: Fetches disruption details, affected SKU, active jobs, and inventory positions.
3. `analyze_inventory_bom`: Computes physical stock vs committed allocations and net shortfall.
4. `assess_job_impact`: Evaluates work order criticality, downtime hourly loss, and SLA default risk.
5. `discover_suppliers`: Slices authorized regional distributor networks, blacklisting failed suppliers.
6. `check_compatibility`: Verifies OEM engineering specifications, dimensions, and tolerances.
7. `assess_quotes`: Calculates landed costs, MOQ penalties, freight fees, and risk scores.
8. `generate_recovery_options`: Formulates multi-strategy recovery options.
9. `get_plan_details`: Retrieves itemized supplier breakdown for a selected plan.
10. `prepare_vendor_communication`: Drafts structured vendor inquiries and expedite requests.
11. `request_vendor_quote_by_voice`: Simulates or conducts supplier quote inquiries via ElevenLabs/Twilio.
12. `get_delivery_status`: Retrieves tracking events, transit delays, and delivery ETA.
13. `prepare_procurement_draft`: Compiles a formal purchase order draft locked to the plan's cryptographic hash.
14. `request_premium_report_payment`: Triggers the 0.50 USDC payment protocol to unlock the premium audit pack.

### 2.3 Specialist Agent Network (9 Tasks across 8 Mailboxes)
To respect the Agentverse Basic plan limit of 8 external mailbox identities while retaining all 9 specialist capabilities, tasks are mapped as follows:

| Mailbox Identity | Specialist Task(s) | Role & Operational Function |
|---|---|---|
| 1 | **Coordinator** | Conversational triage, reasoning loop, card builder |
| 2 | **Inventory / BOM** | Physical stock vs committed balance reconciliation |
| 3 | **Job Impact** | Downstream line dependency traversal & hourly downtime modeling |
| 4 | **Supplier Sourcing** | **Supplier Discovery** + **Engineering Compatibility** (shared mailbox) |
| 5 | **Quote & Planning** | **Quote Risk Scoring** + **Recovery Plan Optimization** (shared mailbox) |
| 6 | **Procurement** | Formal PO draft generation and strict approval gate enforcement |
| 7 | **Vendor Voice** | Negotiation transcripts and automated ElevenLabs/Twilio voice synthesis |
| 8 | **Delivery Watch** | Inbound logistics tracking, milestone verification, delay alerts |

---

## 3. Storage & Persistence Architecture

StockRescue supports dual repository modes:
- **Local Development & Testing:** Fast, zero-dependency SQLite repository (`data/stockrescue.db` or `:memory:`).
- **Production Enterprise:** Supabase PostgreSQL with pooled connections (`psycopg-pool`) and LangGraph checkpoint persistence.

### Key Database Entities
- `organizations`: Multi-tenant enterprise organization records.
- `business_profiles`: Operational size (`SMALL`, `MEDIUM`, `ENTERPRISE`), priority goals, and base currency.
- `organization_memberships`: Role-based membership linking user handles to organizations.
- `disruption_cases`: Core case records linking affected SKUs, reporting dates, budgets, and failed suppliers.
- `jobs`: Work orders requiring the affected component with criticality ratings and deadlines.
- `inventory_positions`: Physical bin locations, on-hand counts, and committed reserves.
- `supplier_offers`: Distributor quotes with MOQs, pack sizes, lead times, and freight surcharges.
- `recovery_plans`: Deterministically generated recovery options.
- `procurement_approvals`: Human-in-the-Loop signed authorization tokens with canonical plan fingerprints.
- `purchase_order_drafts`: Formal, auditable purchase order records.
- `delivery_events`: Carrier tracking logs and milestone telematics.
- `audit_events`: Tamper-evident immutable ledger of system actions.
- `payment_requests`: Fetch.ai Payment Protocol transaction ledger.

---

## 4. Communication Protocols

### 4.1 Agent Chat Protocol (ACP)
- **Namespace**: `uagents_core.contrib.protocols.chat`
- **Inbound Message**: `ChatMessage` with `TextContent(text="...")`.
- **Outbound Response**: `ChatAcknowledgement` and rich `MetadataContent` containing interactive JSON cards.

### 4.2 Inter-Agent Task Protocol
- **Envelope**: Typed `TaskRequest` and `TaskResponse` envelopes.
- **Fields**: `task_id` (UUID), `task_type` (e.g. `inventory_bom`), and domain-specific `payload`.
- **Reliability Features**: Correlation ID preservation, reciprocal sender validation, request expiration deadlines, and idempotent response caching.

### 4.3 Agent Payment Protocol (Seller Service)
- Built-in implementation of the Fetch.ai Payment Protocol specification via Skyfire.
- Allows external buyers to purchase unredacted contingency audit packs for `0.50 USDC`.
- Protocol state machine: `RequestPayment` -> `CommitPayment` (client signature) -> cryptographic verification -> `CompletePayment` + audit unlock.

---

## 5. Security & Governance Boundaries

1. **Purchasing Safeguard:** Autonomous live spending is permanently disabled (`REAL_PURCHASING_ENABLED=false`). The system strictly produces signed drafts requiring authorized human operator sign-off.
2. **Canonical Plan Fingerprinting:** Every recovery plan is fingerprinted via SHA-256 over its canonical JSON structure. Approvals and purchase orders bind to this hash; any post-approval tampering invalidates the authorization.
3. **Role-Based Access Control (RBAC):** Approval endpoints require authenticated Supabase JWTs with `procurement_approver` or `admin` roles in metadata claims.
4. **Telephony Safeguards:** Telephony operates in simulated mode by default (`VOICE_PROVIDER=simulated`, `REAL_TELEPHONY_ENABLED=false`) with call duration limits and authorized destination whitelisting.
