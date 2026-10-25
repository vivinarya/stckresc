from __future__ import annotations
import json
from datetime import datetime
from typing import Literal, Optional
from pydantic.v1 import Field, root_validator, validator
from uagents import Model

# uagents.Model is a pydantic v1 model; keep this module on the v1 API
IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$"
ERROR_CODE_PATTERN = r"^[a-z][a-z0-9_.-]{0,63}$"
MAX_CASE_JSON_BYTES = 64 * 1024


def _json_object(value: str, label: str) -> dict:
    if len(value.encode("utf-8")) > MAX_CASE_JSON_BYTES:
        raise ValueError(f"{label} payload exceeds the size limit")
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label} payload must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} payload must be a JSON object")
    return parsed


class InventoryRequest(Model):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    workflow_id: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    trace_id: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    case_id: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    as_of: datetime
    case_json: str = Field(min_length=2, max_length=MAX_CASE_JSON_BYTES)

    @validator("as_of")
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value

    @validator("case_json")
    def validate_case_json(cls, value: str) -> str:
        _json_object(value, "case")
        return value

    @root_validator(skip_on_failure=True)
    def validate_case_identity(cls, values: dict) -> dict:
        if _json_object(values["case_json"], "case").get("case_id") != values["case_id"]:
            raise ValueError("case identifier does not match payload")
        return values


class InventoryResponse(Model):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    workflow_id: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    trace_id: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    case_id: str = Field(min_length=1, max_length=100, regex=IDENTIFIER_PATTERN)
    status: Literal["ok", "error"]
    result_json: Optional[str] = Field(default=None, max_length=MAX_CASE_JSON_BYTES)
    error_code: Optional[str] = Field(
        default=None, min_length=1, max_length=64, regex=ERROR_CODE_PATTERN
    )

    @validator("result_json")
    def validate_result_json(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            _json_object(value, "result")
        return value

    @root_validator(skip_on_failure=True)
    def validate_response_status(cls, values: dict) -> dict:
        result_json = values.get("result_json")
        error_code = values.get("error_code")
        if values["status"] == "ok":
            if result_json is None or error_code is not None:
                raise ValueError("successful response requires a result only")
        elif result_json is not None or error_code is None:
            raise ValueError("error response requires an error code only")
        return values
