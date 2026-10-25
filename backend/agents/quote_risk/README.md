# StockRescue Quote Risk Specialist Agent

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Autonomous supplier price quote analysis, Minimum Order Quantity (MOQ) evaluation, expedited freight feasibility, and financial risk modeling specialist.

---

## 1. Executive Overview & Problem Statement
When emergency suppliers quote parts during factory crises, they often bundle hidden conditions: prohibitive Minimum Order Quantities (requiring buying 100 units when only 4 are needed), non-refundable expediting surcharges, unrealistic dispatch windows, or vague delivery disclaimers. Plant buyers who overlook these clauses risk buying excess obsolete stock or incurring unexpected freight bills.

The **StockRescue Quote Risk Specialist** scrutinizes incoming supplier quotes. It calculates the True Landed Cost, compares expediting costs against plant downtime losses, evaluates MOQ penalties, and assigns a calibrated Quote Risk Score (0.0 to 1.0) with granular risk warning flags.

---

## 2. Technical Architecture & Event Loop
- **Serverless Cloud Microcontainer**: Runs natively on Agentverse with zero idle polling loops.
- **Event-Driven Execution**: Wakes up immediately upon receiving `TaskRequest(task_type="quote_risk")`.
- **Pure Arithmetic & Heuristic Verification**: Computes multi-tier financial schedules and risk metrics in under 20 milliseconds, returning immediately to sleep.

```
       +---------------------------------------------+
       |         StockRescue Coordinator             |
       +----------------------+----------------------+
                              |
                              | TaskRequest (task_type="quote_risk")
                              v
       +---------------------------------------------+
       |       Quote Risk Specialist (Cloud)         |
       |  - True Landed Cost Computation             |
       |  - MOQ Surplus Capital Waste Assessment     |
       |  - Lead-Time Margin & Expedited Feasibility |
       |  - Multi-Dimensional Risk Index (0.0 - 1.0) |
       +----------------------+----------------------+
                              |
                              | TaskResponse (scored quotes, risk flags, recommended tier)
                              v
       +---------------------------------------------+
       |         StockRescue Coordinator             |
       +---------------------------------------------+
```

---

## 3. Communication Protocol & Message Schemas

### Protocol Identifier
`stockrescue-quote-risk:1.0`

### Inbound Envelope: `TaskRequest`
```json
{
  "task_id": "qr-eval-9912-d4a3",
  "task_type": "quote_risk",
  "payload": {
    "case_id": "DEMO-STOCK-RESCUE-001",
    "required_quantity": 4,
    "hourly_downtime_cost": 12500.0,
    "quotes": [
      {
        "quote_id": "Q-MOTION-01",
        "supplier_name": "Motion Industries",
        "unit_price_usd": 42.50,
        "moq": 4,
        "expedited_freight_usd": 180.0,
        "quoted_lead_time_hours": 4.5,
        "restocking_fee_percent": 15.0
      },
      {
        "quote_id": "Q-FAST-PARTS-02",
        "supplier_name": "FastTrack Spares Ltd",
        "unit_price_usd": 35.00,
        "moq": 25,
        "expedited_freight_usd": 350.0,
        "quoted_lead_time_hours": 3.0,
        "restocking_fee_percent": 30.0
      }
    ]
  }
}
```

### Outbound Envelope: `TaskResponse`
```json
{
  "task_id": "qr-eval-9912-d4a3",
  "task_type": "quote_risk",
  "status": "SUCCESS",
  "data": {
    "best_overall_quote_id": "Q-MOTION-01",
    "evaluated_quotes": [
      {
        "quote_id": "Q-MOTION-01",
        "total_landed_cost_usd": 350.0,
        "effective_unit_cost_usd": 87.50,
        "moq_surplus_cost_usd": 0.0,
        "risk_score": 0.12,
        "risk_level": "LOW",
        "flags": []
      },
      {
        "quote_id": "Q-FAST-PARTS-02",
        "total_landed_cost_usd": 1225.0,
        "effective_unit_cost_usd": 306.25,
        "moq_surplus_cost_usd": 735.0,
        "risk_score": 0.68,
        "risk_level": "MEDIUM_HIGH",
        "flags": ["MOQ_SIGNIFICANTLY_EXCEEDS_REQUIREMENT", "ELEVATED_RESTOCKING_PENALTY"]
      }
    ]
  }
}
```

---

## 4. Financial Formulation & Risk Scoring Logic

### A. True Landed Cost Equation
$$\text{Landed Cost} = \max(\text{Req Qty}, \text{MOQ}) \cdot \text{Unit Price} + \text{Expedited Freight} + \text{Handling Surcharge}$$

### B. Effective Unit Cost
$$\text{Effective Unit Cost} = \frac{\text{Landed Cost}}{\text{Req Qty}}$$

### C. Composite Risk Score Algorithm (0.0 - 1.0)
$$\text{Risk} = 0.40 \cdot R_{\text{moq}} + 0.35 \cdot R_{\text{lead\_margin}} + 0.15 \cdot R_{\text{penalty\_terms}} + 0.10 \cdot R_{\text{price\_variance}}$$
Where:
- $R_{\text{moq}} = \min\left(1.0, \frac{\text{MOQ} - \text{Req Qty}}{\text{Req Qty}}\right)$.
- $R_{\text{lead\_margin}}$ penalizes quotes with insufficient buffer before factory SLA breaches.

---

## 5. Security & Operational Guardrails
- **Division-by-Zero Safeguard**: Guarantees non-zero divisors across all cost ratio routines.
- **Negative Quote Guard**: Validates that prices, quantities, and freight charges are non-negative.
- **Cloud Zero-Idle Operation**: Fully serverless event-driven execution with zero background polling.
