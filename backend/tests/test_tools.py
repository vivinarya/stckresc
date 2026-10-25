from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from helpers import NOW, SKU, make_assessment, make_offer
from shared.models import CompatibilityStatus, InventoryRecord
from tools.compatibility import (
    CompatibilityInputError,
    assess_compatibility,
)
from tools.inventory import (
    InventoryInputError,
    check_inventory,
    normalize_sku,
    summarize_inventory,
)
from tools.quote_normalizer import QuoteNormalizationError, normalize_quote
from tools.supplier_catalog import SupplierCatalogError, find_supplier_offers


# inventory
def test_inventory_aggregates_locations_case_insensitively():
    records = [
        InventoryRecord(sku="a-1", quantity_on_hand=5, quantity_committed=2, location="east"),
        InventoryRecord(sku="A-1", quantity_on_hand=4, location="west"),
    ]
    position = summarize_inventory(records)["A-1"]
    assert (position.quantity_on_hand, position.quantity_committed) == (9, 2)
    assert position.available_quantity == 7
    assert position.locations == ("east", "west")


def test_inventory_shortage_and_missing_sku():
    records = [InventoryRecord(sku=SKU, quantity_on_hand=8, quantity_committed=5)]
    short = check_inventory(SKU, 5, records)
    assert (short.available_quantity, short.shortage_quantity, short.sufficient) == (3, 2, False)
    assert check_inventory(SKU, 3, records).sufficient
    missing = check_inventory("OTHER-1", 4, records)
    assert not missing.found and missing.shortage_quantity == 4


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, 1_000_001])
def test_inventory_rejects_bad_required_quantity(quantity):
    with pytest.raises(InventoryInputError):
        check_inventory(SKU, quantity, [])


@pytest.mark.parametrize("sku", ["a/../b", "../etc", "bad sku", "", None, 5])
def test_normalize_sku_rejects_unsafe_values(sku):
    with pytest.raises(InventoryInputError):
        normalize_sku(sku)


def test_normalize_sku_trims_and_uppercases():
    assert normalize_sku("  brg-6204 ") == SKU


# supplier catalog
def test_order_quantity_respects_minimum_and_pack_size():
    offer = make_offer(minimum_order_quantity=5, pack_size=5)
    (candidate,) = find_supplier_offers(SKU, 2, [offer], as_of=NOW)
    assert candidate.order_quantity == 5
    assert candidate.total_cost == Decimal("550.00")


def test_stale_future_short_and_foreign_currency_offers_are_excluded():
    offers = [
        make_offer(offer_id="STALE", valid_until=NOW - timedelta(seconds=1)),
        make_offer(offer_id="FUTURE", quoted_at=NOW + timedelta(hours=1),
                   valid_until=NOW + timedelta(days=1)),
        make_offer(offer_id="SHORT", quantity_available=1),
        make_offer(offer_id="USD", currency="USD"),
        make_offer(offer_id="OTHERSKU", sku="OTHER-1"),
    ]
    assert find_supplier_offers(SKU, 2, offers, as_of=NOW) == ()


def test_unknown_freight_stays_unknown():
    (candidate,) = find_supplier_offers(SKU, 2, [make_offer(freight_cost=None)], as_of=NOW)
    assert candidate.total_cost is None and not candidate.freight_known


def test_supplier_catalog_input_validation():
    with pytest.raises(SupplierCatalogError):
        find_supplier_offers(SKU, 2, [make_offer()], as_of=datetime(2026, 1, 1))
    with pytest.raises(SupplierCatalogError):
        find_supplier_offers(SKU, 2, [make_offer(), make_offer()], as_of=NOW)
    with pytest.raises(SupplierCatalogError):
        find_supplier_offers(SKU, 2, "not a list", as_of=NOW)
    with pytest.raises(SupplierCatalogError):
        find_supplier_offers(SKU, 0, [], as_of=NOW)


# compatibility
def test_exact_sku_is_compatible_without_evidence():
    decision = assess_compatibility(SKU, SKU.lower(), [], as_of=NOW)
    assert decision.is_compatible and decision.evidence == ("exact SKU match",)


def test_unassessed_substitute_is_unknown():
    decision = assess_compatibility(SKU, "ALT-1", [], as_of=NOW)
    assert decision.status == CompatibilityStatus.UNKNOWN and not decision.is_compatible


def test_latest_assessment_wins():
    older = make_assessment("ALT-1", assessed_at=NOW - timedelta(days=2))
    newer = make_assessment("ALT-1", CompatibilityStatus.INCOMPATIBLE,
                            assessed_at=NOW - timedelta(days=1))
    assert assess_compatibility(SKU, "ALT-1", [older, newer], as_of=NOW).status == \
        CompatibilityStatus.INCOMPATIBLE


def test_conflicting_assessments_at_same_time_are_unknown():
    same = NOW - timedelta(days=1)
    items = [
        make_assessment("ALT-1", assessed_at=same),
        make_assessment("ALT-1", CompatibilityStatus.INCOMPATIBLE, assessed_at=same),
    ]
    assert assess_compatibility(SKU, "ALT-1", items, as_of=NOW).status == \
        CompatibilityStatus.UNKNOWN


def test_future_assessments_are_ignored():
    future = make_assessment("ALT-1", assessed_at=NOW + timedelta(days=1))
    assert assess_compatibility(SKU, "ALT-1", [future], as_of=NOW).status == \
        CompatibilityStatus.UNKNOWN


def test_compatibility_rejects_bad_input():
    with pytest.raises(CompatibilityInputError):
        assess_compatibility("bad sku", "ALT-1", [], as_of=NOW)
    with pytest.raises(CompatibilityInputError):
        assess_compatibility(SKU, "ALT-1", [], as_of=datetime(2026, 1, 1))


# quote normalisation
def test_quote_totals_and_delivery_estimate():
    quote = normalize_quote(make_offer(), 2, as_of=NOW)
    assert quote.material_cost == Decimal("200.00")
    assert quote.total_cost == Decimal("250.00")
    assert quote.estimated_delivery_at == NOW + timedelta(days=1)


def test_quote_without_freight_or_lead_time_has_unknown_totals():
    quote = normalize_quote(make_offer(freight_cost=None, lead_time_days=None), 2, as_of=NOW)
    assert quote.total_cost is None and quote.estimated_delivery_at is None


def test_quote_rejects_expired_foreign_currency_and_oversized_orders():
    with pytest.raises(QuoteNormalizationError):
        normalize_quote(make_offer(valid_until=NOW - timedelta(seconds=1)), 2, as_of=NOW)
    with pytest.raises(QuoteNormalizationError):
        normalize_quote(make_offer(currency="USD"), 2, as_of=NOW)
    with pytest.raises(QuoteNormalizationError):
        normalize_quote(make_offer(quantity_available=1), 2, as_of=NOW)
