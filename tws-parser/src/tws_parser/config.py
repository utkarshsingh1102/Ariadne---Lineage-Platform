from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7688"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "lineagepass"
    neo4j_database: str = "neo4j"

    # Postgres
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "lineage"
    postgres_user: str = "lineage"
    postgres_password: str = "lineagepass"
    postgres_schema: str = "tws"

    # Misc
    log_level: str = "INFO"
    batch_size: int = 1000
    max_file_size_mb: int = 200
    script_path_strip_args: bool = True
    strict_parsing: bool = False

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
