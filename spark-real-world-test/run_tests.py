"""Smoke-test the spark-parser against every .py file in pyspark-examples.

For each file:
  1. AST-derive an expected-counts oracle (how many spark.read, .write, .join,
     @udf, spark.sql calls — these are *minimums* the parser should hit).
  2. POST the file to the local gateway's /parse/upload endpoint.
  3. Compare parser output to the oracle.
  4. Record warnings + a verdict (PASS / WARN / FAIL).

Writes a JSON results file the report generator consumes.
"""
from __future__ import annotations

import ast
import json
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests

GATEWAY_URL = "http://localhost:8000"
UPLOAD_ENDPOINT = f"{GATEWAY_URL}/parse/upload"
REPO_DIR = Path(__file__).resolve().parent / "pyspark-examples"
OUT_FILE = Path(__file__).resolve().parent / "results.json"


@dataclass
class Oracle:
    spark_read: int = 0       # spark.read.* / spark.table — **excludes** spark.sql
    spark_sql_blocks: int = 0 # spark.sql(...) calls
    writes: int = 0           # df.write.*
    joins: int = 0            # df.join(...)
    udfs: int = 0             # @udf / @pandas_udf decorators
    has_spark_session: bool = False
    # Set when the file's only "sources" come from spark.sql clauses that may
    # reference temp views (parser correctly reports 0 source_tables for
    # in-memory-data temp views).
    only_sql_sources: bool = False


@dataclass
class FileResult:
    file: str
    status: str = "?"                       # PASS / WARN / FAIL / SKIP
    oracle: dict[str, Any] = field(default_factory=dict)
    parser: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict] = field(default_factory=list)
    error: str | None = None
    diagnoses: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Oracle — count interesting things directly from the AST
# ---------------------------------------------------------------------------

def _attr_chain(node: ast.AST) -> list[str]:
    """For ``spark.read.parquet`` returns ['spark','read','parquet']."""
    out: list[str] = []
    while isinstance(node, ast.Attribute):
        out.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        out.append(node.id)
    return list(reversed(out))


def derive_oracle(src: str) -> Oracle | None:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None

    o = Oracle()

    class V(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call):
            chain = _attr_chain(node.func)
            chain_str = ".".join(chain)
            # spark.read.format(...).load(...) / spark.read.parquet(...) / spark.read.csv(...)
            # Match anything starting with spark.read
            if len(chain) >= 2 and chain[-2:] == ["read", "load"]:
                o.spark_read += 1
            elif len(chain) >= 2 and chain[-2] == "read" and chain[-1] in {
                "parquet", "csv", "json", "orc", "text", "table", "jdbc", "format"
            } and chain[-1] != "format":
                o.spark_read += 1
            elif len(chain) >= 2 and chain[-1] == "table" and any(p in chain for p in ["spark"]):
                o.spark_read += 1
            elif "sql" in chain[-1:] and len(chain) >= 2 and chain[-2:] == ["spark", "sql"]:
                o.spark_sql_blocks += 1
                # The parser correctly reports 0 source_tables when the SQL
                # references a temp view backed by createDataFrame (in-memory).
                # We can't statically tell whether a referenced table is a
                # temp view or a real source, so we don't fold SQL FROM/JOIN
                # tables into the read count; we just flag that SQL was used
                # and relax the source_tables check accordingly.
                o.only_sql_sources = True
            # .write.* chains anywhere
            if "write" in chain and chain[-1] in {
                "saveAsTable", "save", "insertInto", "parquet", "csv", "json", "orc",
                "jdbc", "text",
            }:
                # Only count if "write" is directly two from the end (df.write.X())
                if len(chain) >= 2 and chain[-2] in {"write", "format"}:
                    o.writes += 1
            if chain[-1:] == ["join"]:
                o.joins += 1
            if chain[-1:] == ["getOrCreate"] or chain[-1:] == ["builder"]:
                o.has_spark_session = True
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef):
            for dec in node.decorator_list:
                name = ""
                if isinstance(dec, ast.Name):
                    name = dec.id
                elif isinstance(dec, ast.Attribute):
                    name = dec.attr
                elif isinstance(dec, ast.Call):
                    name = _attr_chain(dec.func)[-1] if _attr_chain(dec.func) else ""
                if name in {"udf", "pandas_udf"}:
                    o.udfs += 1
            self.generic_visit(node)

    V().visit(tree)
    return o


