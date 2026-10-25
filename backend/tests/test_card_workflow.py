"""Unit tests for ASI:One ACP Card Protocol v1 schemas and interactive workflow transitions."""

import json
from decimal import Decimal
import pytest

from agents.coordinator.card_protocol import (
    build_welcome_card,
    build_case_intake_card,
    build_impact_detail_card,
    build_plan_carousel_card,
    build_plan_detail_card,
    build_procurement_review_card,
    build_delivery_tracking_card,
    build_no_feasible_plan_card,
)
from agents.coordinator.card_workflow import parse_user_input, handle_conversational_step
from services.warehouse_api.repository import SQLiteWarehouseRepository
from agents.protocols.transport import SpecialistTransport
from helpers import make_case, NOW


@pytest.fixture
def test_repo(tmp_path):
    repo = SQLiteWarehouseRepository(tmp_path / "card_test.db")
    repo.initialize()
    case = make_case(case_id="DEMO-STOCK-RESCUE-001")
    repo.create_case(case)
    return repo


@pytest.fixture
def local_transport():
    return SpecialistTransport(mode="local")


def test_card_protocol_v1_metadata_structure():
    card = build_welcome_card()
    assert card.type == "metadata"
    meta = card.metadata
    assert meta["card_protocol_version"] == "1"
    assert meta["requires_card_interaction"] == "true"
    assert meta["card_kind"] == "form"

    payload = json.loads(meta["card_payload"])
    assert payload["title"] == "Welcome to StockRescue"
    assert len(payload["fields"]) >= 1


def test_card_carousel_schema():
    plans = [
        {"plan_id": "P-01", "title": "Option A", "total_cost": 2540.00, "budget": 3000.00, "eta_days": 2},
        {"plan_id": "P-02", "title": "Option B", "total_cost": 2750.00, "budget": 3000.00, "eta_days": 0},
    ]
    card = build_plan_carousel_card("DEMO-001", plans)
    meta = card.metadata
    assert meta["card_kind"] == "carousel"
    payload = json.loads(meta["card_payload"])
    assert payload["title"] == "Feasible Recovery Strategies"
    assert len(payload["items"]) == 2
    assert payload["items"][0]["primary_cta"]["selection"]["plan_id"] == "P-01"


def test_parse_user_input_formats():
    # JSON selection
    p1 = parse_user_input('{"action": "view_plan", "plan_id": "P-01"}')
    assert p1["action"] == "view_plan"
    assert p1["plan_id"] == "P-01"

    # Embedded JSON in text
    p2 = parse_user_input('I select this: {"action": "approve_plan", "case_id": "C-01"} thanks')
    assert p2["action"] == "approve_plan"

    # Natural language triggers
    assert parse_user_input("run the demo")["action"] == "run_demo"
    assert parse_user_input("track my shipment")["action"] == "track_delivery"
    assert parse_user_input("show me repair jobs at risk")["action"] == "review_jobs"
    assert parse_user_input("compare recovery plans")["action"] == "compare_plans"
    assert parse_user_input("view plan details")["action"] == "view_plan"
    assert parse_user_input("request approval for draft")["action"] == "request_approval"
    assert parse_user_input("approve procurement draft")["action"] == "approve_plan"


def test_full_conversational_state_progression(test_repo, local_transport):
    # State A: Welcome
    t1, c1 = handle_conversational_step("hi", test_repo, local_transport)
    assert c1.metadata["card_kind"] == "form"

    # State B/C: Case Impact
    t2, c2 = handle_conversational_step("run demo", test_repo, local_transport)
    assert c2.metadata["card_kind"] == "detail"
    p2 = json.loads(c2.metadata["card_payload"])
    assert any("BRG-6204" in row.get("value", "") for row in p2.get("summary_rows", []))

    # State D: Compare Strategy Carousel
    t3, c3 = handle_conversational_step("compare plans", test_repo, local_transport)
    assert c3.metadata["card_kind"] == "carousel"
    p3 = json.loads(c3.metadata["card_payload"])
    assert len(p3["items"]) >= 2

    # State E: View Plan Details
    t4, c4 = handle_conversational_step('{"action": "view_plan", "plan_id": "PLAN-DEMO-01"}', test_repo, local_transport)
    assert c4.metadata["card_kind"] == "detail"

    # State F1: Review Card for Human Approval
    t5, c5 = handle_conversational_step('{"action": "request_approval", "plan_id": "PLAN-DEMO-01"}', test_repo, local_transport)
    assert c5.metadata["card_kind"] == "review"

    # State F2 / G: Approved & Delivery Watch
    t6, c6 = handle_conversational_step('{"action": "approve_plan", "plan_id": "PLAN-DEMO-01"}', test_repo, local_transport)
    assert c6.metadata["card_kind"] == "detail"
    assert "APPROVED" in t6
    assert "Draft-Only" in t6
