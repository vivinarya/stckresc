"""Shared typed request/response contract for Agentverse and inter-agent communication.

Implements strict schema validation, timezone-aware creation and deadline timestamps,
bounded JSON payloads, explicit task types, correlated responses, and safe error codes.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic.v1 import Field, root_validator, validator
from uagents import Model

IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$"
ERROR_CODE_PATTERN = r"^[a-z][a-z0-9_.-]{0,63}$"

MAX_PAYLOAD_BYTES = 64 * 1024  # 64 KiB
MIN_DEADLINE_SECONDS = 1
MAX_DEADLINE_SECONDS = 600  # 10 minutes maximum deadline window

ALLOWED_TASK_TYPES = frozenset({
    "inventory_bom",
    "job_impact",
    "supplier_discovery",
    "compatibility",
    "quote_risk",
    "recovery_planner",
    "procurement_draft",
    "vendor_communication_prep",
    "vendor_voice_call",
    "delivery_watch",
})


class TaskResponseStatus(str, Enum):
    OK = "ok"
    REJECTED = "rejected"
    INVALID_REQUEST = "invalid_request"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INTERNAL_ERROR = "internal_error"


class TaskErrorCode(str, Enum):
    INVALID_PAYLOAD = "invalid_payload"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    TASK_TYPE_UNSUPPORTED = "task_type_unsupported"
    TASK_TYPE_FORBIDDEN = "task_type_forbidden"
    UNAUTHORIZED_SENDER = "unauthorized_sender"
    DEADLINE_EXPIRED = "deadline_expired"
    DEADLINE_INVALID = "deadline_invalid"
    CORRELATION_MISMATCH = "correlation_mismatch"
    TIMEOUT = "timeout"
    AGENT_UNAVAILABLE = "agent_unavailable"
    INTERNAL_ERROR = "internal_error"
    BUSINESS_RULE_VIOLATION = "business_rule_violation"


class MessageContractError(ValueError):
    """Raised when message serialization, correlation or validation fails."""
    pass


class CorrelationMismatchError(MessageContractError):
    """Raised when a response does not match the original request."""
    pass


def _validate_json_object(value: str, label: str) -> dict[str, Any]:
    encoded = value.encode("utf-8")
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"{label} payload exceeds {MAX_PAYLOAD_BYTES} bytes limit")
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label} payload must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} payload must be a JSON object")
    return parsed


def _dump_model(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(
            mode="json",
            exclude={
                "inventory": {"__all__": {"available_quantity"}},
                "lines": {"__all__": {"line_total"}},
            },
        )
    if hasattr(obj, "dict"):
        return obj.dict()
    if isinstance(obj, dict):
        return {k: _dump_model(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_dump_model(x) for x in obj]
    return obj


def serialize_task_payload(data: Any) -> str:
    """Serializes a dictionary or Pydantic model into a validated JSON string."""
    raw = _dump_model(data)
    if not isinstance(raw, dict):
        raise MessageContractError("Payload data must be a dict or Pydantic model")

    try:
        encoded_str = json.dumps(raw, ensure_ascii=False, allow_nan=False, default=str)
    except (TypeError, ValueError) as exc:
        raise MessageContractError("Failed to serialize payload to JSON") from exc

    if len(encoded_str.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise MessageContractError(f"Serialized payload exceeds {MAX_PAYLOAD_BYTES} bytes limit")
    return encoded_str


class TaskRequest(Model):
    """Typed remote task request passed between agents."""

    schema_version: Literal["1.0"] = "1.0"
    message_id: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    correlation_id: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    workflow_id: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    trace_id: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    sender: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    recipient: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    task_type: str = Field(min_length=1, max_length=64)
    created_at: datetime
    deadline_at: datetime
    payload_json: str = Field(min_length=2, max_length=MAX_PAYLOAD_BYTES)

    @validator("task_type")
    def validate_task_type(cls, value: str) -> str:
        if value not in ALLOWED_TASK_TYPES:
            raise ValueError(f"task_type '{value}' is not supported")
        return value

    @validator("created_at")
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value

    @validator("deadline_at")
    def validate_deadline_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline_at must include a timezone")
        return value

    @validator("payload_json")
    def validate_payload_json(cls, value: str) -> str:
        _validate_json_object(value, "TaskRequest")
        return value

    @root_validator(skip_on_failure=True)
    def validate_timestamps(cls, values: dict) -> dict:
        created_at = values.get("created_at")
        deadline_at = values.get("deadline_at")
        if created_at and deadline_at:
            if deadline_at <= created_at:
                raise ValueError("deadline_at must be strictly later than created_at")
            if deadline_at > created_at + timedelta(seconds=MAX_DEADLINE_SECONDS):
                raise ValueError(f"deadline window cannot exceed {MAX_DEADLINE_SECONDS} seconds")
        return values

    def is_expired(self, as_of: datetime | None = None) -> bool:
        check_time = as_of or datetime.now(timezone.utc)
        if check_time.tzinfo is None or check_time.utcoffset() is None:
            raise MessageContractError("as_of comparison time must include a timezone")
        return check_time >= self.deadline_at

    def get_payload(self) -> dict[str, Any]:
        return _validate_json_object(self.payload_json, "TaskRequest")

    @classmethod
    def create(
        cls,
        *,
        task_type: str,
        workflow_id: str,
        trace_id: str,
        sender: str,
        recipient: str,
        payload: Any,
        correlation_id: str | None = None,
        message_id: str | None = None,
        created_at: datetime | None = None,
        timeout_seconds: float = 30.0,
    ) -> "TaskRequest":
        now = created_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise MessageContractError("created_at must include a timezone")

        deadline_window = max(float(MIN_DEADLINE_SECONDS), min(float(MAX_DEADLINE_SECONDS), float(timeout_seconds)))
        deadline = now + timedelta(seconds=deadline_window)

        payload_json = serialize_task_payload(payload)
        return cls(
            schema_version="1.0",
            message_id=message_id or str(uuid4()),
            correlation_id=correlation_id or str(uuid4()),
            workflow_id=workflow_id,
            trace_id=trace_id,
            sender=sender,
            recipient=recipient,
            task_type=task_type,
            created_at=now,
            deadline_at=deadline,
            payload_json=payload_json,
        )


class TaskResponse(Model):
    """Typed remote task response passed back to requesting agent."""

    schema_version: Literal["1.0"] = "1.0"
    message_id: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    correlation_id: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    workflow_id: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    trace_id: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    sender: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    recipient: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    task_type: str = Field(min_length=1, max_length=64)
    status: TaskResponseStatus
    result_json: Optional[str] = Field(default=None, max_length=MAX_PAYLOAD_BYTES)
    error_code: Optional[str] = Field(
        default=None, min_length=1, max_length=64, regex=ERROR_CODE_PATTERN
    )
    error_detail: Optional[str] = Field(default=None, max_length=500)
    created_at: datetime

    @validator("task_type")
    def validate_task_type(cls, value: str) -> str:
        if value not in ALLOWED_TASK_TYPES:
            raise ValueError(f"task_type '{value}' is not supported")
        return value

    @validator("created_at")
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value

    @validator("result_json")
    def validate_result_json(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            _validate_json_object(value, "TaskResponse")
        return value

    @root_validator(skip_on_failure=True)
    def validate_status_consistency(cls, values: dict) -> dict:
        status = values.get("status")
        result_json = values.get("result_json")
        error_code = values.get("error_code")

        if status == TaskResponseStatus.OK:
            if result_json is None:
                raise ValueError("successful response requires result_json")
            if error_code is not None:
                raise ValueError("successful response cannot carry an error_code")
        else:
            if error_code is None:
                raise ValueError("error response requires an error_code")
            if result_json is not None:
                raise ValueError("error response cannot carry result_json")
        return values

    def get_result(self) -> dict[str, Any] | None:
        if self.result_json is None:
            return None
        return _validate_json_object(self.result_json, "TaskResponse")

    @classmethod
    def ok(
        cls,
        request: TaskRequest,
        *,
        sender: str,
        result: Any,
        message_id: str | None = None,
        created_at: datetime | None = None,
    ) -> "TaskResponse":
        now = created_at or datetime.now(timezone.utc)
        result_json = serialize_task_payload(result)
        return cls(
            schema_version="1.0",
            message_id=message_id or str(uuid4()),
            correlation_id=request.correlation_id,
            workflow_id=request.workflow_id,
            trace_id=request.trace_id,
            sender=sender,
            recipient=request.sender,
            task_type=request.task_type,
            status=TaskResponseStatus.OK,
            result_json=result_json,
            error_code=None,
            error_detail=None,
            created_at=now,
        )

    @classmethod
    def failure(
        cls,
        request: TaskRequest,
        *,
        sender: str,
        status: TaskResponseStatus,
        error_code: str | TaskErrorCode,
        error_detail: str | None = None,
        message_id: str | None = None,
        created_at: datetime | None = None,
    ) -> "TaskResponse":
        if status == TaskResponseStatus.OK:
            raise MessageContractError("Cannot use TaskResponse.failure with status OK")
        code_str = error_code.value if isinstance(error_code, TaskErrorCode) else str(error_code)
        now = created_at or datetime.now(timezone.utc)
        safe_detail = (error_detail[:500] if error_detail else None)
        return cls(
            schema_version="1.0",
            message_id=message_id or str(uuid4()),
            correlation_id=request.correlation_id,
            workflow_id=request.workflow_id,
            trace_id=request.trace_id,
            sender=sender,
            recipient=request.sender,
            task_type=request.task_type,
            status=status,
            result_json=None,
            error_code=code_str,
            error_detail=safe_detail,
            created_at=now,
        )


def verify_response_matches_request(request: TaskRequest, response: TaskResponse) -> None:
    """Verifies correlation, workflow, trace, task type, and recipient linkage."""
    if response.correlation_id != request.correlation_id:
        raise CorrelationMismatchError(
            f"Correlation ID mismatch: expected {request.correlation_id}, got {response.correlation_id}"
        )
    if response.workflow_id != request.workflow_id:
        raise CorrelationMismatchError(
            f"Workflow ID mismatch: expected {request.workflow_id}, got {response.workflow_id}"
        )
    if response.trace_id != request.trace_id:
        raise CorrelationMismatchError(
            f"Trace ID mismatch: expected {request.trace_id}, got {response.trace_id}"
        )
    if response.task_type != request.task_type:
        raise CorrelationMismatchError(
            f"Task type mismatch: expected {request.task_type}, got {response.task_type}"
        )
    if response.recipient != request.sender:
        raise CorrelationMismatchError(
            f"Recipient mismatch: expected {request.sender}, got {response.recipient}"
        )