# ---------------------------------------------------------------------------
# Run + compare
# ---------------------------------------------------------------------------

def parse_file_via_gateway(path: Path) -> tuple[dict, list[dict], str | None]:
    try:
        with path.open("rb") as f:
            files = {"file": (path.name, f, "text/x-python")}
            data = {"source_type": "spark"}
            r = requests.post(UPLOAD_ENDPOINT, files=files, data=data, timeout=60)
    except requests.RequestException as e:
        return {}, [], f"request failed: {e}"
    if r.status_code != 200:
        return {}, [], f"HTTP {r.status_code}: {r.text[:200]}"
    body = r.json()
    stats = body.get("stats") or body.get("result", {}).get("stats") or {}
    warnings = body.get("warnings") or body.get("result", {}).get("warnings") or []
    return stats, warnings, None


def diagnose(oracle: Oracle | None, stats: dict, warnings: list[dict]) -> tuple[str, list[str]]:
    notes: list[str] = []
    if oracle is None:
        return "SKIP", ["AST itself failed to parse this file — not a parser bug"]

    syntax_warns = [w for w in warnings if w.get("type") in {"syntax_error", "parse_error"}]
    if syntax_warns:
        notes.append(f"parser emitted syntax_error: {syntax_warns[0].get('detail','')}")
        return "FAIL", notes

    if not oracle.has_spark_session and oracle.spark_read == 0 and oracle.writes == 0:
        return "SKIP", ["no PySpark calls in this file"]

    # Bucket comparisons (parser may collapse duplicates, but should never be 0
    # if the oracle saw >=1 of that thing).
    if oracle.spark_read > 0 and (stats.get("source_tables", 0) == 0):
        notes.append(
            f"oracle saw {oracle.spark_read} reads but parser reported 0 source_tables"
        )
    # If the file's only "sources" come from spark.sql clauses, 0 source_tables
    # is legitimate (temp view of an in-memory DataFrame). Don't WARN on that.
    if oracle.writes > 0 and (stats.get("target_tables", 0) == 0):
        notes.append(
            f"oracle saw {oracle.writes} writes but parser reported 0 target_tables"
        )
    if oracle.joins > 0 and (stats.get("joins", 0) == 0):
        notes.append(f"oracle saw {oracle.joins} joins but parser reported 0 joins")
    if oracle.udfs > 0 and (stats.get("udfs", 0) == 0):
        notes.append(f"oracle saw {oracle.udfs} UDF decorators but parser reported 0 udfs")
    if oracle.spark_sql_blocks > 0 and (stats.get("sql_blocks", 0) == 0):
        notes.append(
            f"oracle saw {oracle.spark_sql_blocks} spark.sql calls but parser reported 0 sql_blocks"
        )

    if notes:
        return "WARN", notes
    return "PASS", []


def main() -> int:
    files = sorted(REPO_DIR.glob("*.py"))
    print(f"discovered {len(files)} .py files")
    results: list[FileResult] = []
    summary: Counter[str] = Counter()
    t0 = time.time()

    for i, path in enumerate(files, 1):
        try:
            src = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            r = FileResult(file=path.name, status="SKIP", error="non-utf8")
            results.append(r)
            summary["SKIP"] += 1
            continue

        oracle = derive_oracle(src)
        stats, warnings, err = parse_file_via_gateway(path)
        if err:
            r = FileResult(file=path.name, status="FAIL", error=err,
                           oracle=asdict(oracle) if oracle else {})
            r.diagnoses = [err]
        else:
            status, notes = diagnose(oracle, stats, warnings)
            r = FileResult(
                file=path.name,
                status=status,
                oracle=asdict(oracle) if oracle else {},
                parser=stats,
                warnings=warnings,
                diagnoses=notes,
            )
        results.append(r)
        summary[r.status] += 1
        bar = "." if r.status == "PASS" else ("s" if r.status == "SKIP" else ("w" if r.status == "WARN" else "F"))
        print(bar, end="", flush=True)
        if i % 50 == 0:
            print(f" {i}", flush=True)

    elapsed = time.time() - t0
    print(f"\nrun complete in {elapsed:.1f}s\nsummary: {dict(summary)}")

    OUT_FILE.write_text(json.dumps({
        "summary": dict(summary),
        "total": len(files),
        "elapsed_sec": elapsed,
        "results": [asdict(r) for r in results],
    }, indent=2))
    print(f"results -> {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
