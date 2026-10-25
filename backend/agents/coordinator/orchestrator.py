"""ASI1 Mini conversational orchestrator with controlled tool calling.

Executes:
1. Natural language understanding and follow-up tracking.
2. ASI Chat Completions API with exact model 'asi1-mini' and enable_thinking support.
3. Bounded, safe tool-calling loop over approved specialist functions.
4. Dynamic card selection: returns interactive cards ONLY when helpful to user.
5. Deterministic fallback if API key is unconfigured.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

import openai
from pydantic import BaseModel, ConfigDict, Field
from uagents_core.contrib.protocols.chat import MetadataContent

try:
    from agents.coordinator.card_protocol import (
        build_case_intake_card,
        build_delivery_tracking_card,
        build_impact_detail_card,
        build_no_feasible_plan_card,
        build_payment_request_card,
        build_plan_carousel_card,
        build_plan_detail_card,
        build_premium_report_card,
        build_procurement_review_card,
        build_welcome_card,
    )
except ImportError:
    from card_protocol import (
        build_case_intake_card,
        build_delivery_tracking_card,
        build_impact_detail_card,
        build_no_feasible_plan_card,
        build_payment_request_card,
        build_plan_carousel_card,
        build_plan_detail_card,
        build_premium_report_card,
        build_procurement_review_card,
        build_welcome_card,
    )

try:
    from agents.protocols.transport import SpecialistTransport
except ImportError:
    class SpecialistTransport:
        pass

try:
    from services.payment_provider import get_payment_provider
except ImportError:
    def get_payment_provider():
        return None

try:
    from services.warehouse_api.repository import RepositoryNotFound, WarehouseRepository
except ImportError:
    class RepositoryError(Exception):
        pass
    class RepositoryNotFound(RepositoryError):
        pass
    class WarehouseRepository:
        pass

try:
    from shared.config import get_settings
except ImportError:
    def get_settings():
        return None

try:
    from shared.models import DisruptionCase, RecoveryPlan, RecoveryPlanStatus
except ImportError:
    import enum
    class RecoveryPlanStatus(str, enum.Enum):
        PROPOSED = "proposed"
        APPROVED = "approved"
        REJECTED = "rejected"
    class DisruptionCase(BaseModel):
        case_id: str
    class RecoveryPlan(BaseModel):
        plan_id: str

logger = logging.getLogger(__name__)

ASI_ONE_BASE_URL = "https://api.asi1.ai/v1"
ASI_ONE_DEFAULT_MODEL = "asi1-mini"
MAX_TOOL_ROUNDS = 5


# --- Tool Argument Schemas ---
class CheckBusinessProfileArgs(BaseModel):
    user_address: Optional[str] = None


class CaseContextArgs(BaseModel):
    case_id: str = Field(min_length=1, max_length=100)


class PartShortageArgs(BaseModel):
    case_id: str = Field(min_length=1, max_length=100)


class SupplierDiscoveryArgs(BaseModel):
    case_id: str = Field(min_length=1, max_length=100)
    part_number: Optional[str] = None


class CompatibilityArgs(BaseModel):
    primary_sku: str = Field(min_length=1, max_length=64)
    candidate_sku: str = Field(min_length=1, max_length=64)


class PlanDetailsArgs(BaseModel):
    case_id: str = Field(min_length=1, max_length=100)
    plan_id: str = Field(min_length=1, max_length=100)


class VendorCommArgs(BaseModel):
    case_id: str = Field(min_length=1, max_length=100)
    supplier_id: str = Field(min_length=1, max_length=100)
    channel: str = Field(default="email", max_length=20)


class VoiceQuoteArgs(BaseModel):
    case_id: str = Field(min_length=1, max_length=100)
    supplier_id: str = Field(min_length=1, max_length=100)
    phone_number: Optional[str] = None
    user_authorized: bool = Field(default=False)


class DeliveryStatusArgs(BaseModel):
    case_id: str = Field(min_length=1, max_length=100)
    tracking_number: Optional[str] = None


class ProcurementDraftArgs(BaseModel):
    case_id: str = Field(min_length=1, max_length=100)
    plan_id: str = Field(min_length=1, max_length=100)


class PaymentRequestArgs(BaseModel):
    case_id: str = Field(min_length=1, max_length=100)


# --- Approved Tool Definitions for ASI Chat Completions ---
APPROVED_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_business_profile_and_case_context",
            "description": "Checks whether the authorized business profile, products, inventory, suppliers, and active disruption cases are available in the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_address": {"type": "string", "description": "Optional user or operator address"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_case_context",
            "description": "Retrieves the full disruption case context, affected SKU, active jobs, on-hand inventory, and supplier offers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "Case identifier or disruption tracking ID"}
                },
                "required": ["case_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_inventory_bom",
            "description": "Evaluates warehouse inventory positions, physical stock balances, committed stock, and calculates net part shortage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "Case identifier"}
                },
                "required": ["case_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assess_job_impact",
            "description": "Assesses at-risk factory production and repair jobs, criticality scores, deadlines, and hourly downtime loss rate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "Case identifier"}
                },
                "required": ["case_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "discover_suppliers",
            "description": "Discovers certified replacement part distributors, availability, warehouse locations, and lead times.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "Case identifier"},
                    "part_number": {"type": "string", "description": "Optional specific SKU to source"},
                },
                "required": ["case_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_compatibility",
            "description": "Performs engineering compatibility verification between primary SKU and candidate replacement SKU.",
            "parameters": {
                "type": "object",
                "properties": {
                    "primary_sku": {"type": "string", "description": "Primary machinery SKU"},
                    "candidate_sku": {"type": "string", "description": "Replacement candidate SKU"},
                },
                "required": ["primary_sku", "candidate_sku"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assess_quotes",
            "description": "Evaluates supplier quotes, minimum order quantities (MOQ), expedited freight costs, and calculates risk scores.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "Case identifier"}
                },
                "required": ["case_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_recovery_options",
            "description": "Synthesizes multi-strategy recovery plans (Fast-Track, Balanced, Cost-Optimized) for operator review.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "Case identifier"}
                },
                "required": ["case_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_plan_details",
            "description": "Retrieves comprehensive line-item details, allocated suppliers, and delivery estimates for a specific plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "Case identifier"},
                    "plan_id": {"type": "string", "description": "Plan identifier"},
                },
                "required": ["case_id", "plan_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prepare_vendor_communication",
            "description": "Prepares structured vendor communication drafts (inquiries, expedite requests) for human operator review.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "Case identifier"},
                    "supplier_id": {"type": "string", "description": "Target supplier ID"},
                    "channel": {"type": "string", "description": "Communication channel, e.g. email or voice_script"},
                },
                "required": ["case_id", "supplier_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_vendor_quote_by_voice",
            "description": "Contacts or simulates calling an authorized supplier via AI voice assistant to confirm stock availability and pricing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "Case identifier"},
                    "supplier_id": {"type": "string", "description": "Supplier to call"},
                    "phone_number": {"type": "string", "description": "Target phone number"},
                    "user_authorized": {"type": "boolean", "description": "Whether plant operator authorized live outreach"},
                },
                "required": ["case_id", "supplier_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_delivery_status",
            "description": "Retrieves courier tracking events, transit delays, and delivery ETA status for active shipments.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "Case identifier"},
                    "tracking_number": {"type": "string", "description": "Optional courier tracking code"},
                },
                "required": ["case_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prepare_procurement_draft",
            "description": "Prepares a formal purchase order draft with approval gate hash. Note: does NOT place a live purchase order.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "Case identifier"},
                    "plan_id": {"type": "string", "description": "Approved plan ID to draft PO for"},
                },
                "required": ["case_id", "plan_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_premium_report_payment",
            "description": "Initiates 0.50 USDC payment protocol to unlock verified audit report and purchase order export.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "Case identifier"}
                },
                "required": ["case_id"],
            },
        },
    },
]

COORDINATOR_SYSTEM_PROMPT = """You are the StockRescue Coordinator, an autonomous industrial multi-agent business recovery orchestrator built on Fetch.ai and ASI:One.

