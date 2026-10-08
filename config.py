"""Config
Read from the env vars and pass on as settings
ref:https://pydantic.dev/docs/validation/2.9/concepts/validators/
"""

from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process settings read from environment variables at import time.
    ingest_mode defaults to queue; sync mode is accepted only when allow_ingest_sync is true.
    Validation errors never echo the input values (they include connection URLs)."""

    database_url: str
    broker_url: str
    ingest_mode: Literal["sync", "queue"] = "queue"
    allow_ingest_sync: bool = False

    @model_validator(mode="after")
    def check_ingest_mode_valid(self) -> Self:
        """Raise ValueError when ingest_mode is sync but allow_ingest_sync is false."""
        if not self.allow_ingest_sync and self.ingest_mode == "sync":
            raise ValueError("Ingest mode cannot be sync: sync mode is not allowed")
        return self

    model_config = SettingsConfigDict(hide_input_in_errors=True)


settings = Settings()
