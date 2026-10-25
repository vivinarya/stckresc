import json
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from shared.models import Organization, BusinessProfile, OrganizationMembership
from services.warehouse_api.repository import SQLiteWarehouseRepository, RepositoryNotFound
from agents.coordinator.card_workflow import (
    validate_inventory_csv,
    parse_user_input,
    handle_conversational_step,
)
from agents.protocols.transport import SpecialistTransport


@pytest.fixture
def repo(tmp_path):
    db_file = tmp_path / "test_warehouse.db"
    repository = SQLiteWarehouseRepository(db_file)
    repository.initialize()
    return repository


def test_organization_and_business_profile_persistence(repo):
    org = Organization(
        org_id="org_test_123",
        name="Precision Motors Ltd",
        business_type="Automotive",
        location="Detroit, MI",
        country="US",
        currency="USD",
        business_size="medium",
        primary_objective="Zero SLA breaches",
    )
    repo.save_organization(org)

    saved_org = repo.get_organization("org_test_123")
    assert saved_org.name == "Precision Motors Ltd"
    assert saved_org.business_size == "medium"
    assert saved_org.currency == "USD"

    profile = BusinessProfile(
        profile_id="prof_test_123",
        org_id="org_test_123",
        budget_threshold=Decimal("15000.00"),
        approval_policy="strict",
        warehouses=["hub_east", "hub_west"],
        operating_currency="USD",
    )
    repo.save_business_profile(profile)

    saved_profile = repo.get_business_profile("org_test_123")
    assert saved_profile is not None
    assert saved_profile.budget_threshold == Decimal("15000.00")
    assert saved_profile.warehouses == ["hub_east", "hub_west"]


def test_organization_membership_and_isolation(repo):
    org = Organization(
        org_id="org_test_123",
        name="Precision Motors Ltd",
        business_type="Automotive",
        location="Detroit, MI",
        country="US",
        currency="USD",
        business_size="medium",
        primary_objective="Zero SLA breaches",
    )
    repo.save_organization(org)

    mem = OrganizationMembership(
        membership_id="mem_test_123",
        org_id="org_test_123",
        user_address="agent1qcustomeraddress",
        role="admin",
    )
    repo.save_membership(mem)

    saved_mem = repo.get_user_membership("agent1qcustomeraddress")
    assert saved_mem is not None
    assert saved_mem.org_id == "org_test_123"
    assert saved_mem.role == "admin"

    # Non-existent membership returns None
    assert repo.get_user_membership("agent1qunknown") is None


def test_business_onboarding_form_workflow(repo):
    transport = SpecialistTransport(mode="local")

    # 1. User says "onboard our company"
    step1_text, step1_card = handle_conversational_step("onboard our company", repo, transport)
    assert "configure your organization profile" in step1_text.lower()
    assert step1_card is not None
    assert hasattr(step1_card, "metadata")
    assert "card_payload" in step1_card.metadata
    card_data = json.loads(step1_card.metadata["card_payload"])
    assert card_data["title"] == "StockRescue Business Onboarding"

    # 2. User submits the onboarding card
    submission = {
        "action": "submit_business_profile",
        "company_name": "Turbine Dynamics",
        "business_type": "Aerospace",
        "location": "Munich, Germany",
        "currency": "EUR",
        "business_size": "large",
        "primary_objective": "Guarantee turbine overhaul delivery",
        "sender_address": "agent1qairlinebuyer",
    }
    step2_text, step2_card = handle_conversational_step(str(submission).replace("'", '"'), repo, transport)
    assert "✅ Business profile successfully onboarded for 'Turbine Dynamics'" in step2_text
    assert "Large organization" in step2_text
    assert "EUR" in step2_text
    assert step2_card is None

    # Verify stored in repository
    membership = repo.get_user_membership("agent1qairlinebuyer")
    assert membership is not None
    org = repo.get_organization(membership.org_id)
    assert org.name == "Turbine Dynamics"
    assert org.business_size == "large"
    assert org.currency == "EUR"


def test_validate_inventory_csv_valid_and_rejected_rows():
    csv_data = """sku,name,on_hand,committed
BRG-6204,Deep Groove Ball Bearing,25,10
PUMP-X200,Hydraulic Gear Pump,5,0
BAD-ROW-1,,not_a_number,2
BAD-ROW-2,Negative Stock,-3,0
"""
    result = validate_inventory_csv(csv_data)
    assert result["success"] is True
    assert result["imported_count"] == 2
    assert result["rejected_count"] == 2
    assert result["valid_rows"][0]["sku"] == "BRG-6204"
    assert result["valid_rows"][0]["available"] == 15
    assert result["valid_rows"][1]["sku"] == "PUMP-X200"
    assert result["valid_rows"][1]["available"] == 5
    assert len(result["rejected_rows"]) == 2


def test_import_inventory_csv_conversational_step(repo):
    transport = SpecialistTransport(mode="local")

    csv_command = """import csv:
sku,on_hand,committed
BRG-6204,50,20
VLV-9900,12,0
"""
    text, card = handle_conversational_step(csv_command, repo, transport)
    assert "✅ Inventory CSV Processed Successfully!" in text
    assert "Imported Items: 2" in text
    assert "BRG-6204" in text
    assert card is None
