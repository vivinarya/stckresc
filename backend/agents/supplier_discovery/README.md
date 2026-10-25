# StockRescue Supplier Discovery Specialist Agent

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Autonomous authorized distributor discovery, component interchangeability verification, and geographic proximity matching specialist.

---

## 1. Executive Overview & Problem Statement
When primary factory suppliers report backorders or out-of-stock statuses for critical machinery spares, buyers typically resort to desperate web searches or untrusted marketplaces. This risks acquiring counterfeit components, invalidating machinery warranties, or suffering shipping delays from distant locations.

The **StockRescue Supplier Discovery Specialist** evaluates authorized industrial distributor networks, certified regional surplus pools, and manufacturer interchange catalogs. It verifies dimensional and engineering compatibility, evaluates geographical transit distances, checks verified stock quantities, and outputs a ranked roster of qualified candidates.

---

## 2. Technical Architecture & Event Loop
- **Serverless Cloud Microcontainer**: Hosted natively on Agentverse with zero persistent polling loops.
- **Event-Driven Execution**: Listens exclusively for `TaskRequest(task_type="supplier_discovery")`.
- **High-Performance Multi-Catalog Index**: Performs multi-source filtering across ISO-9001 certified vendors in ~30 milliseconds.

```
       +---------------------------------------------+
       |         StockRescue Coordinator             |
       +----------------------+----------------------+
                              |
                              | TaskRequest (task_type="supplier_discovery")
                              v
       +---------------------------------------------+
       |    Supplier Discovery Specialist (Cloud)    |
       |  - Distributor Authorization Verification    |
       |  - Engineering Cross-Reference Matching      |
       |  - Geolocation & Transit Distance Filtering |
       |  - Multi-Criteria Candidate Scoring         |
       +----------------------+----------------------+
                              |
                              | TaskResponse (ranked candidate suppliers)
                              v
       +---------------------------------------------+
       |         StockRescue Coordinator             |
       +---------------------------------------------+
```

---

## 3. Communication Protocol & Message Schemas

### Protocol Identifier
`stockrescue-supplier-discovery:1.0`

### Inbound Envelope: `TaskRequest`
```json
{
  "task_id": "sup-disc-4491-c2f7",
  "task_type": "supplier_discovery",
  "payload": {
    "case_id": "DEMO-STOCK-RESCUE-001",
    "part_number": "BRG-6205-2RS",
    "category": "BEARINGS_ROTATING_EQUIPMENT",
    "target_destination": "Gary, Indiana, USA",
    "required_quantity": 4,
    "allow_approved_equivalents": true
  }
}
```

### Outbound Envelope: `TaskResponse`
```json
{
  "task_id": "sup-disc-4491-c2f7",
  "task_type": "supplier_discovery",
  "status": "SUCCESS",
  "data": {
    "total_candidates_found": 3,
    "candidates": [
      {
        "supplier_id": "SUPP-MOTION-IND",
        "supplier_name": "Motion Industries (Midwest Hub)",
        "certification": "ISO-9001 / Factory Authorized",
        "matched_part_number": "SKF-6205-2RSH",
        "match_type": "EXACT_DIRECT_OEM",
        "warehouse_location": "Indianapolis, IN (142 miles)",
        "stock_available": 18,
        "lead_time_hours": 4.5,
        "base_unit_price_usd": 42.50,
        "confidence_score": 0.98
      },
      {
        "supplier_id": "SUPP-APPLIED-TECH",
        "supplier_name": "Applied Industrial Technologies",
        "certification": "ISO-9001 / Authorized Distributor",
        "matched_part_number": "NSK-6205DDU",
        "match_type": "EQUIVALENT_CERTIFIED",
        "warehouse_location": "Cleveland, OH (310 miles)",
        "stock_available": 54,
        "lead_time_hours": 12.0,
        "base_unit_price_usd": 38.00,
        "confidence_score": 0.92
      }
    ]
  }
}
```

---

## 4. Candidate Scoring & Multi-Criteria Evaluation Matrix
Suppliers are ranked using a multi-factor fitness score:
$$\text{Score} = w_{\text{cert}} \cdot S_{\text{cert}} + w_{\text{dist}} \cdot S_{\text{dist}} + w_{\text{avail}} \cdot S_{\text{avail}} + w_{\text{match}} \cdot S_{\text{match}}$$
Where:
- $S_{\text{cert}} = 1.0$ for Factory Authorized OEM, $0.85$ for ISO-Certified Stockist.
- $S_{\text{dist}} = \max\left(0, 1 - \frac{\text{Distance (miles)}}{1000}\right)$.
- $S_{\text{avail}} = \min\left(1.0, \frac{\text{Stock Available}}{\text{Required Quantity}}\right)$.
- $S_{\text{match}} = 1.0$ for exact OEM part; $0.90$ for pre-cleared engineering equivalent.

---

## 5. Security, Anti-Counterfeit & Operational Guardrails
- **Counterfeit Screening**: Rejects unverified grey-market or auction-site distributors. Candidates must carry active manufacturer authorization or traceable CoC (Certificate of Conformance).
- **Location Sanitization**: Validates geographic coordinates to prevent transit route calculation errors.
- **Pure Cloud Resilience**: No external dependencies that block thread execution; fails gracefully with fallback candidate listings.
