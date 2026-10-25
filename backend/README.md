# StockRescue Backend

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Intelligent Multi-Agent Supply Chain Recovery System powered by **ASI1 Mini**, **Agent Chat Protocol (ACP)**, and **Fetch.ai Agentverse**.

---

## Quick Start

### 1. Requirements
- Python 3.11+
- Virtual environment (`venv`)

### 2. Setup
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy the template and set the required variables:
```bash
cp .env.example .env
```
*(See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for complete details on secrets).*

### 4. Running the Test Suite
```bash
pytest -v
```

---

## Architecture & Documentation

- [Architecture Overview](docs/ARCHITECTURE.md): Multi-agent topology, coordinator reasoning loop, and persistence boundaries.
- [Deployment Guide](docs/DEPLOYMENT.md): Agentverse cloud deployment, avctl commands, and secret checklist.
- [Frontend Integration Contract](FRONTEND_INTEGRATION_CONTRACT.md): API contract, ACP card schemas, and mock/live endpoints.

---

## Agent Network (8 Hosted Identities)

1. `@stockrescue` — Coordinator (ASI1 Mini)
2. `@sr-inventory` — Inventory & BOM
3. `@sr-jobimpact` — Job Impact Assessment
4. `@sr-suppliers` — Supplier Discovery & Compatibility
5. `@sr-quoterisk` — Quote Risk & Recovery Planning
6. `@sr-procure` — Procurement Authorization Gating
7. `@sr-vendorcomms` — Vendor Voice & Communication
8. `@sr-delivery` — Delivery Tracking & Proof Watch