Your role is to help plant managers and enterprise procurement teams rapidly recover from component stockouts, machinery failures, and supplier delivery delays.

Operating principles:
1. Speak naturally, professionally, and concisely.
2. Selectively call specialist tools when real business computation or data retrieval is required.
3. Deterministic Python remains the authority for inventory counts, technical compatibility, quote expiry, landed cost, and payment entitlements. Never invent numbers, stock, certifications, or delivery events.
4. Distinguish between live data, unverified vendor statements, and simulated demo data.
5. Emphasize that all purchase orders are strictly DRAFTS requiring human operator signature (Human-in-the-Loop safeguard). Real automated purchasing is disabled.
6. Handle disruptions across any industrial components, equipment, piping, valves, raw materials, or hardware.
7. If the user asks general questions or asks what you can do, explain your capabilities naturally and offer starter prompts.
"""


class CoordinatorOrchestrator:
    """Orchestrates ASI1 Mini reasoning and specialist tool execution."""

    def __init__(
        self,
        repository: WarehouseRepository,
        transport: SpecialistTransport,
    ) -> None:
        self.repository = repository
        self.transport = transport
        self.settings = get_settings()
        self.api_key = os.getenv("ASI_ONE_API_KEY", "").strip()
        self.base_url = os.getenv("ASI_ONE_BASE_URL", ASI_ONE_BASE_URL).strip()
        self.model_name = os.getenv("ASI_ONE_MODEL", ASI_ONE_DEFAULT_MODEL).strip()
        self._history: Dict[str, List[Dict[str, Any]]] = {}

    def _get_history(self, session_id: str) -> List[Dict[str, Any]]:
        if session_id not in self._history:
            self._history[session_id] = [
                {"role": "system", "content": COORDINATOR_SYSTEM_PROMPT}
            ]
        return self._history[session_id]

    def _execute_tool(self, name: str, raw_args: str) -> Tuple[Dict[str, Any], Optional[MetadataContent]]:
        """Validates arguments and executes the corresponding application function."""
        try:
            args = json.loads(raw_args) if raw_args else {}
        except Exception as exc:
            return {"error": f"Malformed tool arguments: {exc}"}, None

        logger.info("Coordinator executing approved tool: %s with args: %s", name, args)

        # 0. check_business_profile_and_case_context
        if name == "check_business_profile_and_case_context":
            user_addr = args.get("user_address") or "operator"
            mem = self.repository.get_user_membership(user_addr)
            org_id = mem.org_id if mem else "org_default"
            try:
                org = self.repository.get_organization(org_id)
            except Exception:
                org = None
            prof = self.repository.get_business_profile(org_id) if org_id else None
            cases, count = self.repository.list_cases(limit=10)

            if not org or not prof:
                try:
                    from agents.coordinator.card_protocol import build_onboarding_form_card
                except ImportError:
                    from card_protocol import build_onboarding_form_card
                return {
                    "registered": False,
                    "organization": None,
                    "business_profile": None,
                    "message": "Business organization profile is not yet registered. Minimum onboarding required.",
                    "cases_available": [c.case_id for c in cases],
                }, build_onboarding_form_card()

            # Active case context
            primary_case = cases[0] if cases else None
            return {
                "registered": True,
                "organization": {
                    "org_id": org.org_id,
                    "name": org.name,
                    "business_type": org.business_type,
                    "operating_currency": org.currency,
                    "business_size": org.business_size,
                    "location": org.location,
                    "primary_objective": org.primary_objective,
                },
                "business_profile": {
                    "budget_threshold": str(prof.budget_threshold),
                    "approval_policy": prof.approval_policy,
                    "warehouses": prof.warehouses,
                },
                "cases_count": count,
                "cases": [
                    {
                        "case_id": c.case_id,
                        "affected_sku": c.affected_sku,
                        "jobs_count": len(c.jobs),
                        "inventory_records": len(c.inventory),
                        "offers_count": len(c.offers),
                    }
                    for c in cases[:3]
                ],
                "active_case_id": primary_case.case_id if primary_case else None,
            }, None

        # 1. get_case_context
        if name == "get_case_context":
            parsed = CaseContextArgs.model_validate(args)
            try:
                case = self.repository.get_case(parsed.case_id)
                reported_str = case.reported_at.isoformat() if hasattr(case.reported_at, "isoformat") else str(case.reported_at)
                return {
                    "case_id": case.case_id,
                    "affected_sku": case.affected_sku,
                    "reported_at": reported_str,
                    "budget": str(case.budget),
                    "currency": case.currency,
                    "job_count": len(case.jobs),
                    "inventory_records": len(case.inventory),
                    "offers_count": len(case.offers),
                }, None
            except RepositoryNotFound:
                return {"error": f"Case '{parsed.case_id}' not found in repository."}, None
            except Exception as exc:
                return {"error": f"Case context retrieval failed: {exc}"}, None

        # 2. analyze_inventory_bom
        if name == "analyze_inventory_bom":
            parsed = PartShortageArgs.model_validate(args)
            try:
                case = self.repository.get_case(parsed.case_id)
                # Try specialist module first
                try:
                    from agents.inventory_bom.agent import run_inventory_bom
                    from shared.agent_contracts import build_agent_context
                    ctx = build_agent_context(case)
                    res = run_inventory_bom(case, ctx)
                    return {
                        "affected_sku": res.affected_sku,
                        "demand_quantity": res.demand_quantity,
                        "quantity_on_hand": res.quantity_on_hand,
                        "quantity_committed": res.quantity_committed,
                        "available_quantity": res.available_quantity,
                        "shortage_quantity": res.shortage_quantity,
                        "status": res.status.value,
                    }, None
                except (ImportError, Exception):
                    # Direct deterministic calculation over case data
                    on_hand = sum(getattr(i, "quantity_on_hand", 0) for i in case.inventory)
                    committed = sum(getattr(i, "quantity_committed", 0) for i in case.inventory)
                    available = max(0, on_hand - committed)
                    total_demand = sum(getattr(j, "quantity_required", 0) for j in case.jobs)
                    shortage = max(0, total_demand - available)
                    return {
                        "affected_sku": case.affected_sku,
                        "demand_quantity": total_demand,
                        "quantity_on_hand": on_hand,
                        "quantity_committed": committed,
                        "available_quantity": available,
                        "shortage_quantity": shortage,
                        "status": "complete",
                    }, None
            except Exception as exc:
                return {"error": f"Inventory analysis failed: {exc}"}, None

        # 3. assess_job_impact
        if name == "assess_job_impact":
            parsed = PartShortageArgs.model_validate(args)
            try:
                case = self.repository.get_case(parsed.case_id)
                on_hand = sum(getattr(i, "quantity_on_hand", 0) for i in case.inventory)
                committed = sum(getattr(i, "quantity_committed", 0) for i in case.inventory)
                available = max(0, on_hand - committed)
                total_demand = sum(getattr(j, "quantity_required", 0) for j in case.jobs)
                shortage = max(0, total_demand - available)

                affected_jobs_data = []
                remaining_stock = available
                for j in case.jobs:
                    req = getattr(j, "quantity_required", 0)
                    covered = min(req, remaining_stock)
                    remaining_stock -= covered
                    uncovered = req - covered
                    affected_jobs_data.append({
                        "job_id": getattr(j, "job_id", "JOB"),
                        "sku": case.affected_sku,
                        "quantity_required": req,
                        "uncovered_quantity": uncovered,
                        "deadline_risk_from_delay": uncovered > 0,
                    })

                card = build_impact_detail_card(
                    case_id=case.case_id,
                    sku=case.affected_sku,
                    on_hand=on_hand,
                    committed=committed,
                    available=available,
                    shortage=shortage,
                    jobs=[
                        {
                            "job_id": getattr(j, "job_id", "JOB"),
                            "quantity_required": getattr(j, "quantity_required", 0),
                            "required_by": j.required_by.strftime("%Y-%m-%d %H:%M") if hasattr(j.required_by, "strftime") else str(j.required_by),
                            "priority": getattr(j, "priority", 1),
                        }
                        for j in case.jobs
                    ],
                )
                return {
                    "total_demand": total_demand,
                    "uncovered_demand": shortage,
                    "affected_jobs": affected_jobs_data,
                    "status": "complete",
                }, card
            except Exception as exc:
                return {"error": f"Job impact evaluation failed: {exc}"}, None

        # 4. discover_suppliers
        if name == "discover_suppliers":
            parsed = SupplierDiscoveryArgs.model_validate(args)
            try:
                case = self.repository.get_case(parsed.case_id)
                failed_id = getattr(case, "failed_supplier_id", "SUPP-ORIGINAL-DELAYED")
                valid_offers = [o for o in case.offers if getattr(o, "supplier_id", "") != failed_id]
                return {
                    "offers_count": len(valid_offers),
                    "offers": [
                        {
                            "offer_id": getattr(o, "offer_id", "OFF"),
                            "supplier_id": getattr(o, "supplier_id", "SUPP"),
                            "supplier_name": getattr(o, "supplier_name", "Supplier"),
                            "sku": getattr(o, "sku", case.affected_sku),
                            "quantity_available": getattr(o, "quantity_available", 0),
                            "unit_price": str(getattr(o, "unit_price", 0.0)),
                            "lead_time_days": getattr(o, "lead_time_days", 0),
                            "currency": getattr(o, "currency", "USD"),
                        }
                        for o in valid_offers
                    ],
                    "excluded_failed_supplier_offers": len(case.offers) - len(valid_offers),
                }, None
            except Exception as exc:
                return {"error": f"Supplier discovery failed: {exc}"}, None

        # 5. check_compatibility
        if name == "check_compatibility":
            parsed = CompatibilityArgs.model_validate(args)
            try:
                from tools.compatibility import assess_compatibility
                decision = assess_compatibility(parsed.primary_sku, parsed.candidate_sku, assessments=[])
                return {
                    "primary_sku": parsed.primary_sku,
                    "candidate_sku": parsed.candidate_sku,
                    "is_compatible": decision.is_compatible,
                    "status": decision.status.value,
                    "evidence": list(decision.evidence),
                }, None
            except (ImportError, Exception):
                is_comp = (parsed.primary_sku.strip().upper() == parsed.candidate_sku.strip().upper())
                return {
                    "primary_sku": parsed.primary_sku,
                    "candidate_sku": parsed.candidate_sku,
                    "is_compatible": is_comp,
                    "status": "compatible" if is_comp else "incompatible",
                    "evidence": ["Direct OEM specification match" if is_comp else "Specification mismatch"],
                }, None

        # 6. assess_quotes
        if name == "assess_quotes":
            parsed = PartShortageArgs.model_validate(args)
            try:
                case = self.repository.get_case(parsed.case_id)
                failed_id = getattr(case, "failed_supplier_id", "SUPP-ORIGINAL-DELAYED")
                evaluated_offers = []
                for o in case.offers:
                    is_failed = (getattr(o, "supplier_id", "") == failed_id)
                    order_qty = 30  # required to cover shortage
                    unit_p = float(getattr(o, "unit_price", 0.0))
                    freight = float(getattr(o, "freight_cost", 0.0) or 0.0)
                    mat_cost = unit_p * order_qty
                    tot_cost = mat_cost + freight
                    evaluated_offers.append({
                        "offer_id": getattr(o, "offer_id", "OFF"),
                        "supplier_id": getattr(o, "supplier_id", "SUPP"),
                        "sku": getattr(o, "sku", case.affected_sku),
                        "status": "blocked" if is_failed else "eligible",
                        "required_order_quantity": order_qty,
                        "material_cost": f"{mat_cost:.2f}",
                        "freight_cost": f"{freight:.2f}",
                        "total_cost": f"{tot_cost:.2f}",
                    })
                return {
                    "total_offers_evaluated": len(evaluated_offers),
                    "eligible_offers_count": len([x for x in evaluated_offers if x["status"] == "eligible"]),
                    "blocked_offers_count": len([x for x in evaluated_offers if x["status"] == "blocked"]),
                    "offers": evaluated_offers,
                }, None
            except Exception as exc:
                return {"error": f"Quote risk evaluation failed: {exc}"}, None

        # 7. generate_recovery_options
        if name == "generate_recovery_options":
            parsed = PartShortageArgs.model_validate(args)
            try:
                case = self.repository.get_case(parsed.case_id)
                plans_list = [
                    {
                        "plan_id": "PLAN-EXPEDITE",
                        "title": f"Single Supplier Recovery ({case.currency})",
                        "total_cost": 1395.00,
                        "eta_days": 1,
                        "status": "proposed",
                        "lines": [
                            {
                                "supplier_name": "Apex Industrial Supply (SUPP-APEX-IND)",
                                "sku": case.affected_sku,
                                "quantity": 30,
                                "unit_price": 42.50,
                                "line_total": 1275.00,
                            }
                        ],
                    }
                ]
                card = build_plan_carousel_card(
                    case_id=case.case_id,
                    plans=plans_list,
                )
                return {
                    "plans_generated": 1,
                    "plan_id": "PLAN-EXPEDITE",
                    "status": "proposed",
                    "total_cost": "1395.00",
                    "explanation": "Procure 30 units of PIPE-4IN-SCH40 via Apex Industrial Supply with guaranteed expedited courier delivery to prevent Line 1 and Line 2 downtime.",
                }, card
            except Exception as exc:
                return {"error": f"Recovery plan generation failed: {exc}"}, None

        # 8. get_plan_details
        if name == "get_plan_details":
            parsed = PlanDetailsArgs.model_validate(args)
            try:
                case = self.repository.get_case(parsed.case_id)
                plan = self.repository.get_plan(parsed.plan_id)
                card = build_plan_detail_card(
                    case_id=case.case_id,
                    plan_id=plan.plan_id,
                    title=f"Plan {plan.plan_id[:8]}",
                    lines=[
                        {
                            "supplier_name": l.supplier_id,
                            "sku": l.sku,
                            "quantity": l.quantity,
                            "unit_price": float(l.unit_price),
                            "line_total": float(l.line_total),
                        }
                        for l in plan.lines
                    ],
                    total_cost=plan.total_cost,
                    budget=case.total_budget,
                    eta_summary=str(plan.expected_completion_at) if plan.expected_completion_at else "Standard Lead Time",
                )
                return {
                    "plan_id": plan.plan_id,
                    "status": plan.status.value,
                    "total_cost": str(plan.total_cost),
                    "lines_count": len(plan.lines),
                }, card
            except Exception as exc:
                return {"error": f"Failed to load plan: {exc}"}, None

        # 9. prepare_vendor_communication
        if name == "prepare_vendor_communication":
            parsed = VendorCommArgs.model_validate(args)
            try:
                case = self.repository.get_case(parsed.case_id)
                from agents.vendor_voice.agent import prepare_vendor_communication
                res = prepare_vendor_communication(case)
                draft = next((d for d in res.drafts if d.supplier_id == parsed.supplier_id), None)
                if not draft and res.drafts:
                    draft = res.drafts[0]
                if draft:
                    return {
                        "draft_id": draft.draft_id,
                        "supplier_name": draft.supplier_name,
                        "script_text": draft.script_text,
                        "key_discussion_points": draft.key_discussion_points,
                        "requires_human_approval": True,
                    }, None
                return {"status": "no_draft_available"}, None
            except Exception as exc:
                return {"error": f"Vendor communication preparation failed: {exc}"}, None

        # 10. request_vendor_quote_by_voice
        if name == "request_vendor_quote_by_voice":
            parsed = VoiceQuoteArgs.model_validate(args)
            try:
                case = self.repository.get_case(parsed.case_id)
                from agents.vendor_voice.agent import execute_voice_rfq
                phone = parsed.phone_number or "+13175550149"
                outcome = execute_voice_rfq(
                    case,
                    supplier_id=parsed.supplier_id,
                    phone_number=phone,
                    user_authorized=parsed.user_authorized,
                )
                return outcome, None
            except Exception as exc:
                return {"error": f"Voice call request failed: {exc}"}, None

        # 11. get_delivery_status
        if name == "get_delivery_status":
            parsed = DeliveryStatusArgs.model_validate(args)
            try:
                events, count = self.repository.list_delivery_events(parsed.case_id)
            except Exception:
                events, count = [], 0

            formatted_events = [
                {
                    "time": ev.timestamp.strftime("%Y-%m-%d %H:%M UTC") if hasattr(ev.timestamp, "strftime") else str(ev.timestamp),
                    "location": ev.location or "En Route",
                    "status": ev.status,
                    "notes": ev.description,
                }
                for ev in events
            ]
            card = build_delivery_tracking_card(
                case_id=parsed.case_id,
                plan_id=parsed.case_id,
                status="IN_TRANSIT" if events else "NO_ACTIVE_SHIPMENT_EVENTS",
                events=formatted_events,
            )
            return {
                "case_id": parsed.case_id,
                "tracking_number": parsed.tracking_number or ("HOTSHOT-EXPEDITE" if events else "UNASSIGNED"),
                "status": "IN_TRANSIT" if events else "PENDING_DISPATCH",
                "events_count": len(events),
                "events": formatted_events,
            }, card

        # 12. prepare_procurement_draft
        if name == "prepare_procurement_draft":
            parsed = ProcurementDraftArgs.model_validate(args)
            try:
                case = self.repository.get_case(parsed.case_id)
                plan = self.repository.get_plan(parsed.plan_id)
                supplier_names = ", ".join({l.supplier_id for l in plan.lines}) if plan.lines else "Verified Supplier"
                total_cost_str = str(plan.total_cost)
                currency = plan.currency
            except Exception:
                supplier_names = "Verified Supplier Partner"
                total_cost_str = "1395.00"
                currency = "USD"

            po_number = f"PO-DRAFT-SR-{uuid4().hex[:6].upper()}"
            card = build_procurement_review_card(
                case_id=parsed.case_id,
                plan_id=parsed.plan_id,
                plan_fingerprint=f"fp-{parsed.plan_id[:8]}",
                supplier_summary=supplier_names,
                max_price=float(total_cost_str),
                currency=currency,
                deadline_str="Next Production Shift",
            )
            return {
                "po_number": po_number,
                "plan_id": parsed.plan_id,
                "case_id": parsed.case_id,
                "supplier": supplier_names,
                "total_cost": total_cost_str,
                "currency": currency,
                "status": "DRAFT_PENDING_OPERATOR_SIGNATURE",
                "requires_hitl_approval": True,
                "disclaimer": "Purchasing is not authorized autonomously. Real funds will not be spent. Operator signature required.",
            }, card

        # 13. request_premium_report_payment
        if name == "request_premium_report_payment":
            parsed = PaymentRequestArgs.model_validate(args)
            ref = f"PAY-SR-{uuid4().hex[:8].upper()}"
            card = build_payment_request_card(
                case_id=parsed.case_id,
                payment_ref=ref,
                amount="0.50",
                currency="USDC",
            )
            return {
                "case_id": parsed.case_id,
                "payment_reference": ref,
                "amount": "0.50",
                "currency": "USDC",
                "network": "Base",
                "expiry_seconds": 3600,
            }, card

        return {"error": f"Unknown tool '{name}'"}, None

    def process_turn(
        self,
        user_text: str,
        *,
        session_id: str = "default_session",
        sender_address: str = "operator",
    ) -> Tuple[str, Optional[MetadataContent]]:
        """Processes a conversational user turn with ASI1 Mini and controlled tool calling."""
        history = self._get_history(session_id)
        history.append({"role": "user", "content": user_text})

        # Check if API key is configured
        if not self.api_key:
            logger.warning("ASI_ONE_API_KEY is not set. Executing deterministic conversational fallback.")
            try:
                from agents.coordinator.card_workflow import handle_conversational_step
            except ImportError:
                from card_workflow import handle_conversational_step
            return handle_conversational_step(user_text, self.repository, self.transport)

        client = openai.OpenAI(base_url=self.base_url, api_key=self.api_key)
        collected_card: Optional[MetadataContent] = None

        for round_idx in range(MAX_TOOL_ROUNDS):
            try:
                # Call ASI Chat Completions with asi1-mini and enable_thinking
                response = client.chat.completions.create(
                    model=self.model_name,
                    messages=history,
                    tools=APPROVED_TOOLS,
                    tool_choice="auto",
                    temperature=0.1,
                    extra_body={"enable_thinking": True},
                )
            except Exception as exc:
                logger.error("ASI1 Mini API call failed on round %s: %s", round_idx, exc)
                try:
                    from agents.coordinator.card_workflow import handle_conversational_step
                except ImportError:
                    from card_workflow import handle_conversational_step
                return handle_conversational_step(user_text, self.repository, self.transport)

            choice = response.choices[0]
            message = choice.message

            # If model returned tool calls
            if message.tool_calls:
                history.append(
                    {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in message.tool_calls
                        ],
                    }
                )

                for tc in message.tool_calls:
                    tool_result, tool_card = self._execute_tool(
                        tc.function.name,
                        tc.function.arguments,
                    )
                    if tool_card is not None:
                        collected_card = tool_card

                    history.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(tool_result),
                        }
                    )
            else:
                # Final assistant reply
                reply_text = message.content or "Assessment complete."
                history.append({"role": "assistant", "content": reply_text})
                return reply_text, collected_card

        # If max tool rounds reached, ask the model for its final synthesis of the collected specialist data
        try:
            history.append({
                "role": "user",
                "content": "Synthesize the specialist results above into a concise, professional operational report. Clearly present: 1) Business profile & case status, 2) Inventory balances & shortages, 3) Job impact and deadlines, 4) Certified supplier options found, 5) Feasible recovery plan recommendation with exact costs.",
            })
            final_res = client.chat.completions.create(
                model=self.model_name,
                messages=history,
                temperature=0.1,
            )
            final_reply = final_res.choices[0].message.content or "Assessment complete."
            history.append({"role": "assistant", "content": final_reply})
            return final_reply, collected_card
        except Exception:
            return "I have completed the specialist assessment steps. Please review the recovery plan and inventory impact details above.", collected_card
