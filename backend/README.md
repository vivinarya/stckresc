# StockRescue Backend

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)
[![Backend CI](https://github.com/vivinarya/stckresc/actions/workflows/backend-ci.yml/badge.svg)](https://github.com/vivinarya/stckresc/actions/workflows/backend-ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Autonomous Multi-Agent Supply Chain Recovery System powered by **ASI1 Mini**, **Agent Chat Protocol (ACP)**, and **Fetch.ai Agentverse**.

---

## Table of Contents

- [Overview](#overview)
- [Backend Directory Layout](#backend-directory-layout)
- [Prerequisites](#prerequisites)
- [Installation & Local Setup](#installation--local-setup)
- [Environment Configuration](#environment-configuration)
- [Running Backend Services](#running-backend-services)
  - [FastAPI Warehouse Service](#fastapi-warehouse-service)
  - [Terminal Coordinator CLI](#terminal-coordinator-cli)
  - [Demo Scenario Simulation](#demo-scenario-simulation)
- [Agent Network & Mailbox Mapping](#agent-network--mailbox-mapping)
- [Running the Test Suite](#running-the-test-suite)
- [Agentverse Cloud Deployment](#agentverse-cloud-deployment)
- [Documentation Index](#documentation-index)

---

## Overview

The StockRescue backend is composed of:
1. **Coordinator Orchestrator:** An autonomous agent running the `asi1-mini` model with dynamic reasoning loop (`enable_thinking: true`) that parses natural language emergency reports, calls 14 specialist tools, and emits interactive ACP cards.
2. **Specialist Agents:** Autonomous uAgents executing deterministic domain operations (inventory reconciliation, job impact scoring, supplier sourcing, compatibility checking, quote risk calculation, recovery optimization, purchase order drafting, voice outreach simulation, and delivery tracking).
3. **Warehouse REST API:** A FastAPI service supporting multi-tenant disruption cases, recovery plans, cryptographic purchase order approvals, and carrier event streams.
4. **Deterministic Tool Layer:** High-performance, pure Python computation modules ensuring arithmetic consistency, inventory conservation, and specification safety.

---

## Backend Directory Layout

```
backend/
├── agents/                      # Specialist uAgents implementations
│   ├── coordinator/             # Master coordinator, ACP card protocol, LangGraph flow
│   ├── inventory_bom/           # Warehouse stock and BOM reconciliation specialist
│   ├── job_impact/              # Production line downtime risk evaluator
│   ├── supplier_discovery/      # Distributor sourcing and catalog query specialist
│   ├── compatibility/           # OEM engineering specification compatibility specialist
│   ├── quote_risk/              # Quote risk scoring and cost normalization specialist
│   ├── recovery_planner/        # Multi-criteria recovery plan optimization engine
│   ├── procurement/             # Purchase order draft generator & approval gating
│   ├── vendor_voice/            # Voice RFQ simulator and call transcript logger
│   ├── delivery_watch/          # Logistics tracking and shipment delay monitor
│   └── protocols/               # Inter-agent protocol transport definitions
├── docs/                        # In-depth architectural & deployment guides
│   ├── ARCHITECTURE.md          # Multi-agent topology, coordinator flow, persistence
│   └── DEPLOYMENT.md            # Agentverse cloud deployment, secrets, avctl CLI
├── observability/               # Langfuse tracing and telemetry instrumentation
│   └── langfuse_tracker.py
├── scripts/                     # Developer utilities, CLI chat, and test runners
│   ├── chat_coordinator_local.py # Local terminal chat with Coordinator agent
│   ├── demo_priya_rescue.py     # Retail boutique supply disruption demo
│   ├── migrate_sqlite_to_postgres.py # SQLite-to-PostgreSQL migration utility
│   ├── seed_demo_data.py        # Demo dataset seeder
│   ├── smoke_test_agentverse.py # Agentverse mailbox smoke test
│   └── smoke_test_all_agents_agentverse.py
├── services/                    # Integration and application services
│   ├── warehouse_api/           # FastAPI application, auth, schemas, and repository
│   ├── voice_service/           # Twilio and ElevenLabs voice telephony handlers
│   └── payment_provider/        # Fetch.ai Payment Protocol seller integration
├── shared/                      # Shared Pydantic domain models, config, state machine
├── tests/                       # Comprehensive Pytest test suite (180+ tests)
├── tools/                       # Deterministic computation algorithms
├── .env.example                 # Master environment configuration template
├── FRONTEND_INTEGRATION_CONTRACT.md # Authoritative API and ACP contract
├── pytest.ini                   # Pytest configuration
├── README.md                    # This backend documentation
└── requirements.txt             # Python dependencies
```

---

## Prerequisites

- **Python:** 3.11 or 3.12 (Python 3.11 recommended)
- **Virtual Environment Tool:** `venv` or `conda`
- **Database:** SQLite (default for development and testing) or PostgreSQL / Supabase (production)

---

## Installation & Local Setup

From the `backend` directory:

```bash
# 1. Create a virtual environment
python -m venv .venv

# 2. Activate virtual environment
# Linux / macOS:
source .venv/bin/activate
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Environment Configuration

Copy the template to `.env`:

```bash
# Linux / macOS:
cp .env.example .env

# Windows (PowerShell):
Copy-Item .env.example .env
```

Configure the core parameters in `.env`:
```ini
# LLM Orchestrator
ASI_ONE_API_KEY=your_key_here
ASI_ONE_BASE_URL=https://api.asi1.ai/v1
ASI_ONE_MODEL=asi1-mini

# Database
DATABASE_BACKEND=sqlite
DATABASE_PATH=data/stockrescue.db

# Safe Execution Guardrails
DEMO_MODE=true
REAL_PURCHASING_ENABLED=false
REAL_TELEPHONY_ENABLED=false
VOICE_PROVIDER=simulated
```

---

## Running Backend Services

### FastAPI Warehouse Service
Start the Warehouse API service on `127.0.0.1:8000`:

```bash
python -m uvicorn services.warehouse_api.main:app --host 127.0.0.1 --port 8000 --reload
```
View interactive Swagger docs at `http://127.0.0.1:8000/docs`.

### Terminal Coordinator CLI
Chat directly with the Coordinator agent in your terminal without requiring Agentverse mailboxes:

```bash
python scripts/chat_coordinator_local.py
```

### Demo Scenario Simulation
Execute the multi-agent procurement rescue demo:

```bash
python scripts/demo_priya_rescue.py
```

---

## Agent Network & Mailbox Mapping

StockRescue features 9 specialist tasks running across 8 consolidated Agentverse mailbox identities:

| Identity | Agent Name | Handle | Address | Role |
|---|---|---|---|---|
| 1 | **Coordinator** | `@stockrescue` | `agent1qwftafhh6kekpkzqxh4mdzuvnphhqrqkwxfxgnaccde3fms6r65svnk0cma` | ASI1 Mini conversational orchestrator & card generator |
| 2 | **Inventory/BOM** | `@sr-inventory` | `agent1qvwmvakw2y7s0u4vj2sqcgz9y7ty2fxrg8c5vvgzlw4r70mmz3xpuppm4cr` | Physical vs. committed stock balance reconciler |
| 3 | **Job Impact** | `@sr-jobimpact` | `agent1qwpes68a2as0hh3yahxzz78266agt6ten4ykny3xmqcyc56fhkds7388fln` | Production line criticality & downtime loss scoring |
| 4 | **Supplier Sourcing** | `@sr-suppliers` | `agent1q0ac6fztvpvzuf0kegpek9wl5qnuv26f29aeny7rluygkg56z9d0gygf3vh` | Supplier Discovery & Compatibility (shared mailbox) |
| 5 | **Quote & Planning** | `@sr-quoterisk` | `agent1q2nn70ng2g6d5ccpz87a3ng8kaswyu6me8c4tfjp4zfma0twztstgulm5mz` | Quote Risk & Recovery Planner (shared mailbox) |
| 6 | **Procurement** | `@sr-procure` | `agent1qggs7dy7ky0fza48dpwjqhfnfrrmq2mxg5d8hdvl8x064xhfhqdtwv82wzg` | Purchase order drafting & HITL approval gating |
| 7 | **Vendor Comms** | `@sr-vendorcomms` | `agent1q2ts3pay9rd0zvyk6r5y4f46avu3zl5ft33yvdjv2fxta24k5the5vua4vr` | Negotiation transcripts & voice synthesis calls |
| 8 | **Delivery Watch** | `@sr-delivery` | `agent1q29xxmnpfh4vkjanmhd7hzt4jgjxk6fhur2pwgzn85l6xcdfgpdcggkqlhx` | Courier telemetry tracking & delay alert detector |

---

## Running the Test Suite

Run the full test suite with pytest:

```bash
pytest -v
```

Run tests with the strict in-memory SQLite configuration used in GitHub Actions CI:

```bash
APP_ENV=testing STOCKRESCUE_ENV=testing DATABASE_BACKEND=sqlite DATABASE_PATH=":memory:" REAL_TELEPHONY_ENABLED=false VOICE_PROVIDER=simulated SPECIALIST_TRANSPORT_MODE=local pytest -v
```

On Windows (PowerShell):
```powershell
$env:APP_ENV="testing"; $env:STOCKRESCUE_ENV="testing"; $env:DATABASE_BACKEND="sqlite"; $env:DATABASE_PATH=":memory:"; $env:REAL_TELEPHONY_ENABLED="false"; $env:VOICE_PROVIDER="simulated"; $env:SPECIALIST_TRANSPORT_MODE="local"; pytest -v
```

---

## Agentverse Cloud Deployment

To deploy an agent to Agentverse hosting via `avctl`:

```bash
cd agents/coordinator
avctl hosting deploy --no-dependency-check --file agent.py,agentverse_entry.py,orchestrator.py,card_protocol.py,card_workflow.py
```

For complete deployment details and secret management, see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## Documentation Index

- [Architecture Overview](docs/ARCHITECTURE.md): Multi-agent topology, coordinator reasoning loop, and persistence boundaries.
- [Deployment Guide](docs/DEPLOYMENT.md): Agentverse cloud deployment, `avctl` commands, and secret checklist.
- [Frontend Integration Contract](FRONTEND_INTEGRATION_CONTRACT.md): Authoritative HTTP API specifications, schemas, and ACP card models.
- [Coordinator Agent Documentation](agents/coordinator/README.md)
- [Inventory & BOM Specialist Documentation](agents/inventory_bom/README.md)
- [Job Impact Specialist Documentation](agents/job_impact/README.md)
- [Supplier Discovery Specialist Documentation](agents/supplier_discovery/README.md)
- [Compatibility Specialist Documentation](agents/compatibility/README.md)
- [Quote Risk Specialist Documentation](agents/quote_risk/README.md)
- [Recovery Planner Specialist Documentation](agents/recovery_planner/README.md)
- [Procurement Specialist Documentation](agents/procurement/README.md)
- [Vendor Voice Specialist Documentation](agents/vendor_voice/README.md)
- [Delivery Watch Specialist Documentation](agents/delivery_watch/README.md)
