"""Runtime settings. Every address and secret comes from AETHER_* env vars or .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AETHER_", env_file=".env", extra="ignore")

    device_token: str = "change-me"

    influx_url: str = "http://localhost:8086"
    influx_org: str = "aether"
    influx_bucket: str = "telemetry"
    influx_token: str = "change-me"

    sqlite_path: str = "data/aether.db"

    omlx_base_url: str = ""
    omlx_model: str = ""
    omlx_api_key: str = ""

    rag_brief: str = "../data/rag/psi_fire_safety_brief.json"
    rag_manual: str = "../data/rag/facts_manual.json"

    advisory_period_s: float = 30.0
    llm_first_token_timeout_s: float = 6.0
    llm_connect_timeout_s: float = 3.0
    llm_total_timeout_s: float = 20.0

    cors_origins: str = "*"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
