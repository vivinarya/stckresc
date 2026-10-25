import pytest

from shared.config import Settings, get_settings

# manual connectivity script that calls Langfuse at import time
collect_ignore = ["test_langfuse.py"]

# every variable Settings can read; tests must never inherit them from the shell
SETTINGS_ENV_NAMES = [name.upper() for name in Settings.model_fields]


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    get_settings.cache_clear()
    # tests must never reach real services or pick up real secrets from .env
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in SETTINGS_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name in (
        "LANGFUSE_ENABLED",
        "ASI_ONE_API_KEY",
        "ASI_ONE_MODEL",
        "LANGGRAPH_DB_POOL_MAX_SIZE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("LANGGRAPH_CHECKPOINTS_ENABLED", "false")
    monkeypatch.setenv("STOCKRESCUE_ENV", "development")
    yield
    get_settings.cache_clear()
