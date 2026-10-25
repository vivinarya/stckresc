# StockRescue Procurement Specialist Agent

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Autonomous purchase order (PO) draft generation, corporate ERP reconciliation, approval-gate governance, and formal supplier dispatch specialist.

---

## 1. Executive Overview & Problem Statement
In manufacturing and enterprise facilities, uncoordinated emergency purchasing often leads to "maverick spend"—unapproved credit card purchases, bypassing preferred vendor contracts, missing accounting cost center tags, and zero traceability for audits. Conversely, waiting for traditional 5-layer corporate signature cycles causes machinery to sit idle for days.

The **StockRescue Procurement Specialist** resolves this tension. It synthesizes approved recovery parameters into compliant, structured **Purchase Order (PO) Drafts** complete with General Ledger (GL) account mappings, delivery dock instructions, expedited courier dispatch tags, and explicit Human-in-the-Loop (HITL) approval gates.

---

## 2. Technical Architecture & Event Loop
- **Serverless Cloud Microcontainer**: Hosted natively on Agentverse with zero persistent polling loops.
- **Event-Driven Execution**: Wakes up solely upon receipt of a `TaskRequest(task_type="procurement_plan")`.
- **Cryptographic & Formal PO Generation**: Produces auditable JSON and human-readable purchase order documents in under 25 milliseconds, returning immediately to sleep.

```
       +---------------------------------------------+
       |         StockRescue Coordinator             |
       +----------------------+----------------------+
                              |
                              | TaskRequest (task_type="procurement_plan")
                              v
       +---------------------------------------------+
       |       Procurement Specialist (Cloud)        |
       |  - Cost Center & GL Account Association      |
       |  - Line-Item Price & Tax Schedule Synthesis |
       |  - Delivery Dock & Expedite Tagging         |
       |  - Strict HITL Approval Gate Enforcement    |
       +----------------------+----------------------+
                              |
                              | TaskResponse (purchase order draft, approval token)
                              v
       +---------------------------------------------+
       |         StockRescue Coordinator             |
       +---------------------------------------------+
```

---

## 3. Communication Protocol & Message Schemas

### Protocol Identifier
`stockrescue-procurement:1.0`

### Inbound Envelope: `TaskRequest`
```json
{
  "task_id": "po-gen-1029-e5f2",
  "task_type": "procurement_plan",
  "payload": {
    "case_id": "DEMO-STOCK-RESCUE-001",
    "selected_supplier_id": "SUPP-MOTION-IND",
    "supplier_name": "Motion Industries",
    "part_number": "SKF-6205-2RSH",
    "quantity": 4,
    "unit_price_usd": 42.50,
    "freight_cost_usd": 180.0,
    "cost_center": "CC-PLANT-04-MAINT",
    "ship_to_dock": "Receiving Dock 2 (Urgent Hot Shot Intake)"
  }
}
```

### Outbound Envelope: `TaskResponse`
```json
{
  "task_id": "po-gen-1029-e5f2",
  "task_type": "procurement_plan",
  "status": "SUCCESS",
  "data": {
    "po_number": "PO-DRAFT-SR-2026-0881",
    "status": "DRAFT_PENDING_OPERATOR_SIGNATURE",
    "requires_hitl_approval": true,
    "line_items": [
      {
        "line_number": 1,
        "part_number": "SKF-6205-2RSH",
        "description": "Deep Groove Ball Bearing, Double Rubber Sealed, C3 Clearance",
        "quantity": 4,
        "unit_price_usd": 42.50,
        "extended_price_usd": 170.00
      },
      {
        "line_number": 2,
        "description": "Dedicated Expedited Hot-Shot Same-Day Courier Delivery",
        "quantity": 1,
        "unit_price_usd": 180.00,
        "extended_price_usd": 180.00
      }
    ],
    "subtotal_usd": 350.00,
    "tax_usd": 24.50,
    "total_authorized_usd": 374.50,
    "approval_gate_hash": "0x89fbc412ae8b1d99"
  }
}
```

---

## 4. Governance, Compliance & Financial Controls
1. **Strict Human-in-the-Loop Barrier**: The agent **never releases** purchase orders directly to suppliers or executes wire transfers autonomously. It produces an auditable Draft and approval token requiring explicit plant manager signature.
2. **Double-Spend & Duplicate Order Prevention**: Each PO draft is permanently tagged with a case-level UUID hash to prevent duplicate orders across simultaneous operator sessions.
3. **Audit Trail Completeness**: Every draft embeds the full upstream reasoning chain: origin disruption ticket, inventory check status, and quote risk evaluation.

---

## 5. Security & Operational Guardrails
- **Immutable State Hashing**: Financial authorization drafts generate a SHA-256 integrity hash ensuring order parameters cannot be tampered with between drafting and execution.
- **Serverless Reliability**: Zero persistent state on ephemeral containers; all metadata is cleanly persisted via Supabase PostgreSQL records.
