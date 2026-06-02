from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ParseRequest(BaseModel):
    # Harmonise with the rest of the parser fleet — every other parser
    # (tableau, qlikview, spark) names this field ``file_path``. The
    # gateway's parse-proxy forwards the request body verbatim, so when
    # the frontend POSTs ``{file_path, overwrite}`` the old schema 422'd
    # on a missing ``input_path``. ``AliasChoices`` lets the API accept
    # either name; ``populate_by_name=True`` allows code to construct
    # the model with the canonical attribute (``file_path=...``) too.
    model_config = ConfigDict(populate_by_name=True)

    file_path: str = Field(
        ...,
        validation_alias=AliasChoices("file_path", "input_path"),
        description="Absolute path to composer-text or XML export",
    )
    format: str = "auto"
    neo4j_database: str | None = None
    write_neo4j: bool = True
    write_postgres: bool = True
    overwrite: bool = False
    # Phase 1: fail-closed mode. When true, any collected lexer/parser error
    # returns HTTP 422 instead of partial IR with warnings. Cross-file
    # unresolved dependencies are NOT parse errors and do not trip strict.
    strict: bool = False


class Warning(BaseModel):
    type: str
    detail: str
    line: int | None = None
    column: int | None = None


class ParseResponse(BaseModel):
    # Phase 1: explicit tri-state. The API must NEVER return ``ok`` with a
    # non-empty parse-error collector — that was the silent-zero hazard.
    #   ok      — zero parse errors (the IR may legitimately be empty)
    #   partial — parse errors present, but some IR was produced
    #   failed  — parse errors present and zero IR
    status: Literal["ok", "partial", "failed"] = "ok"
    parsed_schedules: int
    parsed_jobs: int
    stats: dict[str, int]
    duration_ms: int
    warnings: list[Warning] = []
    # Every Schedule id this parse wrote. One TWS composer file routinely
    # declares multiple SCHEDULE blocks; the gateway's project-grouping
    # flow attaches all of them so a project carrying that file lists each
    # schedule individually (matches the Files page's per-schedule rows).
    parsed_node_ids: list[str] = []


class HealthResponse(BaseModel):
    status: str
    neo4j: str
    postgres: str


class VersionResponse(BaseModel):
    parser: str
    parser_version: str
    contract_version: str
    version: str


class ExcelFilter(BaseModel):
    schedule_id: str | None = None
    workstation: str | None = None
    start_time_min: str | None = None
    start_time_max: str | None = None
    script_path_like: str | None = None


class ExcelExportRequest(BaseModel):
    filter: ExcelFilter = Field(default_factory=ExcelFilter)


# ---------------------------------------------------------------------------
# Multi-file parse — open N files at once, surface what's shared between them
# ---------------------------------------------------------------------------


class MultiParseRequest(BaseModel):
    file_paths: list[str] = Field(
        ..., min_length=2, max_length=20,
        description="Absolute paths to the composer-text or XML files to "
                    "parse together. Min 2, max 20 per batch.",
    )
    write_neo4j: bool = True
    write_postgres: bool = False
    overwrite: bool = False
    strict: bool = False
    neo4j_database: str | None = None


class SharedEntityPayload(BaseModel):
    id: str
    name: str
    label: str
    source_files: list[str]


class CrossFileFollowsPayload(BaseModel):
    from_file: str
    from_job_qualified: str
    to_file: str
    to_job_qualified: str
    condition: str | None = None


class CommonalityReportPayload(BaseModel):
    shared_entities: dict[str, list[SharedEntityPayload]] = Field(default_factory=dict)
    file_specific: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    cross_file_follows: list[CrossFileFollowsPayload] = Field(default_factory=list)


class PerFileResult(BaseModel):
    file_path: str
    status: Literal["ok", "partial", "failed"]
    parsed_schedules: int
    parsed_jobs: int
    parse_errors: int
    warnings: list[Warning] = Field(default_factory=list)
    # Schedule ids this file contributed to the merged unit — sourced from
    # the provenance map. The gateway uses this to attach each file's
    # schedule ids to a project when /upload/auto is grouping into one.
    parsed_node_ids: list[str] = Field(default_factory=list)


class MultiParseResponse(BaseModel):
    status: Literal["ok", "partial", "failed"]
    files: list[PerFileResult]
    merged_stats: dict[str, int]
    commonality: CommonalityReportPayload
    duration_ms: int
    warnings: list[Warning] = Field(default_factory=list)
