# Agentverse Deployment & Secrets Guide

This document details the configuration, secret management, and verification procedures for running StockRescue's agents natively on **Fetch.ai Agentverse Cloud Hosting**.

---

## 1. Registered Agent Identities & Mailbox Mapping

StockRescue features 9 specialist tasks running across 8 consolidated Agentverse mailbox identities:

| Agent Name | Role | Address | Handle | Domain / Protocol |
| :--- | :--- | :--- | :--- | :--- |
| **`stockrescue-coordinator`** | Main Coordinator | `agent1qwftafhh6kekpkzqxh4mdzuvnphhqrqkwxfxgnaccde3fms6r65svnk0cma` | `@stockrescue` | `stockrescue-coordinator` |
| **`stockrescue-inventory-bom`** | Inventory / BOM | `agent1qvwmvakw2y7s0u4vj2sqcgz9y7ty2fxrg8c5vvgzlw4r70mmz3xpuppm4cr` | `@sr-inventory` | `stockrescue-inventory_bom` |
| **`stockrescue-job-impact`** | Job Impact | `agent1qwpes68a2as0hh3yahxzz78266agt6ten4ykny3xmqcyc56fhkds7388fln` | `@sr-jobimpact` | `stockrescue-job_impact` |
| **`stockrescue-supplier-discovery`** | Supplier Sourcing | `agent1q0ac6fztvpvzuf0kegpek9wl5qnuv26f29aeny7rluygkg56z9d0gygf3vh` | `@sr-suppliers` | `stockrescue-supplier_discovery` (also serves `compatibility`) |
| **`stockrescue-quote-risk`** | Quote & Planning | `agent1q2nn70ng2g6d5ccpz87a3ng8kaswyu6me8c4tfjp4zfma0twztstgulm5mz` | `@sr-quoterisk` | `stockrescue-quote_risk` (also serves `recovery_planner`) |
| **`stockrescue-procurement`** | Procurement Gating | `agent1qggs7dy7ky0fza48dpwjqhfnfrrmq2mxg5d8hdvl8x064xhfhqdtwv82wzg` | `@sr-procure` | `stockrescue-procurement` |
| **`stockrescue-vendor-voice`** | Vendor Voice Comms | `agent1q2ts3pay9rd0zvyk6r5y4f46avu3zl5ft33yvdjv2fxta24k5the5vua4vr` | `@sr-vendorcomms` | `stockrescue-vendor-voice` |
| **`stockrescue-delivery-watch`** | Delivery Tracking | `agent1q29xxmnpfh4vkjanmhd7hzt4jgjxk6fhur2pwgzn85l6xcdfgpdcggkqlhx` | `@sr-delivery` | `stockrescue-delivery-watch` |

---

## 2. Environment Variables & Secret Configuration

Configure these secrets in the Agentverse Dashboard or your local `.env` file:

### 2.1 Core LLM Orchestration
- `ASI_ONE_API_KEY`: API key for ASI:One Developer API.
- `ASI_ONE_BASE_URL`: `https://api.asi1.ai/v1` (default).
- `ASI_ONE_MODEL`: `asi1-mini` (default).

### 2.2 Database & Supabase
- `DATABASE_BACKEND`: `postgres` (production) or `sqlite` (local dev).
- `DATABASE_PATH`: `data/stockrescue.db` (used when `DATABASE_BACKEND=sqlite`).
- `WAREHOUSE_DATABASE_URL`: Connection URI for PostgreSQL / Supabase.
- `SUPABASE_URL`: Project URL (`https://<project-ref>.supabase.co`).
- `SUPABASE_JWT_SECRET`: Secret for validating incoming user bearer tokens.
- `SUPABASE_JWT_ISSUER`: `https://<project-ref>.supabase.co/auth/v1`.
- `SUPABASE_JWT_AUDIENCE`: `authenticated`.

### 2.3 Vendor Voice Outreach (ElevenLabs + Twilio)
- `VOICE_PROVIDER`: `simulated` (default safe mode) or `elevenlabs_twilio`.
- `REAL_TELEPHONY_ENABLED`: `false` (default safe mode).
- `ELEVENLABS_API_KEY`: ElevenLabs authorization key.
- `ELEVENLABS_AGENT_ID`: Configured ElevenLabs Conversational AI Agent ID.
- `ELEVENLABS_AGENT_PHONE_NUMBER_ID`: Outbound telephone ID mapped in ElevenLabs.
- `TWILIO_ACCOUNT_SID`: Twilio account SID.
- `TWILIO_AUTH_TOKEN`: Twilio account authentication token.
- `TWILIO_FROM_NUMBER`: Authorized Twilio caller ID.

### 2.4 Agent Payment Protocol (Fetch.ai / Skyfire)
- `FETCH_PAYMENT_ENABLED`: `true` or `false`.
- `SELLER_SKYFIRE_API_KEY`: Skyfire API key.
- `SELLER_SERVICE_ID`: `stockrescue-premium-report`.
- `SELLER_ACCOUNT_ID`: Skyfire merchant account.
- `SKYFIRE_ENV`: `sandbox` or `production`.

### 2.5 Agentverse Access Controls & Seeds
- `AGENTVERSE_SEED_COORDINATOR`: Secret seed for the Coordinator agent.
- `AGENTVERSE_SEED_INVENTORY_BOM`: Secret seed for the Inventory/BOM agent.
- `AGENTVERSE_SEED_JOB_IMPACT`: Secret seed for the Job Impact agent.
- `AGENTVERSE_SEED_SUPPLIER_DISCOVERY`: Secret seed (serves both supplier discovery and compatibility).
- `AGENTVERSE_SEED_QUOTE_RISK`: Secret seed (serves both quote risk and recovery planner).
- `AGENTVERSE_SEED_PROCUREMENT`: Secret seed for the Procurement agent.
- `AGENTVERSE_SEED_VENDOR_VOICE`: Secret seed for the Vendor Voice agent.
- `AGENTVERSE_SEED_DELIVERY_WATCH`: Secret seed for the Delivery Watch agent.
- `SPECIALIST_TRANSPORT_MODE`: `local` (in-process fallback) or `agentverse` (mailbox network).

---

## 3. Deploying Agents via AVCTL

The Fetch.ai `avctl` CLI tool packages and deploys agent code to Agentverse cloud hosting.

### 3.1 Prerequisites
Install the `avctl` tool according to Fetch.ai documentation:
```bash
# Verify installation
avctl --version
```

### 3.2 Deploying Coordinator
From `backend/agents/coordinator`:
```bash
avctl hosting deploy --no-dependency-check --file agent.py,agentverse_entry.py,orchestrator.py,card_protocol.py,card_workflow.py
```

### 3.3 Deploying Specialist Agents
From each agent's directory under `backend/agents/<agent_name>`:
```bash
avctl hosting deploy --no-dependency-check --file agent.py,agentverse_entry.py
```

### 3.4 Verifying Deployment Status
Check the status of all deployed agents:
```bash
avctl hosting get agents
```
All agents should report:
```
COMPILED: true
RUNNING: true
```

---

## 4. Verification & Health Monitoring

### 4.1 Local Warehouse Health Check
```bash
curl -X GET http://127.0.0.1:8000/health
```
Expected response:
```json
{
  "app_name": "StockRescue Warehouse API",
  "demo_mode": true,
  "real_purchasing_enabled": false
}
```

### 4.2 Agentverse Mailbox Smoke Test
Run the automated mailbox connectivity test:
```bash
cd backend
python scripts/smoke_test_agentverse.py
python scripts/smoke_test_all_agents_agentverse.py
```
