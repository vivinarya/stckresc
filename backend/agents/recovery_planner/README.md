# StockRescue Recovery Planner Specialist Agent

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Autonomous multi-criteria recovery plan optimization, single/split-sourcing strategy synthesizer, and mathematical constraint solver.

---

## 1. Executive Overview & Problem Statement

When an industrial supply chain fails, plant operators are forced to resolve conflicting multi-dimensional trade-offs in minutes:
- Sourcing from a single distributor might take 3 days to deliver the full quantity, causing 2 days of line downtime.
- Split-sourcing from two regional distributors can deliver enough emergency parts within 18 hours to resume production, but incurs dual expedited freight charges.
- Ordering partial surplus lots may violate vendor Minimum Order Quantities (MOQs) or package unit multiples.

The **StockRescue Recovery Planner** solves this optimization challenge. It evaluates physical on-hand inventory, active production job deadlines, distributor quotes, pack sizes, and freight surcharges to generate deterministic, cost-optimal recovery options (Fast-Track Expedited, Balanced Recommended, Budget Optimized).

---

## 2. Technical Architecture & Event Loop

- **Serverless Cloud Microcontainer**: Deployed natively on Agentverse with zero idle polling loops.
- **Consolidated Mailbox Integration**: Shares physical mailbox identity with Quote Risk (`@sr-quoterisk` / `agent1q2nn70ng2g6d5ccpz87a3ng8kaswyu6me8c4tfjp4zfma0twztstgulm5mz`) under the Agentverse Basic 8-mailbox configuration.
- **Deterministic Mathematical Solver**: Computes feasible candidate allocations in under 25 milliseconds, guaranteeing mathematical conservation of quantities.
- **Proof of Infeasibility**: If no supplier combination satisfies budget, delivery, and compatibility constraints, it returns an auditable mathematical proof (`no_feasible_plan`) explaining exact bottlenecks.

```
       +---------------------------------------------+
       |         StockRescue Coordinator             |
       +----------------------+----------------------+
                              |
                              | TaskRequest (task_type="recovery_planner")
                              v
       +---------------------------------------------+
       |      Recovery Planner Specialist (Cloud)    |
       |  - Net Shortfall Demand Formulation         |
       |  - Compatible Offer Candidate Slicing       |
       |  - Single & Split-Source Allocation Search  |
       |  - Landed Cost Arithmetic Minimization      |
       +----------------------+----------------------+
                              |
                              | TaskResponse (recovery_plan, outcome, blockers)
                              v
       +---------------------------------------------+
       |         StockRescue Coordinator             |
       +---------------------------------------------+
```

---

## 3. Communication Protocol & Message Schemas

### Protocol Identifier
`stockrescue-recovery-planner:1.0`

### Inbound Envelope: `TaskRequest`
```json
{
  "task_id": "rec-plan-8812-f1c3",
  "task_type": "recovery_planner",
  "payload": {
    "case_id": "CASE-PIPE-001",
    "as_of": "2026-11-14T00:00:00Z"
  }
}
```

### Outbound Envelope: `TaskResponse`
```json
{
  "task_id": "rec-plan-8812-f1c3",
  "task_type": "recovery_planner",
  "status": "completed",
  "payload": {
    "case_id": "CASE-PIPE-001",
    "outcome": "awaiting_approval",
    "summary": "Generated optimal recovery plan allocating 30 units across Midwest Piping Corp.",
    "recovery_plan": {
      "plan_id": "PLAN-PIPE-REC-001",
      "case_id": "CASE-PIPE-001",
      "status": "proposed",
      "total_cost": "1395.00",
      "currency": "USD",
      "lines": [
        {
          "line_id": "LINE-01",
          "offer_id": "OFFER-MIDWEST-001",
          "supplier_id": "SUPP-MIDWEST",
          "sku": "PIPE-4IN-SCH40",
          "quantity": 30,
          "unit_price": "42.50",
          "freight_cost": "120.00",
          "total_cost": "1395.00",
          "lead_time_days": 1,
          "estimated_delivery_at": "2026-11-15T00:00:00Z"
        }
      ]
    },
    "blockers": [],
    "procurement_authorized": false
  }
}
```

---

## 4. Multi-Criteria Optimization Strategy

1. **Deficit Slicing**: Evaluates net uncovered units across production lines after exhausting on-hand warehouse inventory.
2. **Pack Size & MOQ Rounding**:
   $$\text{Purchased Units} = \left\lceil \frac{\max(\text{Needed}, \text{MOQ})}{\text{Pack Size}} \right\rceil \times \text{Pack Size}$$
3. **Total Landed Cost Calculation**:
   $$\text{Landed Cost} = \sum (\text{Units} \times \text{Unit Price}) + \text{Freight Surcharges} + \text{Expedite Fees}$$
4. **Feasibility Verification**: Verifies that estimated delivery dates precede job downtime deadlines and total cost does not exceed the case budget ceiling.
