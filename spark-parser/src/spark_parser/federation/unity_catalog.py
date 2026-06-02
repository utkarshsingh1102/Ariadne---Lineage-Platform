"""Unity Catalog read-only client — v0.2 §10.

Verifies that the tables the parser discovered actually exist in Unity Catalog.
Mismatches turn into ``WarningIR(type="unity_catalog_mismatch")`` so the
downstream graph can flag stale FQNs.

This client is HTTP-only against the Databricks Unity Catalog REST API
(``GET /api/2.1/unity-catalog/tables/{full_name}``). We do not write to UC.
The HTTP transport is injected so tests don't need a live host.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from ..models.domain import SparkScriptIR, WarningIR


# Callable signature: (full_name, headers) -> (status_code, json_body | None)
HttpClient = Callable[[str, dict[str, str]], tuple[int, dict[str, Any] | None]]


class UnityCatalogClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        http: HttpClient,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = http

    def table_exists(self, full_name: str) -> bool:
        status, _ = self.http(
            f"{self.base_url}/api/2.1/unity-catalog/tables/{full_name}",
            {"Authorization": f"Bearer {self.token}"},
        )
        return status == 200

    def verify_script(self, ir: SparkScriptIR) -> list[WarningIR]:
        """Walk every table FQN on the IR. Emit one warning per missing table.

        Returns the list of warnings (also appended onto ``ir.warnings`` for
        convenience).
        """
        warnings: list[WarningIR] = []
        seen: set[str] = set()
        fqns: list[str] = []
        for df in ir.dataframes:
            for t in df.reads_from + df.writes_to:
                fqn = t.fully_qualified_name
                if not fqn or fqn in seen:
                    continue
                seen.add(fqn)
                fqns.append(fqn)
        for fqn in fqns:
            if not self.table_exists(fqn):
                w = WarningIR(
                    type="unity_catalog_mismatch",
                    detail=(
                        f"Table '{fqn}' referenced by the script but absent "
                        f"from Unity Catalog at {self.base_url}"
                    ),
                )
                warnings.append(w)
                ir.warnings.append(w)
        return warnings
