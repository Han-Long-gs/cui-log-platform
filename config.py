from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    broker_url: str
    ingest_mode: str


settings = Settings()
