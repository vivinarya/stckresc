# StockRescue Inventory & BOM Specialist Agent

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Autonomous component shortfall evaluation, physical warehouse position reconciliation, and bill-of-materials analysis specialist.

---

## 1. Executive Overview & Problem Statement
In high-throughput industrial environments, inventory records in legacy Enterprise Resource Planning (ERP) systems frequently diverge from physical bin realities due to unrecorded scrap, misplaced bins, or concurrent work orders reserving the same lot. When a machine breaks down, maintenance engineers cannot afford to waste hours walking across warehouses only to find an empty bin.

The **StockRescue Inventory & BOM Specialist** is an autonomous cloud agent that inspects warehouse inventory positions, parses multi-tier Bills of Materials (BOM), reconciles committed vs. available quantities, normalizes heterogeneous SKU naming conventions, and computes the mathematically bounded net component shortfall.

---

## 2. Technical Architecture & Event Loop
- **Pure Event-Driven Runtime**: Configured without interval polling (`@agent.on_interval`). The container stays in ultra-low power hibernation until a `TaskRequest` targeting `inventory_bom` arrives.
- **Microsecond In-Memory Execution**: Evaluates multi-line component hierarchies in under 15 milliseconds using optimized linear dictionary lookups.
- **Zero Compute Waste**: Returns immediately to sleep upon message dispatch, conserving host compute units.

```
       +---------------------------------------------+
       |         StockRescue Coordinator             |
       +----------------------+----------------------+
                              |
                              | TaskRequest (task_type="inventory_bom")
                              v
       +---------------------------------------------+
       |    Inventory & BOM Specialist (Cloud)       |
       |  - SKU Canonicalization                      |
       |  - Bin Position Reconciliation               |
       |  - Net Shortage Calculation: max(0, D - A)   |
       +----------------------+----------------------+
                              |
                              | TaskResponse (shortage, bin locations, status)
                              v
       +---------------------------------------------+
       |         StockRescue Coordinator             |
       +---------------------------------------------+
```

---

## 3. Communication Protocol & Message Schemas

### Protocol Identifier
`stockrescue-inventory-bom:1.0`

### Inbound Envelope: `TaskRequest`
```json
{
  "task_id": "inv-req-7782-a9b1",
  "task_type": "inventory_bom",
  "payload": {
    "case_id": "DEMO-STOCK-RESCUE-001",
    "part_number": "BRG-6205-2RS",
    "required_qty": 4,
    "line_id": "LINE-04-PUMP",
    "warehouse_records": [
      {
        "bin_id": "BIN-A12-04",
        "on_hand": 1,
        "allocated": 1,
        "available": 0,
        "condition": "NEW_OEM"
      }
    ]
  }
}
```

### Outbound Envelope: `TaskResponse`
```json
{
  "task_id": "inv-req-7782-a9b1",
  "task_type": "inventory_bom",
  "status": "SUCCESS",
  "data": {
    "normalized_part_number": "BRG62052RS",
    "required_quantity": 4,
    "available_quantity": 0,
    "net_shortage": 4,
    "is_critical_shortage": true,
    "verified_bin_locations": [],
    "notes": "1 unit on-hand in BIN-A12-04 is committed to active maintenance work order WO-8812."
  }
}
```

---

## 4. Deterministic Logic & Normalization Rules

### A. SKU Normalization Engine
Different vendors and ERP databases format part numbers with arbitrary hyphens, slashes, spaces, and casing. The agent strips all non-alphanumeric separators:
$$\text{SKU}_{\text{norm}} = \text{regex\_replace}(\text{upper}(\text{SKU}), \text{"[^A-Z0-9]"}, \text{""})$$
This ensures `BRG-6205-2RS`, `brg 6205/2rs`, and `BRG62052RS` match identically.

### B. Net Shortage Calculation
The net shortfall is strictly bounded below by zero to prevent negative shortage anomalies:
$$\text{Net Shortage} = \max\left(0, \sum \text{Required Quantity} - \sum \text{Unallocated Available Stock}\right)$$

---

## 5. Security & Operational Guardrails
- **Input Sanitization**: Rejects requests where `required_qty > 1,000,000` or negative numbers, guarding against buffer allocation attacks.
- **Idempotent Handling**: Stateless computation ensures identical input payloads yield identical deterministic responses.
- **Air-Gapped Credentials**: Operates purely within Fetch.ai Agentverse cloud infrastructure without external unauthenticated database ports.
