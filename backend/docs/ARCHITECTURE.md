# StockRescue Architecture Overview

StockRescue is an intelligent, multi-agent business recovery system designed to assist businesses during critical component shortages, supplier delivery delays, and manufacturing disruptions.

The system interacts natively with users over the **Agent Chat Protocol (ACP)** via **ASI:One** and orchestrates specialist agents across **Agentverse Hosted Cloud Infrastructure**.

---

## 1. System Components

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
└──────────────┘ └──────────────┘ └─────────────┘ └──────────────┘ └──────────────┘
                                       │               │
                                       ▼               ▼
                                ┌─────────────┐ ┌──────────────┐
                                │ Vendor Comms│ │   Delivery   │
                                │ & Voice RFQ │ │    Watch     │
                                └─────────────┘ └──────────────┘
```

### 1.1 The Coordinator (`stockrescue-coordinator`)
- **Model**: ASI1 Mini (`asi1-mini` via `https://api.asi1.ai/v1/chat/completions`) with dynamic reasoning (`enable_thinking: True`).
- **Interactive ACP Cards**: Form cards, Detail cards, Option Carousels, and Procurement Review cards.
- **Triage & Session Memory**: Automatically maintains bounded context from previous conversational turns without trapping users in repetitive onboarding forms.
- **Agent Payment Protocol**: Built-in seller protocol offering optional verified recovery reports (0.50 USDC) via Skyfire.

### 1.2 Specialist Agent Network
1. **Inventory/BOM Specialist (`stockrescue-inventory-bom`)**:
   - Computes physical on-hand stock, committed units, active reservations, and net shortages.
   - Preserves inventory conservation rules.
2. **Job Impact Specialist (`stockrescue-job-impact`)**:
   - Assesses production schedule risks, affected job deadlines, customer priorities, and uncovered demand.
3. **Supplier Discovery & Compatibility Specialist (`stockrescue-supplier-discovery`)**:
   - Discovers candidate replacement vendors, validates substitute specifications, filters out failed vendors.
4. **Quote Risk & Recovery Planning Specialist (`stockrescue-quote-risk`)**:
   - Normalizes commercial quotes (MOQs, packing multiples, freight, validity deadlines, currency).
   - Generates deterministic, cost-optimal recovery plans.
5. **Procurement Specialist (`stockrescue-procurement`)**:
   - Strict human-gated purchase order drafting. Enforces `REAL_PURCHASING_ENABLED=false` to prevent automated spending without explicit sign-off.
6. **Vendor Communication & Voice Specialist (`stockrescue-vendor-voice`)**:
   - Structured RFQ generation and automated voice quote acquisition via ElevenLabs + Twilio (simulated by default).
7. **Delivery Watch Specialist (`stockrescue-delivery-watch`)**:
   - Carrier event tracking, transit delay monitoring, and verified delivery receipt verification.

---

## 2. Storage & Data Persistence

- **Source of Truth**: Supabase PostgreSQL (or local SQLite for dev/test).
- **Schema v6**:
  - `organizations`: Multi-tenant organization records.
  - `business_profiles`: Business sizes (`SMALL`, `MEDIUM`, `ENTERPRISE`), operational objectives, preferred currencies.
  - `organization_memberships`: Role-based membership linking user handles to organizations.
  - `disruption_cases`, `jobs`, `inventory_positions`, `supplier_offers`: Operational domain entities.
  - `recovery_plans`, `procurement_approvals`, `purchase_order_drafts`: Planning and authorization records.
  - `delivery_events`: Carrier milestone logs.
  - `payment_requests`: Fetch.ai Payment Protocol transaction ledger.

---

## 3. Communication Protocols

1. **Agent Chat Protocol (ACP)**: Direct natural-language dialogue between operators and the Coordinator.
2. **Specialist Task Exchange**: JSON-RPC compatible typed `TaskRequest` and `TaskResponse` messages with:
   - Correlation ID & Workflow ID preservation
   - Reciprocal sender authorization
   - Request expiration deadlines
   - Idempotent response caching
