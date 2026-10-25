# Agentverse Deployment & Secrets Guide

This document details the configuration, secret management, and verification procedures for running StockRescue's 8 agents natively on **Fetch.ai Agentverse Cloud Hosting**.

---

## 1. Registered Agent Identities

| Agent Name | Role | Address | Handle | Domain / Protocol |
| :--- | :--- | :--- | :--- | :--- |
| **`stockrescue-coordinator`** | Main Coordinator | `agent1qwftafhh6kekpkzqxh4mdzuvnphhqrqkwxfxgnaccde3fms6r65svnk0cma` | `@stockrescue` | `stockrescue-coordinator` |
| **`stockrescue-inventory-bom`** | Inventory/BOM | `agent1qvwmvakw2y7s0u4vj2sqcgz9y7ty2fxrg8c5vvgzlw4r70mmz3xpuppm4cr` | `@sr-inventory` | `stockrescue-inventory_bom` |
| **`stockrescue-job-impact`** | Job Impact | `agent1qwpes68a2as0hh3yahxzz78266agt6ten4ykny3xmqcyc56fhkds7388fln` | `@sr-jobimpact` | `stockrescue-job_impact` |
| **`stockrescue-supplier-discovery`** | Supplier Sourcing | `agent1q0ac6fztvpvzuf0kegpek9wl5qnuv26f29aeny7rluygkg56z9d0gygf3vh` | `@sr-suppliers` | `stockrescue-supplier_discovery` |
| **`stockrescue-quote-risk`** | Quote & Planning | `agent1q2nn70ng2g6d5ccpz87a3ng8kaswyu6me8c4tfjp4zfma0twztstgulm5mz` | `@sr-quoterisk` | `stockrescue-quote_risk` |
| **`stockrescue-procurement`** | Procurement Gating | `agent1qggs7dy7ky0fza48dpwjqhfnfrrmq2mxg5d8hdvl8x064xhfhqdtwv82wzg` | `@sr-procure` | `stockrescue-procurement` |
| **`stockrescue-vendor-voice`** | Vendor Voice Comms | `agent1q2ts3pay9rd0zvyk6r5y4f46avu3zl5ft33yvdjv2fxta24k5the5vua4vr` | `@sr-vendorcomms` | `stockrescue-vendor-voice` |
| **`stockrescue-delivery-watch`** | Delivery Tracking | `agent1q29xxmnpfh4vkjanmhd7hzt4jgjxk6fhur2pwgzn85l6xcdfgpdcggkqlhx` | `@sr-delivery` | `stockrescue-delivery-watch` |

---

## 2. Environment Variables & Secret Configuration

Configure these secrets in the Agentverse Dashboard or `.env` file:

### 2.1 Core LLM Orchestration
- `ASI_ONE_API_KEY`: API key for ASI:One Developer API.
- `ASI_ONE_BASE_URL`: `https://api.asi1.ai/v1` (default).
- `ASI_ONE_MODEL`: `asi1-mini` (default).

### 2.2 Database & Supabase
- `DATABASE_BACKEND`: `postgres` (or `sqlite` for local dev).
- `WAREHOUSE_DATABASE_URL`: Connection string for PostgreSQL / Supabase.
- `SUPABASE_URL`: Project URL (`https://<project-ref>.supabase.co`).
- `SUPABASE_JWT_SECRET`: Secret for validating incoming user tokens.

### 2.3 Vendor Voice Outreach (ElevenLabs + Twilio)
- `VOICE_PROVIDER`: `simulated` (default) or `elevenlabs_twilio`.
- `REAL_TELEPHONY_ENABLED`: `false` (default safe mode).
- `ELEVENLABS_API_KEY`: ElevenLabs authorization key.
- `ELEVENLABS_AGENT_ID`: Configured ElevenLabs Conversational AI Agent.
- `ELEVENLABS_AGENT_PHONE_NUMBER_ID`: Outbound telephone ID mapped in ElevenLabs.
- `TWILIO_ACCOUNT_SID`: Twilio account identifier.
- `TWILIO_AUTH_TOKEN`: Twilio account authentication token.
- `TWILIO_FROM_NUMBER`: Authorized Twilio caller ID.

### 2.4 Agent Payment Protocol (Fetch.ai / Skyfire)
- `FETCH_PAYMENT_ENABLED`: `true` or `false`.
- `SELLER_SKYFIRE_API_KEY`: Skyfire API key.
- `SELLER_SERVICE_ID`: `stockrescue-premium-report`.
- `SELLER_ACCOUNT_ID`: Skyfire merchant account.
- `SKYFIRE_ENV`: `sandbox` or `production`.

---

## 3. Deploying Agents via AVCTL

Deploying all agents to Agentverse cloud:

```powershell
# From each agent directory under backend/agents/<agent_name>
avctl hosting deploy --no-dependency-check --file agent.py,agentverse_entry.py
```

Check status of deployed agents:
```powershell
avctl hosting get agents
```
All agents should report `COMPILED: true` and `RUNNING: true`.
