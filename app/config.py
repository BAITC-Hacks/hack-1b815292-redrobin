"""Application configuration and environment settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from tempfile import gettempdir

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or ``.env``."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str | None = Field(default=None, alias="OPENAI_MODEL")
    max_file_mb: int = Field(default=20, gt=0, alias="MAX_FILE_MB")
    max_uncompressed_mb: int = Field(default=100, gt=0, alias="MAX_UNCOMPRESSED_MB")
    max_zip_entries: int = Field(default=2000, gt=0, alias="MAX_ZIP_ENTRIES")
    job_ttl_minutes: int = Field(default=60, gt=0, alias="JOB_TTL_MINUTES")
    analysis_timeout_seconds: int = Field(
        default=300, gt=0, alias="ANALYSIS_TIMEOUT_SECONDS"
    )
    ai_parallelism: int = Field(default=6, ge=1, le=8, alias="AI_PARALLELISM")
    temp_root: Path = Field(default=Path(gettempdir()) / "redrobin", alias="TEMP_ROOT")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide immutable configuration snapshot."""

    return Settings()
