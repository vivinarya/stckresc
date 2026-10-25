#supplier offer filtering

from __future__ import annotations
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pydantic import ValidationError
from shared.models import MAX_QUANTITY, SupplierOffer
from tools.inventory import InventoryInputError, normalize_sku

#limits
MAX_SUPPLIER_OFFERS = 500
CURRENCY_PATTERN = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

class SupplierCatalogError(ValueError):
    pass

@dataclass(frozen=True, slots=True)
class SupplierCandidate:
    offer: SupplierOffer
    order_quantity: int
    material_cost: Decimal
    total_cost: Decimal | None
    freight_known: bool
    expiry_known: bool
    lead_time_known: bool

def _validate_as_of(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise SupplierCatalogError("comparison time must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise SupplierCatalogError("comparison time must include a timezone")
    return value

def _validate_currency(value: str) -> str:
    if not isinstance(value, str):
        raise SupplierCatalogError("currency must be text")
    currency = value.strip().upper()
    if len(currency) != 3 or any(char not in CURRENCY_PATTERN for char in currency):
        raise SupplierCatalogError("currency is invalid")
    return currency

def validate_supplier_offers(
    offers: Sequence[SupplierOffer],
) -> tuple[SupplierOffer, ...]:
    if isinstance(offers, (str, bytes, bytearray)) or not isinstance(offers, Sequence):
        raise SupplierCatalogError("supplier offers must be a list or tuple")
    if len(offers) > MAX_SUPPLIER_OFFERS:
        raise SupplierCatalogError("supplier offer limit exceeded")

    validated = []
    offer_ids = set()
    for index, offer in enumerate(offers):
        if not isinstance(offer, SupplierOffer):
            raise SupplierCatalogError(f"supplier offer at index {index} has an invalid type")
        try:
            clean_offer = SupplierOffer.model_validate(offer.model_dump())
        except (ValidationError, TypeError, ValueError) as exc:
            raise SupplierCatalogError(
                f"supplier offer at index {index} failed validation"
            ) from exc
        if clean_offer.offer_id in offer_ids:
            raise SupplierCatalogError("supplier offer IDs must be unique")
        offer_ids.add(clean_offer.offer_id)
        validated.append(clean_offer)
    return tuple(validated)


def _order_quantity(offer: SupplierOffer, required_quantity: int) -> int:
    target = max(required_quantity, offer.minimum_order_quantity)
    return ((target + offer.pack_size - 1) // offer.pack_size) * offer.pack_size

def find_supplier_offers(
    sku: str,
    required_quantity: int,
    offers: Sequence[SupplierOffer],
    *,
    currency: str = "INR",
    as_of: datetime | None = None,
) -> tuple[SupplierCandidate, ...]:
    try:
        requested_sku = normalize_sku(sku)
    except InventoryInputError as exc:
        raise SupplierCatalogError("requested SKU is invalid") from exc

    if isinstance(required_quantity, bool) or not isinstance(required_quantity, int):
        raise SupplierCatalogError("required quantity must be an integer")
    if required_quantity < 1 or required_quantity > MAX_QUANTITY:
        raise SupplierCatalogError("required quantity is outside the allowed range")

    requested_currency = _validate_currency(currency)
    comparison_time = _validate_as_of(as_of)
    candidates = []

    for offer in validate_supplier_offers(offers):
        if offer.sku != requested_sku or offer.currency != requested_currency:
            continue
        if offer.quoted_at > comparison_time:
            continue
        if offer.valid_until is not None and comparison_time >= offer.valid_until:
            continue

        order_quantity = _order_quantity(offer, required_quantity)
        if order_quantity > MAX_QUANTITY or order_quantity > offer.quantity_available:
            continue

        material_cost = offer.unit_price * order_quantity
        total_cost = (
            material_cost + offer.freight_cost
            if offer.freight_cost is not None
            else None
        )
        candidates.append(
            SupplierCandidate(
                offer=offer,
                order_quantity=order_quantity,
                material_cost=material_cost,
                total_cost=total_cost,
                freight_known=offer.freight_cost is not None,
                expiry_known=offer.valid_until is not None,
                lead_time_known=offer.lead_time_days is not None,
            )
        )
    return tuple(candidates)
