from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "lineagepass"
    neo4j_database: str = "neo4j"

    log_level: str = "INFO"
    batch_size: int = 1000
    temp_dir: str = "/tmp/tableau-parser"
    max_file_size_mb: int = 500


settings = Settings()
