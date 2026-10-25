from __future__ import annotations
import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Self
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )
    app_name: str = "StockRescue Warehouse API"
    warehouse_api_host: str = "127.0.0.1"
    warehouse_api_port: int = Field(default=8000, ge=1, le=65535)
    stockrescue_env: str = Field(default="development")
    database_backend: str = Field(default="sqlite")
    warehouse_database_url: SecretStr | None = Field(default=None)
    langgraph_postgres_url: SecretStr | None = Field(default=None)
    langgraph_checkpoints_enabled: bool = False
    langgraph_db_pool_max_size: int = Field(default=4, ge=1, le=20)
    database_path: str = str(BACKEND_ROOT / "data" / "stockrescue.db")
    database_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30.0)
    demo_mode: bool = True
    real_purchasing_enabled: bool = False
    warehouse_api_key: SecretStr | None = None
    supabase_url: str | None = None
    supabase_jwt_jwks_url: str | None = None
    supabase_jwt_secret: SecretStr | None = None
    supabase_jwt_issuer: str | None = None
    supabase_jwt_audience: str | None = "authenticated"
    supabase_jwt_algorithms: str = "ES256,HS256"
    supabase_jwks_cache_seconds: int = Field(default=300, ge=10, le=86400)
    supabase_jwks_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30.0)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    max_request_body_bytes: int = Field(default=1_048_576, ge=65_536, le=10_485_760)
    specialist_transport_mode: str = Field(default="local")
    specialist_timeout_seconds: float = Field(default=15.0, ge=1.0, le=60.0)
    specialist_max_retries: int = Field(default=2, ge=0, le=5)
    agentverse_allowed_senders: str = ""  # legacy fallback
    agentverse_chat_allowed_senders: str = ""
    agentverse_specialist_allowed_senders: str = ""
    agentverse_coordinator_address: str = ""
    agentverse_inventory_bom_address: str = ""
    agentverse_job_impact_address: str = ""
    agentverse_supplier_discovery_address: str = ""
    agentverse_compatibility_address: str = ""
    agentverse_quote_risk_address: str = ""
    agentverse_recovery_planner_address: str = ""
    agentverse_procurement_address: str = ""
    agentverse_vendor_voice_address: str = ""
    agentverse_delivery_watch_address: str = ""
    voice_provider: str = Field(default="simulated")
    real_telephony_enabled: bool = Field(default=False)
    elevenlabs_api_key: SecretStr | None = None
    elevenlabs_agent_id: str | None = None
    elevenlabs_agent_phone_number_id: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_phone_number: str | None = None
    voice_allowed_destinations: str = ""
    voice_max_call_duration_seconds: int = Field(default=300, ge=10, le=1800)
    voice_max_calls_per_case: int = Field(default=3, ge=1, le=10)

    def get_voice_allowed_destinations(self) -> set[str]:
        raw = self.voice_allowed_destinations
        return {s.strip() for s in raw.split(",") if s.strip()}

    def get_chat_allowed_senders(self) -> set[str]:
        raw = self.agentverse_chat_allowed_senders
        return {s.strip() for s in raw.split(",") if s.strip()}

    def get_specialist_allowed_senders(self) -> set[str]:
        raw = self.agentverse_specialist_allowed_senders
        return {s.strip() for s in raw.split(",") if s.strip()}

    def get_specialist_address(self, role: str) -> str:
        """Resolves Agentverse recipient address for a specialist role.

        Supports consolidated 8-mailbox configuration under Agentverse Basic plan:
        - Compatibility shares the Supplier Discovery address (Supplier Sourcing).
        - Recovery Planner shares the Quote Risk address (Quote & Planning).
        """
        mapping = {
            "inventory_bom": self.agentverse_inventory_bom_address,
            "job_impact": self.agentverse_job_impact_address,
            "supplier_discovery": self.agentverse_supplier_discovery_address,
            "compatibility": self.agentverse_compatibility_address or self.agentverse_supplier_discovery_address,
            "quote_risk": self.agentverse_quote_risk_address,
            "recovery_planner": self.agentverse_recovery_planner_address or self.agentverse_quote_risk_address,
            "procurement": self.agentverse_procurement_address,
            "vendor_voice": self.agentverse_vendor_voice_address,
            "delivery_watch": self.agentverse_delivery_watch_address,
        }
        return mapping.get(role, "").strip()

    def get_jwks_url(self) -> str | None:
        if self.supabase_jwt_jwks_url and self.supabase_jwt_jwks_url.strip():
            return self.supabase_jwt_jwks_url.strip()
        if self.supabase_url and self.supabase_url.strip():
            base = self.supabase_url.strip().rstrip("/")
            return f"{base}/auth/v1/.well-known/jwks.json"
        return None

    def get_jwt_issuer(self) -> str | None:
        if self.supabase_jwt_issuer and self.supabase_jwt_issuer.strip():
            return self.supabase_jwt_issuer.strip()
        if self.supabase_url and self.supabase_url.strip():
            base = self.supabase_url.strip().rstrip("/")
            return f"{base}/auth/v1"
        return None

    def is_auth_configured(self) -> bool:
        has_jwks = bool(self.get_jwks_url() and self.get_jwt_issuer())
        has_secret = bool(self.supabase_jwt_secret and self.supabase_jwt_secret.get_secret_value().strip())
        return has_jwks or has_secret

    @field_validator("app_name")
    @classmethod
    def validate_app_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 100 or not value.isprintable():
            raise ValueError("app name is invalid")
        return value
    @field_validator("warehouse_api_host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 253 or any(ch.isspace() for ch in value):
            raise ValueError("API host is invalid")
        return value
    @field_validator("database_path")
    @classmethod
    def validate_database_path(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 4096 or "\x00" in value:
            raise ValueError("database path is invalid")
        return value.strip()

    @field_validator("warehouse_api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        secret = value.get_secret_value()
        if not secret:
            return None
        if len(secret) < 32 or len(secret) > 512:
            raise ValueError("warehouse API key must contain 32 to 512 characters")
        if not secret.isprintable():
            raise ValueError("warehouse API key contains invalid characters")
        return value

    @field_validator("database_backend")
    @classmethod
    def validate_database_backend(cls, value: str) -> str:
        backend = value.strip().casefold()
        if backend not in {"sqlite", "postgres"}:
            raise ValueError("DATABASE_BACKEND must be 'sqlite' or 'postgres'")
        return backend

    @field_validator("stockrescue_env")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        env = value.strip().casefold()
        if env not in {"development", "test", "production"}:
            raise ValueError("STOCKRESCUE_ENV must be 'development', 'test', or 'production'")
        return env

    @model_validator(mode="after")
    def validate_safety_defaults(self) -> Self:
        if self.real_purchasing_enabled:
            raise ValueError("live purchasing is not implemented; REAL_PURCHASING_ENABLED must remain false")
        if self.stockrescue_env == "production":
            if self.database_backend != "postgres":
                raise ValueError("production environment requires DATABASE_BACKEND=postgres")
            if not self.warehouse_database_url or not self.warehouse_database_url.get_secret_value().strip():
                raise ValueError("WAREHOUSE_DATABASE_URL is required in production")
            if not self.langgraph_checkpoints_enabled:
                raise ValueError("LANGGRAPH_CHECKPOINTS_ENABLED must be true in production")
            if not self.langgraph_postgres_url or not self.langgraph_postgres_url.get_secret_value().strip():
                raise ValueError("LANGGRAPH_POSTGRES_URL is required in production")
            if self.warehouse_database_url.get_secret_value().strip() == self.langgraph_postgres_url.get_secret_value().strip():
                raise ValueError("warehouse and checkpoint databases must use separate URLs")
        return self
    def validate_network_binding(self) -> None:
        host = self.warehouse_api_host
        is_loopback = host.casefold() == "localhost"
        try:
            is_loopback = is_loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback and self.warehouse_api_key is None:
            raise ValueError("a WAREHOUSE_API_KEY is required when binding beyond loopback")
    def cors_origin_list(self) -> list[str]:
        origins = [part.strip() for part in self.cors_origins.split(",") if part.strip()]
        if not origins or "*" in origins:
            raise ValueError("CORS origins must be explicit; wildcard origins are not allowed")
        if any(len(origin) > 300 for origin in origins):
            raise ValueError("CORS origin is too long")
        return list(dict.fromkeys(origins))
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_network_binding()
    return settings
