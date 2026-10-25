from decimal import Decimal

import pytest

from observability import tracing
from observability.tracing import _safe_value, trace_span


def test_sensitive_keys_are_redacted_and_bare_text_is_hidden():
    value = _safe_value({
        "api_key": "abc", "password": "p", "supplier_name": "Acme",
        "description": "free text", "count": 3, "nested": {"token": "t", "ok": "fine"},
    })
    assert value["api_key"] == value["password"] == "[REDACTED]"
    assert value["supplier_name"] == value["description"] == "[REDACTED]"
    assert value["nested"] == {"token": "[REDACTED]", "ok": "fine"}
    assert value["count"] == 3
    assert _safe_value("loose text") == "[REDACTED TEXT]"


def test_values_are_bounded_and_made_json_safe():
    assert _safe_value({"price": Decimal("1.50")}) == {"price": "1.50"}
    assert _safe_value({"n": float("nan")}) == {"n": "[NUMBER REDACTED]"}
    assert _safe_value({"n": 2 ** 60}) == {"n": "[NUMBER REDACTED]"}
    assert len(_safe_value(list(range(100)))) == tracing.MAX_TRACE_ITEMS + 1
    assert _safe_value({"v": {"o": object()}})["v"]["o"].startswith("[UNSUPPORTED")


def test_disabled_tracing_never_touches_langfuse(monkeypatch):
    def boom():
        raise AssertionError("langfuse must not be used when tracing is disabled")

    monkeypatch.setattr("langfuse.get_client", boom)
    with trace_span("agents.test", input_data={"api_key": "zzz"}) as span:
        span.update(output={"ok": 1})


class FakeObservation:
    def __init__(self):
        self.updates = []

    def update(self, **values):
        self.updates.append(values)


class FakeManager:
    def __init__(self, observation):
        self.observation, self.exit_args = observation, None

    def __enter__(self):
        return self.observation

    def __exit__(self, *args):
        self.exit_args = args


@pytest.fixture
def enabled_tracing(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    seen = {}
    observation = FakeObservation()
    manager = FakeManager(observation)

    class Client:
        def start_as_current_observation(self, **kwargs):
            seen.update(kwargs)
            return manager

    monkeypatch.setattr("langfuse.get_client", lambda: Client())
    return seen, observation, manager


def test_enabled_tracing_redacts_inputs_before_sending(enabled_tracing):
    seen, observation, manager = enabled_tracing
    with trace_span("agents.test", workflow_id="wf-1",
                    input_data={"api_key": "zzz", "qty": 2}) as span:
        span.update(output={"supplier_name": "Acme", "total": Decimal("9.00")})
    assert seen["input"] == {"api_key": "[REDACTED]", "qty": 2}
    assert seen["metadata"]["workflow_id"] == "wf-1"
    assert observation.updates[0]["output"] == {"supplier_name": "[REDACTED]", "total": "9.00"}
    assert manager.exit_args == (None, None, None)


def test_exceptions_are_marked_on_the_span_and_still_propagate(enabled_tracing):
    _, observation, manager = enabled_tracing
    with pytest.raises(ValueError):
        with trace_span("agents.test"):
            raise ValueError("boom")
    assert observation.updates[-1] == {"level": "ERROR", "status_message": "ValueError"}
    assert manager.exit_args[0] is ValueError


def test_tracing_failures_never_break_the_workflow(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    def broken():
        raise RuntimeError("langfuse down")

    monkeypatch.setattr("langfuse.get_client", broken)
    with trace_span("agents.test") as span:
        span.update(output={"ok": 1})
    tracing.flush_traces()


def test_missing_credentials_disables_tracing(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    called = False

    def boom():
        nonlocal called
        called = True
        raise AssertionError("Should not be called")

    monkeypatch.setattr("langfuse.get_client", boom)
    with trace_span("agents.test") as span:
        span.update(output={"should": "noop"})
    assert not called


def test_trace_id_passes_trace_context(enabled_tracing):
    seen, observation, manager = enabled_tracing
    hex32_trace_id = "0123456789abcdef0123456789abcdef"
    with trace_span(
        "agents.test",
        workflow_id="wf-100",
        correlation_id="corr-200",
        trace_id=hex32_trace_id,
    ) as span:
        span.update(output={"status": "ok"})

    assert seen["metadata"]["workflow_id"] == "wf-100"
    assert seen["metadata"]["correlation_id"] == "corr-200"
    assert seen["metadata"]["trace_id"] == hex32_trace_id
    assert seen["trace_context"] == {"trace_id": hex32_trace_id}


def test_specialist_dispatcher_dispatch_records_span(enabled_tracing):
    seen, observation, manager = enabled_tracing
    from agents.protocols.dispatcher import SpecialistDispatcher
    from agents.protocols.messages import TaskRequest

    dispatcher = SpecialistDispatcher(
        role="inventory_bom",
        agent_name="stockrescue-inventory-bom",
        allowed_senders={"agent1_coordinator"},
        permitted_tasks={"inventory_bom"},
    )

    req = TaskRequest.create(
        task_type="inventory_bom",
        workflow_id="wf-dispatch-1",
        trace_id="tr-dispatch-1",
        sender="agent1_coordinator",
        recipient="agent1_inventory",
        payload={"case": {}, "context": {}},
        timeout_seconds=5.0,
    )

    # Disallowed sender test
    resp = dispatcher.dispatch("agent1_unauthorized", req)
    assert resp.status.value == "rejected"
    assert len(observation.updates) >= 1
    assert observation.updates[-1]["output"]["status"] == "rejected"
    assert observation.updates[-1]["output"]["error_category"] == "unauthorized_sender"


def test_redaction_covers_supplier_vendor_raw_and_secrets():
    from observability.redaction import redact_value, redact_text

    raw_data = {
        "supplier_name": "MegaCorp",
        "vendor_name": "AlphaLogistics",
        "raw": "unparsed secret string",
        "password": "supersecretpassword",
        "jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotLeakThisSignature",
        "safe_metric": 42,
    }
    redacted = redact_value(raw_data)
    assert redacted["supplier_name"] == "[REDACTED]"
    assert redacted["vendor_name"] == "[REDACTED]"
    assert redacted["raw"] == "[REDACTED]"
    assert redacted["password"] == "[REDACTED]"
    assert redacted["jwt"] == "[REDACTED]"
    assert redacted["safe_metric"] == 42

    text = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotLeakThisSignature postgresql://user:pass123@localhost:5432/db"
    safe_text = redact_text(text)
    assert "pass123" not in safe_text
    assert "doNotLeakThisSignature" not in safe_text
