"""Env-driven settings — read at app startup."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Neo4j
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "lineagepass"
    neo4j_database: str = "neo4j"

    # Postgres (TWS mirror)
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "lineage"
    postgres_user: str = "lineage"
    postgres_password: str = "lineagepass"
    postgres_schema: str = "tws"

    # Per-parser service URLs (in-cluster DNS names from docker-compose)
    parser_tableau_url: str = "http://tableau-parser:8000"
    parser_tws_url: str = "http://tws-parser:8000"
    parser_qlikview_url: str = "http://qlikview-parser:8000"
    parser_spark_url: str = "http://spark-parser:8000"

    # CORS — comma-separated list, default permissive in dev only
    cors_allowed_origins: str = "http://localhost:3000,http://localhost:3001"

    log_level: str = "INFO"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
