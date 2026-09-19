from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the repo root, one level above backend/
ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # Simulator settings.json "ListenAddress" + /api/v1
    sim_base_url: str = "http://127.0.0.1:9898/api/v1"
    sim_email: str = "admin"
    sim_password: str = "admin"
    sim_timeout_seconds: float = 10
    # Reject webhooks whose MD5 Signature doesn't match (fake payments). Only turn off to debug.
    webhook_verify_signature: bool = True

    # MySQL (Aiven). Values come from .env, never hard-code them.
    db_host: str
    db_port: int = 3306
    db_name: str
    db_user: str
    db_password: str
    # CA certificate path (relative to repo root). Set -> TLS with full verification.
    # Empty -> no TLS (only for a local MySQL on your own machine).
    db_ssl_ca: str | None = None
    # Keep small: Aiven's free plan allows few connections and the whole team shares them
    db_pool_size: int = 3
    # pytest only: separate database on the same server (tests wipe it)
    test_db_name: str = "carpark_test"

    jwt_secret: str
    jwt_expire_minutes: int = 480
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "change-me"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
