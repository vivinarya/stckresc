"""Subprocess entry point used to prove state survives a real process restart."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from shared.config import Settings  # noqa: E402

# never read the developer's .env in a worker; the parent passes explicit variables
Settings.model_config["env_file"] = None

from helpers import NOW, make_case  # noqa: E402


def write_workflow(workflow_id: str, case_id: str) -> dict:
    from agents.coordinator.langgraph_workflow import run_coordinator_graph

    result = run_coordinator_graph(make_case(case_id=case_id), workflow_id=workflow_id, as_of=NOW)
    return {"outcome": result.outcome.value, "workflow_id": result.context.workflow_id}


def read_workflow(workflow_id: str) -> dict:
    from agents.coordinator.langgraph_workflow import get_coordinator_workflow_state

    state = get_coordinator_workflow_state(workflow_id)
    if state is None:
        return {"found": False}
    return {
        "found": True,
        "outcome": state["result"].outcome.value,
        "case_id": state["case"].case_id,
        "procurement_authorized": state["result"].procurement_authorized,
    }


def read_records(case_id: str, plan_id: str) -> dict:
    from services.warehouse_api.repository import get_repository

    repo = get_repository(Settings())
    repo.initialize()
    try:
        case = repo.get_case(case_id)
        plan = repo.get_plan(plan_id)
        return {"case_id": case.case_id, "plan_case_id": plan.case_id, "total_cost": str(plan.total_cost)}
    finally:
        repo.close()


def read_approval(approval_id: str) -> dict:
    from services.warehouse_api.repository import get_repository

    repo = get_repository(Settings())
    repo.initialize()
    try:
        appr = repo.get_approval(approval_id)
        return {
            "approval_id": appr.approval_id,
            "case_id": appr.case_id,
            "plan_id": appr.plan_id,
            "plan_fingerprint": appr.plan_fingerprint,
            "revoked": appr.is_revoked(),
            "max_total_cost": str(appr.max_total_cost),
        }
    finally:
        repo.close()


def read_audit(entity_id: str) -> dict:
    from services.warehouse_api.repository import get_repository

    repo = get_repository(Settings())
    repo.initialize()
    try:
        events, count = repo.list_audit_events(entity_id)
        return {
            "count": count,
            "events": [
                {
                    "event_id": e.event_id,
                    "event_type": e.event_type,
                    "actor_id": e.actor_id,
                    "entity_id": e.entity_id,
                }
                for e in events
            ],
        }
    finally:
        repo.close()


if __name__ == "__main__":
    command, *args = sys.argv[1:]
    handlers = {
        "write-workflow": write_workflow,
        "read-workflow": read_workflow,
        "read-records": read_records,
        "read-approval": read_approval,
        "read-audit": read_audit,
    }
    print(json.dumps(handlers[command](*args)))
