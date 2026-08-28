"""Application settings, loaded from environment variables.

No secret is ever hardcoded here — everything comes from the environment so the
same image runs locally (SQLite) and in production (Postgres) unchanged.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # `sqlite:///./patients.db` locally; a Postgres URL in production.
    database_url: str = "sqlite:///./patients.db"

    # Shared secret Vapi sends on every tool call. Empty string == check disabled.
    vapi_server_secret: str = ""

    # Optional file to mirror the JSON conversation log into.
    log_file: str = ""

    app_name: str = "Voice AI Patient Registration"
    app_version: str = "1.0.0"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def normalized_database_url(self) -> str:
        """Neon/Render/Heroku hand out `postgres://...`, which SQLAlchemy 2.x
        refuses. Rewrite it to the driver-qualified form so the deploy step is
        a copy-paste rather than a debugging session."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg2://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is parsed once per process."""
    return Settings()
