# normalise quote

from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pydantic import ValidationError
from shared.models import MAX_QUANTITY, SupplierOffer
from tools.inventory import InventoryInputError, normalize_sku

#limits
MAX_CURRENCY_LENGTH = 3
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$", re.ASCII)

class QuoteNormalizationError(ValueError):
    pass

@dataclass(frozen=True, slots=True)
class NormalizedQuote:
    offer_id: str
    supplier_id: str
    supplier_name: str
    sku: str
    currency: str
    required_quantity: int
    order_quantity: int
    quantity_available: int
    unit_price: Decimal
    material_cost: Decimal
    freight_cost: Decimal | None
    total_cost: Decimal | None
    quoted_at: datetime
    valid_until: datetime | None
    expiry_known: bool
    lead_time_days: int | None
    estimated_delivery_at: datetime | None
    freight_known: bool
    lead_time_known: bool

def _validate_as_of(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise QuoteNormalizationError("comparison time must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise QuoteNormalizationError("comparison time must include a timezone")
    return value

def _validate_currency(value: str) -> str:
    if not isinstance(value, str):
        raise QuoteNormalizationError("currency must be text")
    currency = value.strip().upper()
    if len(currency) != MAX_CURRENCY_LENGTH or CURRENCY_PATTERN.fullmatch(currency) is None:
        raise QuoteNormalizationError("currency is invalid")
    return currency

def _validate_offer(offer: SupplierOffer) -> SupplierOffer:
    if not isinstance(offer, SupplierOffer):
        raise QuoteNormalizationError("supplier offer has an invalid type")
    try:
        return SupplierOffer.model_validate(offer.model_dump())
    except (ValidationError, TypeError, ValueError) as exc:
        raise QuoteNormalizationError("supplier offer failed validation") from exc

def _order_quantity(offer: SupplierOffer, required_quantity: int) -> int:
    target = max(required_quantity, offer.minimum_order_quantity)
    quantity = ((target + offer.pack_size - 1) // offer.pack_size) * offer.pack_size
    if quantity > MAX_QUANTITY:
        raise QuoteNormalizationError("required order quantity exceeds the allowed range")
    if quantity > offer.quantity_available:
        raise QuoteNormalizationError("supplier cannot provide the required order quantity")
    return quantity

def normalize_quote(
    offer: SupplierOffer,
    required_quantity: int,
    *,
    currency: str = "INR",
    as_of: datetime | None = None,
) -> NormalizedQuote:
    clean_offer = _validate_offer(offer)
    comparison_time = _validate_as_of(as_of)

    if isinstance(required_quantity, bool) or not isinstance(required_quantity, int):
        raise QuoteNormalizationError("required quantity must be an integer")
    if required_quantity < 1 or required_quantity > MAX_QUANTITY:
        raise QuoteNormalizationError("required quantity is outside the allowed range")
    try:
        sku = normalize_sku(clean_offer.sku)
    except InventoryInputError as exc:
        raise QuoteNormalizationError("supplier SKU is invalid") from exc
    requested_currency = _validate_currency(currency)
    offer_currency = _validate_currency(clean_offer.currency)
    if offer_currency != requested_currency:
        raise QuoteNormalizationError("currency conversion is not configured")
    if clean_offer.quoted_at > comparison_time:
        raise QuoteNormalizationError("supplier quote is dated in the future")
    if clean_offer.valid_until is not None and comparison_time >= clean_offer.valid_until:
        raise QuoteNormalizationError("supplier quote has expired")

    order_quantity = _order_quantity(clean_offer, required_quantity)
    material_cost = clean_offer.unit_price * order_quantity
    freight_cost = clean_offer.freight_cost
    total_cost = material_cost + freight_cost if freight_cost is not None else None
    lead_time_days = clean_offer.lead_time_days
    estimated_delivery_at = (
        comparison_time + timedelta(days=lead_time_days)
        if lead_time_days is not None
        else None
    )
    return NormalizedQuote(
        offer_id=clean_offer.offer_id,
        supplier_id=clean_offer.supplier_id,
        supplier_name=clean_offer.supplier_name,
        sku=sku,
        currency=requested_currency,
        required_quantity=required_quantity,
        order_quantity=order_quantity,
        quantity_available=clean_offer.quantity_available,
        unit_price=clean_offer.unit_price,
        material_cost=material_cost,
        freight_cost=freight_cost,
        total_cost=total_cost,
        quoted_at=clean_offer.quoted_at,
        valid_until=clean_offer.valid_until,
        expiry_known=clean_offer.valid_until is not None,
        lead_time_days=lead_time_days,
        estimated_delivery_at=estimated_delivery_at,
        freight_known=freight_cost is not None,
        lead_time_known=lead_time_days is not None,
    )
