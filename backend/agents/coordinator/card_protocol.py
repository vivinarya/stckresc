"""Interactive card definitions and builders for ASI:One Agent Chat Protocol (ACP).

Implements the official Agentverse ACP Card Protocol v1 using strict pydantic schemas
from uagents_core.contrib.protocols.chat.cards.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from uagents_core.contrib.protocols.chat import MetadataContent
from uagents_core.contrib.protocols.chat.cards import (
    CarouselBadge,
    CarouselCardPayload,
    CarouselItem,
    CtaAction,
    DetailCardPayload,
    DetailSummaryRow,
    FormCardPayload,
    FormField,
    FormFieldOption,
    ReviewCardPayload,
    ReviewSummaryRow,
    create_card_content,
)


# --- 1. State A: Welcome / Choice Form Card ---
def build_welcome_card() -> MetadataContent:
    """Guided choice card when user opens chat or asks for help."""
    payload = FormCardPayload(
        title="Welcome to StockRescue",
        fields=[
            FormField(
                name="action",
                kind="select",
                label="Select Action",
                required=True,
                options=[
                    FormFieldOption(
                        value="report_disruption",
                        label="Report Component Delay / Shortage",
                    ),
                    FormFieldOption(
                        value="setup_business",
                        label="Register & Onboard Business Profile",
                    ),
                    FormFieldOption(
                        value="enter_case_id",
                        label="Assess Custom Disruption Case by ID",
                    ),
                    FormFieldOption(
                        value="review_jobs",
                        label="Review At-Risk Production Jobs",
                    ),
                    FormFieldOption(
                        value="track_delivery",
                        label="Track Delivery & Shipment Events",
                    ),
                ],
            )
        ],
        submit_cta=CtaAction(
            label="Continue",
            selection={"action": "report_disruption"},
            primary=True,
        ),
    )
    return create_card_content(payload, card_id=uuid4())


# --- 1b. State A2: Business Onboarding Form Card ---
def build_onboarding_form_card() -> MetadataContent:
    """Business onboarding form to capture persistent organization profile."""
    payload = FormCardPayload(
        title="StockRescue Business Onboarding",
        fields=[
            FormField(
                name="company_name",
                kind="text",
                label="Business / Company Name",
                placeholder="e.g. Apex Precision Manufacturing",
                required=True,
            ),
            FormField(
                name="business_type",
                kind="text",
                label="Industry / Business Type",
                placeholder="e.g. Industrial Automation, Aerospace, Automotive",
                required=True,
            ),
            FormField(
                name="location",
                kind="text",
                label="Operating City & Region",
                placeholder="e.g. Pune, Maharashtra",
                required=True,
            ),
            FormField(
                name="currency",
                kind="select",
                label="Preferred Currency",
                required=True,
                options=[
                    FormFieldOption(value="INR", label="INR (₹)"),
                    FormFieldOption(value="USD", label="USD ($)"),
                    FormFieldOption(value="EUR", label="EUR (€)"),
                    FormFieldOption(value="GBP", label="GBP (£)"),
                ],
            ),
            FormField(
                name="business_size",
                kind="select",
                label="Business Scale",
                required=True,
                options=[
                    FormFieldOption(value="small", label="Small (1-50 employees, single warehouse)"),
                    FormFieldOption(value="medium", label="Medium (50-500 employees, multi-site)"),
                    FormFieldOption(value="large", label="Large (500+ employees, enterprise ERP)"),
                ],
            ),
            FormField(
                name="primary_objective",
                kind="text",
                label="Primary Operational Objective",
                placeholder="e.g. Zero production line stoppage, SLA compliance",
                required=False,
            ),
        ],
        submit_cta=CtaAction(
            label="Save Organization Profile",
            selection={"action": "submit_business_profile"},
            primary=True,
        ),
    )
    return create_card_content(payload, card_id=uuid4())


# --- 2. State B: Case Intake Form Card ---
def build_case_intake_card() -> MetadataContent:
    """Intake form to collect case details when missing."""
    payload = FormCardPayload(
        title="StockRescue Case Intake",
        fields=[
            FormField(
                name="case_id",
                kind="text",
                label="Disruption Case ID",
                placeholder="e.g. CASE-2026-001",
                required=True,
            )
        ],
        submit_cta=CtaAction(
            label="Analyze Recovery",
            selection={"action": "recover"},
            primary=True,
        ),
    )
    return create_card_content(payload, card_id=uuid4())


# --- 3. State C: Inventory & Job Impact Detail Card ---
def build_impact_detail_card(
    case_id: str,
    sku: str,
    on_hand: int,
    committed: int,
    available: int,
    shortage: int,
    jobs: list[dict[str, Any]],
) -> MetadataContent:
    """Detailed summary of inventory and job impact."""
    summary_rows = [
        DetailSummaryRow(label="Case ID", value=str(case_id)),
        DetailSummaryRow(label="Affected SKU", value=str(sku)),
        DetailSummaryRow(label="Physical On-Hand", value=f"{on_hand} units"),
        DetailSummaryRow(label="Committed to Jobs", value=f"{committed} units"),
        DetailSummaryRow(label="Available Stock", value=f"{available} units"),
        DetailSummaryRow(label="Net Shortage", value=f"{shortage} units (CRITICAL)"),
    ]

    for idx, j in enumerate(jobs[:3], 1):
        summary_rows.append(
            DetailSummaryRow(
                label=f"At-Risk Job {idx}",
                value=f"{j.get('job_id', 'JOB')} ({j.get('quantity_required', 0)} req)",
            )
        )

    payload = DetailCardPayload(
        title=f"Inventory & Job Impact: {case_id}",
        summary_rows=summary_rows,
        ctas=[
            CtaAction(
                label="Compare Sourcing Strategies",
                selection={"action": "compare_plans", "case_id": case_id},
                primary=True,
            ),
        ],
    )
    return create_card_content(payload, card_id=uuid4())


# --- 4. State D: Recovery Strategy Comparison Carousel Card ---
def build_plan_carousel_card(
    case_id: str,
    plans: list[dict[str, Any]],
) -> MetadataContent:
    """Carousel comparing deterministic recovery plan options."""
    items = []
    for idx, p in enumerate(plans):
        plan_id = p.get("plan_id", f"PLAN-0{idx+1}")
        title = p.get("title", f"Plan {plan_id}")
        cost = p.get("total_cost", 0)
        eta_days = p.get("eta_days", 0)
        status = p.get("status", "Feasible")

        badges = [
            CarouselBadge(label=f"INR {cost:,.2f}", variant="success"),
            CarouselBadge(label=f"ETA {eta_days}d", variant="info"),
        ]

        items.append(
            CarouselItem(
                id=str(plan_id),
                title=str(title),
                subtitle=f"Cost: INR {cost:,.2f} | Status: {status}",
                badges=badges,
                primary_cta=CtaAction(
                    label="View Plan Details",
                    selection={
                        "action": "view_plan",
                        "case_id": case_id,
                        "plan_id": plan_id,
                    },
                    primary=True,
                ),
            )
        )

    if not items:
        items.append(
            CarouselItem(
                id="PLAN-DEFAULT",
                title="Single Supplier Plan",
                subtitle="Emergency stock sourcing",
                primary_cta=CtaAction(
                    label="View",
                    selection={"action": "view_plan", "case_id": case_id, "plan_id": "P1"},
                    primary=True,
                ),
            )
        )

    payload = CarouselCardPayload(
        title="Feasible Recovery Strategies",
        subtitle=f"Deterministic options evaluated for {case_id}",
        items=items,
    )
    return create_card_content(payload, card_id=uuid4())


# --- 5. State E: Plan Details Card ---
def build_plan_detail_card(
    case_id: str,
    plan_id: str,
    title: str,
    lines: list[dict[str, Any]],
    total_cost: Decimal | float,
    budget: Decimal | float,
    eta_summary: str,
) -> MetadataContent:
    """Detailed breakdown of a specific selected plan."""
    summary_rows = [
        DetailSummaryRow(label="Case ID", value=str(case_id)),
        DetailSummaryRow(label="Plan ID", value=str(plan_id)),
        DetailSummaryRow(label="Total Landed Cost", value=f"INR {total_cost:,.2f}"),
        DetailSummaryRow(label="Budget Limit", value=f"INR {budget:,.2f}"),
        DetailSummaryRow(label="Timeline Feasibility", value=str(eta_summary)),
        DetailSummaryRow(label="Compatibility", value="100% Certified / Exact Match"),
    ]

    for idx, line in enumerate(lines[:3], 1):
        summary_rows.append(
            DetailSummaryRow(
                label=f"Supplier Line {idx}",
                value=f"{line.get('supplier_name', 'Supplier')}: {line.get('quantity', 0)}x {line.get('sku', '')}",
            )
        )

    payload = DetailCardPayload(
        title=f"Plan Details: {title}",
        summary_rows=summary_rows,
        ctas=[
            CtaAction(
                label="Prepare Draft PO for Review",
                selection={
                    "action": "request_approval",
                    "case_id": case_id,
                    "plan_id": plan_id,
                },
                primary=True,
            ),
            CtaAction(
                label="Back to Comparison",
                selection={"action": "compare_plans", "case_id": case_id},
                primary=False,
            ),
        ],
    )
    return create_card_content(payload, card_id=uuid4())


# --- 6. State F: Review Card for Procurement Approval ---
def build_procurement_review_card(
    case_id: str,
    plan_id: str,
    plan_fingerprint: str,
    supplier_summary: str,
    max_price: Decimal | float,
    currency: str,
    deadline_str: str,
) -> MetadataContent:
    """Review card with strict human-in-the-loop approval guardrails."""
    summary_rows = [
        ReviewSummaryRow(label="Disruption Case", value=str(case_id)),
        ReviewSummaryRow(label="Plan Fingerprint", value=str(plan_fingerprint[:16]) + "..."),
        ReviewSummaryRow(label="Suppliers & Parts", value=str(supplier_summary)),
        ReviewSummaryRow(label="Cost Ceiling", value=f"{currency} {max_price:,.2f}"),
        ReviewSummaryRow(label="Must Deliver By", value=str(deadline_str)),
        ReviewSummaryRow(label="Guardrail Mode", value="Draft PO Only (Real spend blocked)"),
    ]

    payload = ReviewCardPayload(
        title="Authorize Procurement Draft (Draft-Only)",
        summary_rows=summary_rows,
        approve_cta=CtaAction(
            label="Approve Procurement Draft",
            selection={
                "action": "approve_plan",
                "case_id": case_id,
                "plan_id": plan_id,
                "fingerprint": plan_fingerprint,
            },
            primary=True,
        ),
        reject_cta=CtaAction(
            label="Reject / Cancel",
            selection={"action": "reject_plan", "case_id": case_id, "plan_id": plan_id},
            primary=False,
        ),
    )
    return create_card_content(payload, card_id=uuid4())


# --- 7. State G: Shipment & Tracking Detail Card ---
def build_delivery_tracking_card(
    case_id: str,
    plan_id: str,
    status: str,
    events: list[dict[str, Any]],
) -> MetadataContent:
    """Delivery event tracking card from DeliveryWatch specialist."""
    summary_rows = [
        DetailSummaryRow(label="Disruption Case", value=str(case_id)),
        DetailSummaryRow(label="Plan Ref", value=str(plan_id)),
        DetailSummaryRow(label="Shipment Status", value=status.upper()),
    ]

    for idx, ev in enumerate(events[:3], 1):
        summary_rows.append(
            DetailSummaryRow(
                label=f"Event {idx} ({ev.get('status', '').upper()})",
                value=f"{ev.get('occurred_at', 'Now')}: {ev.get('details', '')}",
            )
        )

    if not events:
        summary_rows.append(
            DetailSummaryRow(
                label="Carrier Tracking",
                value="No active transit exceptions reported.",
            )
        )

    payload = DetailCardPayload(
        title=f"Shipment & Delivery Watch: {case_id}",
        summary_rows=summary_rows,
        ctas=[
            CtaAction(
                label="Refresh Status",
                selection={"action": "track_delivery", "case_id": case_id},
                primary=True,
            )
        ],
    )
    return create_card_content(payload, card_id=uuid4())


# --- 8. No Feasible Plan Notification Card ---
def build_no_feasible_plan_card(case_id: str, summary: str, blockers: list[str]) -> MetadataContent:
    """Card presented when deterministic rules reject all candidates."""
    summary_rows = [
        DetailSummaryRow(label="Case ID", value=str(case_id)),
        DetailSummaryRow(label="Outcome", value="No feasible strategy found within constraints"),
    ]
    for idx, b in enumerate(blockers[:3], 1):
        summary_rows.append(
            DetailSummaryRow(label=f"Blocker {idx}", value=str(b))
        )

    payload = DetailCardPayload(
        title="No Feasible Recovery Strategy",
        summary_rows=summary_rows,
        ctas=[
            CtaAction(
                label="Run Guided Synthetic Demo",
                selection={"action": "run_demo"},
                primary=True,
            ),
            CtaAction(
                label="Try Another Case",
                selection={"action": "enter_case_id"},
                primary=False,
            ),
        ],
    )
    return create_card_content(payload, card_id=uuid4())


# --- 9. State H: Monetization / Payment Request Card ---
def build_payment_request_card(
    case_id: str,
    reference: str,
    amount: str = "0.50",
    currency: str = "USDC",
    payment_method: str = "skyfire",
) -> MetadataContent:
    """Card offering the Premium Sourcing & Recovery Audit Report for 0.50 USDC."""
    summary_rows = [
        DetailSummaryRow(label="Product", value="Premium Multi-Supplier Recovery Report"),
        DetailSummaryRow(label="Case Reference", value=str(case_id)),
        DetailSummaryRow(label="Price", value=f"{amount} {currency}"),
        DetailSummaryRow(label="Payment Method", value=payment_method.upper()),
        DetailSummaryRow(label="Payment Ref", value=str(reference)),
        DetailSummaryRow(label="Entitlement", value="Lifetime Audit Access + Evidence"),
    ]

    payload = DetailCardPayload(
        title="StockRescue Premium Report",
        summary_rows=summary_rows,
        ctas=[
            CtaAction(
                label=f"Authorize Payment ({amount} {currency})",
                selection={
                    "action": "commit_payment",
                    "case_id": case_id,
                    "reference": reference,
                    "amount": amount,
                    "currency": currency,
                },
                primary=True,
            ),
            CtaAction(
                label="Keep Free Summary",
                selection={"action": "compare_plans", "case_id": case_id},
                primary=False,
            ),
        ],
    )
    return create_card_content(payload, card_id=uuid4())


# --- 10. State I: Premium Report Delivery Card ---
def build_premium_report_card(
    case_id: str,
    entitlement_id: str,
    provider_ref: str,
    sku: str,
    plans_summary: str,
    risk_summary: str,
) -> MetadataContent:
    """Delivered premium audit report with verified sourcing options."""
    summary_rows = [
        DetailSummaryRow(label="Entitlement ID", value=str(entitlement_id)),
        DetailSummaryRow(label="Provider Ref", value=str(provider_ref)),
        DetailSummaryRow(label="Disrupted Part", value=str(sku)),
        DetailSummaryRow(label="Verified Strategy", value=str(plans_summary)),
        DetailSummaryRow(label="Risk Assessment", value=str(risk_summary)),
        DetailSummaryRow(label="Analysis Status", value="PAID & VERIFIED (Exact-match certified)"),
    ]

    payload = DetailCardPayload(
        title="StockRescue Premium Recovery Report",
        summary_rows=summary_rows,
        ctas=[
            CtaAction(
                label="Proceed to Procurement Draft",
                selection={"action": "request_approval", "case_id": case_id},
                primary=True,
            ),
            CtaAction(
                label="Track Delivery",
                selection={"action": "track_delivery", "case_id": case_id},
                primary=False,
            ),
        ],
    )
    return create_card_content(payload, card_id=uuid4())
