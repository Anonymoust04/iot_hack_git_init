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
    cors_origins: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173"
    # Also allowed: any port on this machine (Vite may pick 5174, or you open 127.0.0.1). Empty = off.
    # + the frontend deployed on Vercel (https://<project>.vercel.app), so any way of starting the backend works
    cors_origin_regex: str = r"https?://(localhost|127\.0\.0\.1)(:\d+)?|https://[a-z0-9-]+\.vercel\.app"

    # Simulator settings.json "ListenAddress" + /api/v1
    sim_base_url: str = "http://127.0.0.1:9898/api/v1"
    sim_email: str = "admin"
    sim_password: str = "admin"
    sim_timeout_seconds: float = 10
    # Re-read spots + gates from the simulator this often, so the dashboard matches it even when
    # webhooks arrive out of order. Each read has a simulated operating cost. 0 = only on startup.
    sim_sync_seconds: float = 10
    # A visit with no event for this long is closed (its departure webhook was missed), so
    # "cars inside" doesn't keep growing. Must be longer than the simulator's MaxParkingTime.
    stale_session_minutes: float = 15
    # A car still ENTERING after this long never parked (e.g. sent to 'leavepark' when full): close it.
    stale_entering_minutes: float = 3
    # Reject webhooks whose MD5 Signature doesn't match (fake payments). Only turn off to debug.
    webhook_verify_signature: bool = True
    # Reject unsigned webhooks as well as invalid signatures. OFF by default: the shipped
    # simulator sends "Signature": null on every webhook (docs/LEVEL1.md), so requiring one
    # would drop all traffic. Unsigned requests are still counted and listed on the admin
    # integrity page. A WRONG signature is always rejected, whatever this is set to.
    webhook_require_signature: bool = False
    # Which barriers (GET /list-barriers) are entrances / exits, so the dashboard can label them.
    # Comma-separated. Defaults = the Level 2 park. main.py discovers the real map from the
    # simulator at startup and only uses these to override its gate pairing.
    entry_gate: str = "gate1,gate3,gate5"
    exit_gate: str = "gate2,gate4,gate6"
    # Gates held OPEN from startup and never closed by the automation (the through-gates of
    # the Level 3 map). Comma-separated; empty = none.
    always_open_gates: str = "gate7,gate19"

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
