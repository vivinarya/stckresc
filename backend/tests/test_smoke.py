import importlib

import pytest

MODULES = [
    "agents.compatibility.agent",
    "agents.coordinator.agent",
    "agents.coordinator.agentverse_entry",
    "agents.coordinator.langgraph_workflow",
    "agents.inventory_bom.agent",
    "agents.job_impact.agent",
    "agents.supplier_discovery.agent",
    "observability.tracing",
    "services.intent.resolve_intent",
    "services.warehouse_api.main",
    "services.warehouse_api.postgres_repository",
    "services.warehouse_api.repository",
    "services.warehouse_api.schemas",
    "shared.agent_contracts",
    "shared.agent_wire",
    "shared.checkpointing",
    "shared.config",
    "shared.idempotency",
    "shared.models",
    "shared.state_machine",
    "tools.compatibility",
    "tools.inventory",
    "tools.purchase_orders",
    "tools.quote_normalizer",
    "tools.recovery_optimizer",
    "tools.supplier_catalog",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    importlib.import_module(name)


def test_public_repository_classes_exist():
    from services.warehouse_api.postgres_repository import PostgresWarehouseRepository
    from services.warehouse_api.repository import SQLiteWarehouseRepository

    for cls in (PostgresWarehouseRepository, SQLiteWarehouseRepository):
        for method in ("initialize", "create_case", "get_case", "list_cases",
                       "save_plan", "get_plan", "list_plans"):
            assert callable(getattr(cls, method)), (cls.__name__, method)
