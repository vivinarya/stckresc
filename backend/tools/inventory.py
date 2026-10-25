#inventory calculations.

from __future__ import annotations
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pydantic import ValidationError
from shared.models import InventoryRecord, MAX_QUANTITY

#limits
MAX_INVENTORY_RECORDS = 1_000
MAX_AGGREGATED_QUANTITY = MAX_QUANTITY * MAX_INVENTORY_RECORDS
SKU_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{0,63}$", re.ASCII)
PATH_SEGMENT_PATTERN = re.compile(r"(?:^|/)\.\.(?:/|$)")

class InventoryInputError(ValueError):
    pass

@dataclass(frozen=True, slots=True)
class InventoryPosition:
    sku: str
    quantity_on_hand: int
    quantity_committed: int
    available_quantity: int
    locations: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class InventoryAssessment:
    sku: str
    requested_quantity: int
    found: bool
    quantity_on_hand: int
    quantity_committed: int
    available_quantity: int
    shortage_quantity: int
    sufficient: bool
    locations: tuple[str, ...]

def normalize_sku(value: str) -> str:
    if not isinstance(value, str):
        raise InventoryInputError("SKU must be text")
    sku = value.strip().upper()
    if not sku or len(sku) > 64 or SKU_PATTERN.fullmatch(sku) is None:
        raise InventoryInputError("SKU is invalid")
    if PATH_SEGMENT_PATTERN.search(sku):
        raise InventoryInputError("SKU must not contain path traversal segments")
    return sku


def _validated_records(records: Sequence[InventoryRecord]) -> tuple[InventoryRecord, ...]:
    if isinstance(records, (str, bytes, bytearray)) or not isinstance(records, Sequence):
        raise InventoryInputError("inventory records must be a list or tuple")
    if len(records) > MAX_INVENTORY_RECORDS:
        raise InventoryInputError("inventory record limit exceeded")

    validated = []
    for index, record in enumerate(records):
        if not isinstance(record, InventoryRecord):
            raise InventoryInputError(f"inventory record at index {index} has an invalid type")
        try:
            clean_record = InventoryRecord.model_validate(
                record.model_dump(exclude={"available_quantity"})
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise InventoryInputError(
                f"inventory record at index {index} failed validation"
            ) from exc
        validated.append(clean_record)
    return tuple(validated)

def summarize_inventory(
    records: Sequence[InventoryRecord],
) -> dict[str, InventoryPosition]:
    totals: dict[str, tuple[int, int, list[str]]] = {}

    for record in _validated_records(records):
        sku = normalize_sku(record.sku)
        previous = totals.get(sku)
        if previous is None:
            on_hand, committed, locations = 0, 0, []
        else:
            on_hand, committed, locations = previous

        on_hand += record.quantity_on_hand
        committed += record.quantity_committed
        if on_hand > MAX_AGGREGATED_QUANTITY or committed > MAX_AGGREGATED_QUANTITY:
            raise InventoryInputError("aggregated inventory quantity exceeds allowed range")
        if record.location not in locations:
            locations.append(record.location)
        totals[sku] = on_hand, committed, locations

    return {
        sku: InventoryPosition(
            sku=sku,
            quantity_on_hand=on_hand,
            quantity_committed=committed,
            available_quantity=on_hand - committed,
            locations=tuple(locations),
        )
        for sku, (on_hand, committed, locations) in totals.items()
    }

def get_inventory_position(
    sku: str,
    records: Sequence[InventoryRecord],
) -> InventoryPosition | None:
    return summarize_inventory(records).get(normalize_sku(sku))

def check_inventory(
    sku: str,
    required_quantity: int,
    records: Sequence[InventoryRecord],
) -> InventoryAssessment:
    normalized_sku = normalize_sku(sku)
    if isinstance(required_quantity, bool) or not isinstance(required_quantity, int):
        raise InventoryInputError("required quantity must be an integer")
    if required_quantity < 1 or required_quantity > MAX_QUANTITY:
        raise InventoryInputError("required quantity is outside the allowed range")

    position = summarize_inventory(records).get(normalized_sku)
    if position is None:
        return InventoryAssessment(
            sku=normalized_sku,
            requested_quantity=required_quantity,
            found=False,
            quantity_on_hand=0,
            quantity_committed=0,
            available_quantity=0,
            shortage_quantity=required_quantity,
            sufficient=False,
            locations=(),
        )

    shortage = max(0, required_quantity - position.available_quantity)
    return InventoryAssessment(
        sku=position.sku,
        requested_quantity=required_quantity,
        found=True,
        quantity_on_hand=position.quantity_on_hand,
        quantity_committed=position.quantity_committed,
        available_quantity=position.available_quantity,
        shortage_quantity=shortage,
        sufficient=shortage == 0,
        locations=position.locations,
    )
