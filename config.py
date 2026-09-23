from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    env: str
    database_url_test: str
    database_url_dev: str
    database_url_prod: str

    @property
    def database_url(self) -> str:
        if self.env == "dev":
            return self.database_url_dev
        elif self.env == "prod":
            return self.database_url_prod
        else:
            return self.database_url_test

    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parent / ".env")


settings = Settings()
