from __future__ import annotations

from pydantic import BaseModel, Field


class ParseRequest(BaseModel):
    file_path: str = Field(..., description="Absolute path inside the container's mounted volume")
    neo4j_database: str | None = None
    overwrite: bool = False


class Warning(BaseModel):
    type: str
    detail: str


class ParseResponse(BaseModel):
    workbook_id: str
    stats: dict[str, int]
    duration_ms: int
    warnings: list[Warning] = []


class HealthResponse(BaseModel):
    status: str
    neo4j: str


class VersionResponse(BaseModel):
    parser: str
    parser_version: str
    contract_version: str
    version: str          # test asserts `"version" in r.json()`
