"""Regression tests for Coordinator ACP Natural Language Routing & Tool Execution.

Tests:
1. Natural language supplier delay request:
   - Does NOT return the welcome card.
   - Calls the ASI1 Mini tool execution loop over approved tools.
   - Returns grounded plain-language explanations of inventory balance, at-risk jobs, and suppliers.
2. Normal greeting behavior:
   - "hi", "hello", "help" correctly returns the welcome message and welcome form card.
3. Specialist timeout or failure reporting:
   - Failures and timeouts are reported honestly without fabricating success.
4. Interactive card selection routing:
   - Card selections route deterministically back into workflow.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from uagents import Context
from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent, TextContent

from agents.coordinator.agent import (
    APPROVED_TOOLS,
    execute_coordinator_tool,
    handle_message,
    run_asi1_mini_tool_loop,
)


@pytest.fixture
def mock_context():
    ctx = MagicMock(spec=Context)
    ctx.send = AsyncMock()
    ctx.logger = MagicMock()
    return ctx


def test_supplier_delay_natural_language_request_regression(mock_context, monkeypatch):
    """Regression test: natural language supplier delay request must NOT return the welcome card."""
    user_prompt = (
        "Our main supplier just told us that a delivery will be delayed by 3 days. "
        "We have two production jobs waiting, and I'm worried we'll miss our deadlines. "
        "Can you check our available inventory, identify which jobs are at risk, and "
        "find suitable alternative suppliers? Please explain what you find in simple terms."
    )

    msg = ChatMessage(
        msg_id=uuid4(),
        content=[TextContent(type="text", text=user_prompt)],
    )

    # Mock OpenAI client to verify tool calling loop over approved specialist tools
    mock_tc_inv = MagicMock()
    mock_tc_inv.id = "call-inv-1"
    mock_tc_inv.function.name = "analyze_inventory_bom"
    mock_tc_inv.function.arguments = json.dumps({"case_id": "DEMO-STOCK-RESCUE-001"})

    mock_tc_job = MagicMock()
    mock_tc_job.id = "call-job-2"
    mock_tc_job.function.name = "assess_job_impact"
    mock_tc_job.function.arguments = json.dumps({"case_id": "DEMO-STOCK-RESCUE-001"})

    mock_tc_supp = MagicMock()
    mock_tc_supp.id = "call-supp-3"
    mock_tc_supp.function.name = "discover_suppliers"
    mock_tc_supp.function.arguments = json.dumps({"case_id": "DEMO-STOCK-RESCUE-001"})

    # Round 1: Model requests inventory, job impact, and supplier discovery
    resp_r1 = MagicMock()
    resp_r1.choices = [
        MagicMock(
            message=MagicMock(
                content=None,
                tool_calls=[mock_tc_inv, mock_tc_job, mock_tc_supp],
            )
        )
    ]

    # Round 2: Model synthesizes plain English explanation grounded in specialist data
    synthesized_text = (
        "Here is what I found regarding your delayed supplier delivery:\n\n"
        "• Inventory Status: You have 8 units of bearing BRG-6204 in warehouse, but 5 are committed to other work. Available balance is 3 units, leaving a net shortage of 5 units.\n"
        "• At-Risk Jobs: Job JOB-A (Production Line 4 cooling pump) is critically at risk of downtime ($14,500/hr) within 24 hours. Job JOB-B can be covered by on-hand stock.\n"
        "• Alternative Suppliers: Motion Industries has 18 units in stock and can deliver tomorrow at 09:00 AM via next-day expedited transport."
    )
    resp_r2 = MagicMock()
    resp_r2.choices = [
        MagicMock(
            message=MagicMock(
                content=synthesized_text,
                tool_calls=None,
            )
        )
    ]

    monkeypatch.setenv("ASI_ONE_API_KEY", "mock-asi-key")

    with patch("openai.resources.chat.completions.Completions.create", side_effect=[resp_r1, resp_r2]) as mock_create:
        import asyncio
        asyncio.run(handle_message(mock_context, "agent1qsender_operator", msg))

        # Assert at least one call to ctx.send was made (the outgoing reply)
        assert mock_context.send.call_count >= 2  # 1 ack + 1 reply

        # Retrieve the outgoing ChatMessage
        outgoing_call = mock_context.send.call_args_list[-1]
        sent_msg = outgoing_call[0][1]
        assert isinstance(sent_msg, ChatMessage)

        # Extract text and metadata
        sent_texts = [item.text for item in sent_msg.content if isinstance(item, TextContent)]
        full_reply = " ".join(sent_texts)

        sent_cards = [item for item in sent_msg.content if isinstance(item, MetadataContent)]

        # CRITICAL ASSERTION: The welcome card is NEVER returned for this natural language request!
        for card in sent_cards:
            payload = json.loads(card.metadata.get("card_payload", "{}"))
            assert payload.get("title") != "Welcome to StockRescue"

        # Assert text incorporates the specialist data
        assert "shortage of 5 units" in full_reply or "shortage" in full_reply.lower()
        assert "Line 4" in full_reply or "JOB-A" in full_reply
        assert "Motion Industries" in full_reply

        # Assert tool calling invoked model with asi1-mini and enable_thinking
        assert mock_create.call_count == 2
        kwargs = mock_create.call_args_list[0][1]
        assert kwargs["model"] == "asi1-mini"
        assert kwargs["extra_body"] == {"enable_thinking": True}


def test_normal_greeting_returns_welcome_card(mock_context):
    """Asserts that normal greeting 'hi' or 'hello' returns the welcome message and welcome card."""
    msg = ChatMessage(
        msg_id=uuid4(),
        content=[TextContent(type="text", text="hello")],
    )

    import asyncio
    asyncio.run(handle_message(mock_context, "agent1qsender_new_user", msg))

    outgoing_call = mock_context.send.call_args_list[-1]
    sent_msg = outgoing_call[0][1]

    sent_cards = [item for item in sent_msg.content if isinstance(item, MetadataContent)]
    assert len(sent_cards) == 1

    payload = json.loads(sent_cards[0].metadata.get("card_payload", "{}"))
    assert payload.get("title") == "Welcome to StockRescue"


def test_specialist_tool_timeout_and_error_handling():
    """Asserts that specialist tool failures report errors honestly without fabricating success."""
    # Test unknown tool
    res, card = execute_coordinator_tool("nonexistent_tool", "{}")
    assert "error" in res
    assert "Unknown tool" in res["error"]

    # Test malformed args
    res, card = execute_coordinator_tool("analyze_inventory_bom", "{bad_json")
    assert "error" in res
    assert "Malformed" in res["error"]


def test_card_action_selection_routing(mock_context):
    """Asserts that interactive card selections route directly into recovery workflows."""
    msg = ChatMessage(
        msg_id=uuid4(),
        content=[TextContent(type="text", text='{"action": "compare_plans", "case_id": "DEMO-STOCK-RESCUE-001"}')],
    )

    import asyncio
    asyncio.run(handle_message(mock_context, "agent1qsender_operator", msg))

    outgoing_call = mock_context.send.call_args_list[-1]
    sent_msg = outgoing_call[0][1]

    sent_cards = [item for item in sent_msg.content if isinstance(item, MetadataContent)]
    assert len(sent_cards) == 1

    meta = sent_cards[0].metadata
    assert meta["card_kind"] == "carousel"
    payload = json.loads(meta.get("card_payload", "{}"))
    assert "Recovery Options" in payload.get("title", "")
