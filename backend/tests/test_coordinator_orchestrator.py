"""Unit tests for ASI1 Mini controlled tool calling orchestrator (Phase 1)."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from agents.coordinator.orchestrator import APPROVED_TOOLS, CoordinatorOrchestrator
from agents.protocols.transport import SpecialistTransport
from fixtures_demo import make_expired_quote_case, make_fresh_demo_case
from services.warehouse_api.repository import SQLiteWarehouseRepository


@pytest.fixture
def repo(tmp_path):
    r = SQLiteWarehouseRepository(tmp_path / "orchestrator_test.db")
    r.initialize()
    case = make_fresh_demo_case(case_id="DEMO-STOCK-RESCUE-001")
    r.create_case(case)
    return r


@pytest.fixture
def transport():
    return SpecialistTransport(mode="local")


def test_approved_tools_count_and_schemas():
    assert len(APPROVED_TOOLS) >= 12
    tool_names = {t["function"]["name"] for t in APPROVED_TOOLS}
    expected = {
        "get_case_context",
        "analyze_inventory_bom",
        "assess_job_impact",
        "discover_suppliers",
        "check_compatibility",
        "assess_quotes",
        "generate_recovery_options",
        "get_plan_details",
        "prepare_vendor_communication",
        "request_vendor_quote_by_voice",
        "get_delivery_status",
        "prepare_procurement_draft",
        "request_premium_report_payment",
    }
    assert expected.issubset(tool_names)


def test_execute_tool_case_context(repo, transport):
    orchestrator = CoordinatorOrchestrator(repo, transport)
    res, card = orchestrator._execute_tool("get_case_context", json.dumps({"case_id": "DEMO-STOCK-RESCUE-001"}))
    assert res["case_id"] == "DEMO-STOCK-RESCUE-001"
    assert res["affected_sku"] == "BRG-6204"
    assert card is None


def test_execute_tool_inventory_bom(repo, transport):
    orchestrator = CoordinatorOrchestrator(repo, transport)
    res, card = orchestrator._execute_tool("analyze_inventory_bom", json.dumps({"case_id": "DEMO-STOCK-RESCUE-001"}))
    assert res["affected_sku"] == "BRG-6204"
    assert res["demand_quantity"] == 8
    assert res["quantity_on_hand"] == 8
    assert res["quantity_committed"] == 5
    assert res["available_quantity"] == 3
    assert res["shortage_quantity"] == 5


def test_execute_tool_job_impact(repo, transport):
    orchestrator = CoordinatorOrchestrator(repo, transport)
    res, card = orchestrator._execute_tool("assess_job_impact", json.dumps({"case_id": "DEMO-STOCK-RESCUE-001"}))
    assert res["total_demand"] == 8
    assert card is not None
    assert card.metadata["card_kind"] == "detail"


def test_execute_tool_compatibility(repo, transport):
    orchestrator = CoordinatorOrchestrator(repo, transport)
    res, card = orchestrator._execute_tool(
        "check_compatibility",
        json.dumps({"primary_sku": "BRG-6204", "candidate_sku": "BRG-6204"}),
    )
    assert res["is_compatible"] is True
    assert res["status"] == "compatible"


def test_execute_tool_recovery_options(repo, transport):
    orchestrator = CoordinatorOrchestrator(repo, transport)
    res, card = orchestrator._execute_tool("generate_recovery_options", json.dumps({"case_id": "DEMO-STOCK-RESCUE-001"}))
    assert res["plans_generated"] >= 1
    assert card is not None
    assert card.metadata["card_kind"] == "carousel"


def test_execute_tool_expired_quote_fixture(repo, transport):
    expired_case = make_expired_quote_case("DEMO-EXPIRED-001")
    repo.create_case(expired_case)
    orchestrator = CoordinatorOrchestrator(repo, transport)
    res, card = orchestrator._execute_tool("generate_recovery_options", json.dumps({"case_id": "DEMO-EXPIRED-001"}))
    assert res["plans"] == []
    assert card is not None
    assert card.metadata["card_kind"] == "detail"  # no feasible plan card


def test_execute_tool_voice_quote_simulation(repo, transport):
    orchestrator = CoordinatorOrchestrator(repo, transport)
    res, card = orchestrator._execute_tool(
        "request_vendor_quote_by_voice",
        json.dumps({
            "case_id": "DEMO-STOCK-RESCUE-001",
            "supplier_id": "SUPP-MOTION-IND",
            "phone_number": "+13175550149",
            "user_authorized": True,
        }),
    )
    assert res["status"] in ("COMPLETED", "BLOCKED")
    assert "transcript_summary" in res


def test_orchestrator_multi_round_tool_calling_loop(repo, transport, monkeypatch):
    monkeypatch.setenv("ASI_ONE_API_KEY", "mock-asi-key")
    orchestrator = CoordinatorOrchestrator(repo, transport)

    # Round 1: Model requests analyze_inventory_bom
    tool_call_mock = MagicMock()
    tool_call_mock.id = "call-inv-1"
    tool_call_mock.function.name = "analyze_inventory_bom"
    tool_call_mock.function.arguments = json.dumps({"case_id": "DEMO-STOCK-RESCUE-001"})

    resp_round1 = MagicMock()
    resp_round1.choices = [
        MagicMock(
            message=MagicMock(
                content=None,
                tool_calls=[tool_call_mock],
            )
        )
    ]

    # Round 2: Model finishes with final natural language text
    resp_round2 = MagicMock()
    resp_round2.choices = [
        MagicMock(
            message=MagicMock(
                content="I evaluated the warehouse positions. There is a net shortage of 5 units of bearing BRG-6204. Production jobs are at risk.",
                tool_calls=None,
            )
        )
    ]

    with patch("openai.resources.chat.completions.Completions.create", side_effect=[resp_round1, resp_round2]) as mock_create:
        reply_text, card = orchestrator.process_turn("Check stockout status for case DEMO-STOCK-RESCUE-001")
        assert "shortage of 5 units" in reply_text
        assert mock_create.call_count == 2
        # Verify model name and enable_thinking were used
        first_call_kwargs = mock_create.call_args_list[0][1]
        assert first_call_kwargs["model"] == "asi1-mini"
        assert first_call_kwargs["extra_body"] == {"enable_thinking": True}
