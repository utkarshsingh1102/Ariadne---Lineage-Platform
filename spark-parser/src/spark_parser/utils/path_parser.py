"""URI / path parsing for Spark Table location IDs.

Handles ``s3://``, ``abfss://``, ``gs://``, ``file://``, and ``jdbc:`` schemes.
Trailing slashes and query strings are stripped so paths with cosmetic
differences hash to the same canonical ID.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PathInfo:
    scheme: str
    bucket: str
    path: str
    raw: str


def parse_path(uri: str) -> PathInfo:
    """Split ``uri`` into scheme / bucket / path components."""
    raw = uri
    # JDBC URLs:  jdbc:postgresql://host:5432/db?table=t
    if uri.lower().startswith("jdbc:"):
        return PathInfo(scheme="jdbc", bucket="", path=uri[5:].split("?", 1)[0], raw=raw)

    if "://" not in uri:
        return PathInfo(scheme="", bucket="", path=uri, raw=raw)

    scheme, rest = uri.split("://", 1)
    rest = rest.split("?", 1)[0]      # drop query string
    parts = rest.split("/", 1)
    bucket = parts[0]
    path = parts[1] if len(parts) > 1 else ""
    path = path.rstrip("/")
    return PathInfo(scheme=scheme.lower(), bucket=bucket, path=path, raw=raw)


def canonical_path_id(uri: str) -> str:
    """Stable canonical string for a path-based ``:Table`` id (plan §5.4)."""
    info = parse_path(uri)
    if info.scheme == "jdbc":
        return f"table::jdbc:{info.path}".lower()
    if info.scheme:
        body = f"{info.scheme}://{info.bucket}"
        if info.path:
            body += f"/{info.path}"
        return f"table::{body}".lower()
    return f"table::{info.path}".lower()
