"""Application configuration via pydantic-settings.

All settings can be overridden with environment variables or a `.env` file.
See `.env.example` for the full list.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the service.

    ROLE: single source of truth for env-driven config; read once at
    startup and injected via `app.api.deps.get_settings`.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./geospatial.db"
    upload_dir: Path = Path("./uploads")
    max_upload_mb: int = 50
    log_level: str = "INFO"


def get_settings() -> Settings:
    """Return a fresh Settings instance (cached by the caller in deps)."""
    return Settings()
