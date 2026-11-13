# StockRescue Compatibility Specialist Agent

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Autonomous OEM part specification verification, mechanical/electrical tolerance cross-referencing, and candidate substitute engineering safety specialist.

---

## 1. Executive Overview & Problem Statement

In industrial manufacturing, substituting critical components—such as high-pressure pipe spools, bearing assemblies, relief valves, and heavy-duty motor windings—carries severe physical, operational, and warranty risks. Substituting a standard schedule-40 pipe for a high-pressure line can lead to catastrophic rupture, personnel hazards, or environmental compliance violations.

The **StockRescue Compatibility Specialist** performs rigorous, deterministic engineering evaluation of candidate substitute parts against the original equipment manufacturer (OEM) specifications. It checks dimensional tolerances, pressure and temperature ratings, material metallurgy (e.g., 316 Stainless Steel vs 304), and manufacturer certifications before any candidate offer is admitted into recovery planning.

---

## 2. Technical Architecture & Event Loop

- **Serverless Cloud Microcontainer**: Deployed natively on Agentverse with zero persistent polling loops.
- **Consolidated Mailbox Integration**: Shares physical mailbox identity with Supplier Discovery (`@sr-suppliers` / `agent1q0ac6fztvpvzuf0kegpek9wl5qnuv26f29aeny7rluygkg56z9d0gygf3vh`) under the Agentverse Basic 8-mailbox configuration.
- **Microsecond In-Memory Execution**: Validates candidate part interchangeability records in under 15 milliseconds using deterministic rule validation.
- **Deterministic Gatekeeper**: Ensures unverified or incompatible components are completely excluded from downstream purchase order drafts.

```
       +---------------------------------------------+
       |         StockRescue Coordinator             |
       +----------------------+----------------------+
                              |
                              | TaskRequest (task_type="compatibility")
                              v
       +---------------------------------------------+
       |      Compatibility Specialist (Cloud)       |
       |  - Requested SKU Canonicalization           |
       |  - Engineering Cross-Reference Lookup       |
       |  - Pressure, Material & Dimension Checks    |
       |  - Safe vs Excluded Offer Classification    |
       +----------------------+----------------------+
                              |
                              | TaskResponse (safe_offer_ids, excluded_offer_ids)
                              v
       +---------------------------------------------+
       |         StockRescue Coordinator             |
       +---------------------------------------------+
```

---

## 3. Communication Protocol & Message Schemas

### Protocol Identifier
`stockrescue-compatibility:1.0`

### Inbound Envelope: `TaskRequest`
```json
{
  "task_id": "comp-req-9912-e4d2",
  "task_type": "compatibility",
  "payload": {
    "case_id": "CASE-PIPE-001",
    "primary_sku": "PIPE-4IN-SCH40",
    "candidate_sku": "PIPE-4IN-SCH40-SEAMLESS",
    "assessments": [
      {
        "requested_sku": "PIPE-4IN-SCH40",
        "candidate_sku": "PIPE-4IN-SCH40-SEAMLESS",
        "status": "compatible",
        "evidence": "ASTM A106 Grade B seamless compliant with ASME B36.10M dimensions.",
        "assessed_by": "lead_plant_metallurgist",
        "assessed_at": "2026-11-14T00:00:00Z"
      }
    ]
  }
}
```

### Outbound Envelope: `TaskResponse`
```json
{
  "task_id": "comp-req-9912-e4d2",
  "task_type": "compatibility",
  "status": "completed",
  "payload": {
    "case_id": "CASE-PIPE-001",
    "records": [
      {
        "requested_sku": "PIPE-4IN-SCH40",
        "candidate_sku": "PIPE-4IN-SCH40-SEAMLESS",
        "status": "compatible",
        "safe": true,
        "evidence": ["ASTM A106 Grade B seamless compliant with ASME B36.10M dimensions."],
        "assessed_by": "lead_plant_metallurgist",
        "assessed_at": "2026-11-14T00:00:00Z"
      }
    ],
    "safe_offer_ids": ["OFFER-MIDWEST-001"],
    "excluded_offer_ids": []
  }
}
```

---

## 4. Deterministic Compatibility Rules

1. **Exact SKU Match**: If candidate SKU equals requested SKU, compatibility is unconditionally verified as safe.
2. **Substitute Verification**: Non-identical candidate SKUs require:
   - `decision.status == CompatibilityStatus.COMPATIBLE`
   - Verified engineering assessment author (`assessed_by` not empty, null, or `"unverified"`)
   - Documented engineering evidence string present in records.
3. **Fail-Closed Principle**: Any candidate part lacking verified positive compatibility is classified as unsafe and completely filtered out from purchase order drafts.
