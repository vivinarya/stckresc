from __future__ import annotations
import logging
import math
import os
import re
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Iterator


#limits
MAX_TRACE_DEPTH = 5
MAX_TRACE_ITEMS = 30
MAX_TRACE_STRING = 200
TRACE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$", re.ASCII)
SENSITIVE_KEY_PATTERN = re.compile(
    r"secret|password|token|authorization|cookie|api.?key|private.?key|credential|email|phone|address|customer|supplier_name|vendor_name|approved_by|user.?id|account|raw|prompt|system|instruction|question|query|request|response|input|output|message|payload|description|details|comment|evidence|explanation|reason|notes|transcript|text|content",
    re.IGNORECASE,
)

logger = logging.getLogger(__name__)

class _NoOpObservation:
    def update(self, **kwargs: Any) -> None:
        return None

class _SafeObservation:
    def __init__(self, observation: Any) -> None:
        self._observation = observation

    def update(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        values: dict[str, Any] = {}
        if output is not None:
            values["output"] = _safe_value(output)
        if metadata is not None:
            values["metadata"] = _safe_value(metadata)
        if level in {"DEBUG", "DEFAULT", "WARNING", "ERROR"}:
            values["level"] = level
        if status_message is not None:
            values["status_message"] = _safe_status(status_message)
        try:
            self._observation.update(**values)
        except Exception as exc:
            logger.debug("Langfuse observation update failed (%s)", type(exc).__name__)

def _tracing_enabled() -> bool:
    try:
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except Exception:
        pass
    enabled = os.getenv("LANGFUSE_ENABLED", "false").strip().casefold() == "true"
    credentials_present = bool(os.getenv("LANGFUSE_PUBLIC_KEY")) and bool(os.getenv("LANGFUSE_SECRET_KEY"))
    return enabled and credentials_present

def _safe_status(value: str) -> str:
    if not isinstance(value, str):
        return "operation status unavailable"
    cleaned = " ".join(value.split())
    return cleaned[:MAX_TRACE_STRING] or "operation status unavailable"

def _safe_value(value: Any, *, key: str | None = None, depth: int = 0) -> Any:
    if key and SENSITIVE_KEY_PATTERN.search(key):
        return "[REDACTED]"
    if depth > MAX_TRACE_DEPTH:
        return "[TRUNCATED]"
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, Enum):
        return _safe_value(value.value, key=key, depth=depth + 1)
    if isinstance(value, int):
        return value if abs(value) <= 9_007_199_254_740_991 else "[NUMBER REDACTED]"
    if isinstance(value, float):
        return value if math.isfinite(value) and abs(value) <= 1e15 else "[NUMBER REDACTED]"
    if isinstance(value, Decimal):
        return str(value) if value.is_finite() else "[NUMBER REDACTED]"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        if key is None:
            return "[REDACTED TEXT]"
        cleaned = " ".join("".join(ch for ch in value if ch >= " " or ch in "\t\n\r").split())
        return cleaned[:MAX_TRACE_STRING]
    if isinstance(value, dict):
        result = {}
        for index, (child_key, child_value) in enumerate(value.items()):
            if index >= MAX_TRACE_ITEMS:
                result["_truncated"] = True
                break
            if not isinstance(child_key, str) or not child_key or len(child_key) > 64 or not child_key.isprintable():
                continue
            result[child_key[:64]] = _safe_value(child_value, key=child_key, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        return [
            _safe_value(item, depth=depth + 1)
            for item in items[:MAX_TRACE_ITEMS]
        ] + (["[TRUNCATED]"] if len(items) > MAX_TRACE_ITEMS else [])
    return f"[UNSUPPORTED {type(value).__name__}]"

@contextmanager
def trace_span(
    name: str,
    *,
    workflow_id: str | None = None,
    correlation_id: str | None = None,
    trace_id: str | None = None,
    input_data: Any = None,
    metadata: dict[str, Any] | None = None,
    enabled: bool | None = None,
) -> Iterator[_SafeObservation | _NoOpObservation]:
    trace_name = name.strip() if isinstance(name, str) else "stockrescue-operation"
    if TRACE_NAME_PATTERN.fullmatch(trace_name) is None:
        trace_name = "stockrescue-operation"
    use_tracing = _tracing_enabled() if enabled is None else bool(enabled) and _tracing_enabled()
    if not use_tracing:
        yield _NoOpObservation()
        return
    try:
        from langfuse import get_client
        client = get_client()
        safe_metadata = _safe_value(metadata or {})
        if not isinstance(safe_metadata, dict):
            safe_metadata = {}
        if workflow_id is not None:
            safe_metadata["workflow_id"] = _safe_value(workflow_id, key="workflow_id")
        if correlation_id is not None:
            safe_metadata["correlation_id"] = _safe_value(correlation_id, key="correlation_id")
        if trace_id is not None:
            safe_metadata["trace_id"] = _safe_value(trace_id, key="trace_id")

        # Pass trace_context only if trace_id is exactly 32 lowercase hex chars (Langfuse/OTel requirement)
        trace_context = None
        if trace_id and isinstance(trace_id, str):
            clean_tid = trace_id.strip().lower()
            if len(clean_tid) == 32 and all(c in "0123456789abcdef" for c in clean_tid):
                trace_context = {"trace_id": clean_tid}

        start_kwargs: dict[str, Any] = {
            "as_type": "span",
            "name": trace_name,
            "input": _safe_value(input_data),
            "metadata": safe_metadata,
        }
        if trace_context:
            start_kwargs["trace_context"] = trace_context

        manager = client.start_as_current_observation(**start_kwargs)
        observation = manager.__enter__()
    except Exception as exc:
        logger.debug("Langfuse tracing unavailable (%s)", type(exc).__name__)
        yield _NoOpObservation()
        return
    wrapped = _SafeObservation(observation)
    try:
        yield wrapped
    except BaseException as exc:
        wrapped.update(level="ERROR", status_message=type(exc).__name__)
        try:
            manager.__exit__(type(exc), exc, exc.__traceback__)
        except Exception as trace_exc:
            logger.debug("Langfuse span close failed (%s)", type(trace_exc).__name__)
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception as exc:
            logger.debug("Langfuse span close failed (%s)", type(exc).__name__)

def flush_traces() -> None:
    if not _tracing_enabled():
        return
    try:
        from langfuse import get_client
        get_client().flush()
    except Exception as exc:
        logger.debug("Langfuse flush failed (%s)", type(exc).__name__)
