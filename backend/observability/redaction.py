
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, SecretStr


REDACTED = "[REDACTED]"
MAX_TEXT_CHARS = 4_000
MAX_DEPTH = 8
MAX_ITEMS = 100

SENSITIVE_KEYS = {
    "apikey",
    "accesskey",
    "accesstoken",
    "authtoken",
    "authorization",
    "bearer",
    "clientsecret",
    "connectionstring",
    "cookie",
    "credential",
    "credentials",
    "databaseurl",
    "dsn",
    "password",
    "passwd",
    "privatekey",
    "refreshtoken",
    "secret",
    "secretkey",
    "seed",
    "sessiontoken",
    "setcookie",
    "token",
    "warehouseurl",
    "suppliername",
    "vendorname",
    "raw",
}


_KEY_VALUE_PATTERN = re.compile(
    r"""(?ix)
    \b
    (api[_-]?key|access[_-]?token|refresh[_-]?token|jwt[_-]?secret|
     authorization|password|passwd|client[_-]?secret|jwt|
     secret[_-]?key|private[_-]?key|database[_-]?url|
     warehouse[_-]?database[_-]?url|langgraph[_-]?postgres[_-]?url|
     connection[_-]?string|session[_-]?token|agentverse[_-]?seed|
     seed|secret|dsn)
    \b
    (\s*[:=]\s*)
    (?:"[^"]*"|'[^']*'|[^\s,;]+)
    """
)


_DATABASE_URL_PATTERN = re.compile(
    r"(?i)\b(postgresql|postgres|mysql|redis)://"
    r"([^:/@\s]+):([^@\s/]+)@"
)

_BEARER_PATTERN = re.compile(
    r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+"
)

# Standard JWT format: header.payload.signature
_JWT_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9._~+/=-]+\b"
)

_TOKEN_PATTERN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|"
    r"hf_[A-Za-z0-9]{20,})\b"
)

_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _normalise_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.casefold())


def _sensitive_key(key: str) -> bool:
    normalized = _normalise_key(key)

    if normalized in SENSITIVE_KEYS:
        return True

    return any(
        marker in normalized
        for marker in (
            "password",
            "passwd",
            "secret",
            "credential",
            "privatekey",
            "accesstoken",
            "refreshtoken",
            "sessiontoken",
            "apikey",
            "authorization",
            "connectionstring",
        )
    )


def redact_text(value: str, *, max_chars: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str):
        return "[UNAVAILABLE]"

    if not isinstance(max_chars, int) or isinstance(max_chars, bool):
        max_chars = MAX_TEXT_CHARS

    max_chars = max(1, min(max_chars, MAX_TEXT_CHARS))
    text = value[:max_chars]
    text = _CONTROL_PATTERN.sub("", text)

    text = _DATABASE_URL_PATTERN.sub(
        lambda match: (
            f"{match.group(1)}://{match.group(2)}:"
            f"{REDACTED}@"
        ),
        text,
    )


    text = _KEY_VALUE_PATTERN.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}{REDACTED}"
        ),
        text,
    )


    text = _BEARER_PATTERN.sub(
        lambda match: f"{match.group(1)}{REDACTED}",
        text,
    )
    text = _JWT_PATTERN.sub(REDACTED, text)
    text = _TOKEN_PATTERN.sub(REDACTED, text)

    return text


def redact_value(
    value: Any,
    *,
    key: str | None = None,
    _depth: int = 0,
    _counter: list[int] | None = None,
) -> Any:
    if key is not None and _sensitive_key(key):
        return REDACTED

    if _counter is None:
        _counter = [0]

    _counter[0] += 1

    if _counter[0] > MAX_ITEMS * 10:
        return "[TRUNCATED]"

    if _depth > MAX_DEPTH:
        return "[MAX_DEPTH]"

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, SecretStr):
        return REDACTED

    if isinstance(value, str):
        return redact_text(value)

    if isinstance(value, BaseModel):
        try:
            value = value.model_dump(mode="python")
        except Exception:
            return f"[{type(value).__name__}]"

    if isinstance(value, Mapping):
        output = {}

        for index, (item_key, item_value) in enumerate(value.items()):
            if index >= MAX_ITEMS:
                output["_truncated"] = True
                break

            if not isinstance(item_key, str):
                output[f"_invalid_key_{index}"] = REDACTED
                continue

            safe_key = redact_text(item_key, max_chars=128)
            output[safe_key] = redact_value(
                item_value,
                key=item_key,
                _depth=_depth + 1,
                _counter=_counter,
            )

        return output

    if isinstance(value, (list, tuple, set, frozenset)):
        items = []

        for index, item in enumerate(value):
            if index >= MAX_ITEMS:
                items.append("[TRUNCATED]")
                break

            items.append(
                redact_value(
                    item,
                    _depth=_depth + 1,
                    _counter=_counter,
                )
            )

        return items

    return f"[{type(value).__name__}]"


def redact_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    result = redact_value(value)

    if not isinstance(result, dict):
        return {"value": REDACTED}

    return result


def safe_exception_message(exc: Exception) -> str:
    name = type(exc).__name__
    details = redact_text(str(exc), max_chars=500)

    return f"{name}: {details}"
