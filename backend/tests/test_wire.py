import json
from datetime import datetime, timezone

import pytest
from pydantic.v1 import ValidationError

from shared.agent_wire import InventoryRequest, InventoryResponse

REQUEST = dict(
    request_id="req-1", workflow_id="wf-1", trace_id="tr-1", case_id="CASE-1",
    as_of=datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc),
    case_json=json.dumps({"case_id": "CASE-1"}),
)
IDS = dict(request_id="req-1", workflow_id="wf-1", trace_id="tr-1", case_id="CASE-1")


def test_valid_request_and_responses_are_accepted():
    assert InventoryRequest(**REQUEST).case_id == "CASE-1"
    assert InventoryResponse(**IDS, status="ok", result_json="{}").status == "ok"
    assert InventoryResponse(**IDS, status="error", error_code="bad_input").status == "error"


@pytest.mark.parametrize(
    "overrides",
    [
        dict(request_id="../x"),
        dict(workflow_id=""),
        dict(as_of=datetime(2026, 1, 15)),
        dict(case_json=json.dumps({"case_id": "OTHER"})),
        dict(case_json="[1, 2]"),
        dict(case_json="not json"),
        dict(case_json=json.dumps({"case_id": "CASE-1", "pad": "a" * 70_000})),
        dict(schema_version="2.0"),
    ],
)
def test_invalid_requests_are_rejected(overrides):
    with pytest.raises(ValidationError):
        InventoryRequest(**{**REQUEST, **overrides})


@pytest.mark.parametrize(
    "fields",
    [
        dict(status="ok"),
        dict(status="ok", result_json="{}", error_code="oops"),
        dict(status="error"),
        dict(status="error", error_code="oops", result_json="{}"),
        dict(status="error", error_code="Bad Code"),
        dict(status="ok", result_json="[1]"),
    ],
)
def test_inconsistent_responses_are_rejected(fields):
    with pytest.raises(ValidationError):
        InventoryResponse(**IDS, **fields)
