from __future__ import annotations
import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

#limits
MAX_IDEMPOTENCY_PAYLOAD_BYTES = 64 * 1024
MAX_IDEMPOTENCY_RESPONSE_BYTES = 64 * 1024
MAX_JSON_NODES = 2_000
MAX_JSON_DEPTH = 10
MAX_JSON_STRING_LENGTH = 8_192
KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$", re.ASCII)
TABLE_NAME = "stockrescue_idempotency"

class IdempotencyError(ValueError):
    pass

class IdempotencyConflict(IdempotencyError):
    pass

@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    key: str
    fingerprint: str
    status: str
    acquired: bool
    response_json: str | None
    created_at: str
    updated_at: str

def _connection(connection: sqlite3.Connection) -> sqlite3.Connection:
    if not isinstance(connection, sqlite3.Connection):
        raise IdempotencyError("a SQLite connection is required")
    return connection

def _time(value: datetime | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise IdempotencyError("timestamp must include a timezone")
    normalized = value.astimezone(timezone.utc)
    if normalized > datetime.now(timezone.utc):
        raise IdempotencyError("timestamp cannot be in the future")
    return normalized.isoformat(timespec="microseconds")

def _key(value: str) -> str:
    if not isinstance(value, str) or KEY_PATTERN.fullmatch(value) is None:
        raise IdempotencyError("idempotency key is invalid")
    return value

def _check_json(value: Any, *, depth: int, counter: list[int]) -> None:
    counter[0] += 1
    if counter[0] > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
        raise IdempotencyError("JSON data exceeds structural limits")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > 9_007_199_254_740_991:
            raise IdempotencyError("JSON integer exceeds the safe range")
        return
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > 1e15:
            raise IdempotencyError("JSON number is invalid")
        return
    if isinstance(value, str):
        if len(value) > MAX_JSON_STRING_LENGTH:
            raise IdempotencyError("JSON string exceeds the allowed length")
        if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
            raise IdempotencyError("JSON string contains control characters")
        return
    if isinstance(value, list):
        if len(value) > 100:
            raise IdempotencyError("JSON array contains too many items")
        for item in value:
            _check_json(item, depth=depth + 1, counter=counter)
        return
    if isinstance(value, dict):
        if len(value) > 100:
            raise IdempotencyError("JSON object contains too many keys")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128 or not key.isprintable():
                raise IdempotencyError("JSON object key is invalid")
            _check_json(item, depth=depth + 1, counter=counter)
        return
    raise IdempotencyError("value must be JSON-compatible data")

def _canonical_json(value: Any, max_bytes: int) -> str:
    _check_json(value, depth=0, counter=[0])
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise IdempotencyError("value must be bounded JSON-compatible data") from exc
    if len(encoded) > max_bytes:
        raise IdempotencyError("JSON data exceeds the configured size limit")
    return encoded.decode("utf-8")

def initialize_idempotency_schema(connection: sqlite3.Connection) -> None:
    connection = _connection(connection)
    connection.execute(
        f"""CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            key TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('in_progress', 'completed')),
            response_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )

def make_idempotency_key(namespace: str, payload: Any) -> str:
    if not isinstance(namespace, str) or NAMESPACE_PATTERN.fullmatch(namespace) is None:
        raise IdempotencyError("idempotency namespace is invalid")
    canonical = _canonical_json(payload, MAX_IDEMPOTENCY_PAYLOAD_BYTES)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}"

def claim_idempotency(
    connection: sqlite3.Connection,
    key: str,
    payload: Any,
    *,
    at: datetime | None = None,
) -> IdempotencyRecord:
    connection = _connection(connection)
    normalized_key = _key(key)
    canonical = _canonical_json(payload, MAX_IDEMPOTENCY_PAYLOAD_BYTES)
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    timestamp = _time(at)
    initialize_idempotency_schema(connection)

    inserted = connection.execute(
        f"""INSERT INTO {TABLE_NAME}
            (key, fingerprint, status, response_json, created_at, updated_at)
            VALUES (?, ?, 'in_progress', NULL, ?, ?)
            ON CONFLICT(key) DO NOTHING""",
        (normalized_key, fingerprint, timestamp, timestamp),
    )
    row = connection.execute(
        f"SELECT key, fingerprint, status, response_json, created_at, updated_at FROM {TABLE_NAME} WHERE key = ?",
        (normalized_key,),
    ).fetchone()
    if row is None:
        raise IdempotencyError("idempotency record could not be read after claim")
    if row[1] != fingerprint:
        raise IdempotencyConflict("idempotency key was already used for a different request")
    return IdempotencyRecord(
        key=row[0],
        fingerprint=row[1],
        status=row[2],
        acquired=inserted.rowcount == 1,
        response_json=row[3],
        created_at=row[4],
        updated_at=row[5],
    )

def complete_idempotency(
    connection: sqlite3.Connection,
    key: str,
    response: Any,
    *,
    at: datetime | None = None,
) -> IdempotencyRecord:
    connection = _connection(connection)
    normalized_key = _key(key)
    response_json = _canonical_json(response, MAX_IDEMPOTENCY_RESPONSE_BYTES)
    timestamp = _time(at)
    initialize_idempotency_schema(connection)
    updated = connection.execute(
        f"""UPDATE {TABLE_NAME}
            SET status = 'completed', response_json = ?, updated_at = ?
            WHERE key = ? AND status = 'in_progress'""",
        (response_json, timestamp, normalized_key),
    )
    row = connection.execute(
        f"SELECT key, fingerprint, status, response_json, created_at, updated_at FROM {TABLE_NAME} WHERE key = ?",
        (normalized_key,),
    ).fetchone()
    if row is None:
        raise IdempotencyError("idempotency key has not been claimed")
    if updated.rowcount == 0 and (row[2] != "completed" or row[3] != response_json):
        raise IdempotencyConflict("idempotency record is already completed with a different response")
    return IdempotencyRecord(
        key=row[0],
        fingerprint=row[1],
        status=row[2],
        acquired=False,
        response_json=row[3],
        created_at=row[4],
        updated_at=row[5],
    )

def get_idempotency_record(
    connection: sqlite3.Connection,
    key: str,
) -> IdempotencyRecord | None:
    connection = _connection(connection)
    normalized_key = _key(key)
    initialize_idempotency_schema(connection)
    row = connection.execute(
        f"SELECT key, fingerprint, status, response_json, created_at, updated_at FROM {TABLE_NAME} WHERE key = ?",
        (normalized_key,),
    ).fetchone()
    if row is None:
        return None
    return IdempotencyRecord(
        key=row[0],
        fingerprint=row[1],
        status=row[2],
        acquired=False,
        response_json=row[3],
        created_at=row[4],
        updated_at=row[5],
    )
