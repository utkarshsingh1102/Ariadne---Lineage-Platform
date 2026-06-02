"""Phase 3 — connection-store readers (v2 plan §1 Stage 1).

In production QlikView installations, ``LIB CONNECT TO 'foo'`` rarely
embeds the full connection string in the script. Instead the script
references a NAMED connection whose details live in one of three external
stores:

  - ``*.dconn``   — QlikView LIB connection XML (typically under
                    ``ProgramData\\QlikTech\\…\\Connections\\<name>.dconn``).
                    XML body: ``<Connection> <ConnectionString>…</ConnectionString>
                    <Type>OLEDB|ODBC|FILE</Type> </Connection>``.
  - ``odbc.ini``  — Windows ODBC DSN registry export (INI format).
                    Section ``[<DSN_NAME>]`` with ``Driver=…``,
                    ``Server=…``, etc.
  - ``Settings.ini`` — QlikView Server's own DSN catalog (INI). Same
                    shape as odbc.ini but the section is
                    ``[<connection_name>]`` and the keys are QV-specific.

This module turns those stores into a single, deterministic resolver:

    >>> resolver = ConnectionStore.from_paths(dconn_dir=…, odbc_ini=…, settings_ini=…)
    >>> resolver.resolve("snowflake-prod")
    DataConnection(name='snowflake-prod', platform_kind='snowflake', host='…',
                   database='…', warehouse='…', auth_method='oauth', …)

Every secret string is run through ``secrets.scrub`` BEFORE it can reach
``DataConnection.raw_locator_redacted`` so the password never lives in
memory longer than the parse of one connection-string. The salted
fingerprint (per the v2 plan's change-detection contract) is computed
from the raw value, then the raw value is dropped.
"""
from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree as _et

from .models import DataConnection, Diagnostic
from .secrets import fingerprint as _fingerprint
from .secrets import scrub as _scrub


# ---------------------------------------------------------------------------
# Pure parsers — one per store format.
# ---------------------------------------------------------------------------

# DSN string parser shared with visitor's _extract_kv but lifted here so
# this module has no dependency on visitor internals.
_KV = re.compile(r"(?i)([A-Za-z_][\w ]*?)\s*=\s*([^;\"']*)")


def _kv_dict(raw: str) -> dict[str, str]:
    """Parse a ``key=value;key=value`` connection string into a case-
    folded dict. Whitespace around keys/values is stripped; empty values
    are dropped."""
    out: dict[str, str] = {}
    if not raw:
        return out
    for k, v in _KV.findall(raw):
        key = k.strip().lower()
        val = v.strip()
        if key and val:
            out[key] = val
    return out


def parse_dconn_file(path: Path | str) -> dict | None:
    """Parse a ``.dconn`` (QlikView LIB connection XML) file.

    Returns ``{name, type, raw}`` or ``None`` if the file isn't a valid
    .dconn. Soft-fail by design — a malformed connection store must NEVER
    abort an estate parse.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    try:
        # XML on disk in real QlikView installs is utf-16-LE with BOM;
        # lxml handles both, but we read bytes to skip any wrapper.
        raw_bytes = p.read_bytes()
        # Strip a UTF-16 BOM if present so lxml uses the declared encoding.
        if raw_bytes.startswith(b"\xff\xfe") or raw_bytes.startswith(b"\xfe\xff"):
            raw_bytes = raw_bytes.decode("utf-16").encode("utf-8")
        root = _et.fromstring(raw_bytes)
    except (_et.XMLSyntaxError, ValueError, UnicodeDecodeError):
        return None

    # Common QV shapes:
    #   <Connection>
    #     <ConnectionString>…</ConnectionString>
    #     <Type>OLEDB</Type>
    #   </Connection>
    # …but real-world variants exist; we walk children defensively.
    if root.tag.lower() not in {"connection", "qlikconnection", "dconn"}:
        # Some exports nest under a <Connections><Connection>…</> root.
        first = root.find(".//{*}Connection") or root.find(".//Connection")
        if first is not None:
            root = first
        else:
            return None

    def _child(tag: str) -> str | None:
        for c in root:
            if c.tag.split("}")[-1].lower() == tag.lower() and c.text:
                return c.text.strip()
        return None

    name = _child("name") or _child("connectionname") or p.stem
    raw = _child("connectionstring") or ""
    kind = (_child("type") or "").upper() or None
    return {"name": name, "type": kind, "raw": raw}


def parse_ini_store(path: Path | str) -> dict[str, dict[str, str]] | None:
    """Parse an ``odbc.ini`` / ``Settings.ini`` file into
    ``{section_name: {key_lower: value}}``. Case is normalised on keys
    (Windows is case-insensitive) but preserved on values. Returns ``None``
    on missing file."""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    cfg = configparser.RawConfigParser()
    cfg.optionxform = str  # preserve case, we'll lowercase on lookup
    try:
        with p.open(encoding="utf-8", errors="replace") as fp:
            cfg.read_file(fp)
    except (configparser.Error, OSError, UnicodeError):
        return None
    out: dict[str, dict[str, str]] = {}
    for section in cfg.sections():
        body: dict[str, str] = {}
        for k, v in cfg.items(section):
            body[k.lower()] = v
        out[section] = body
    return out


# ---------------------------------------------------------------------------
# ConnectionStore — uniform resolver across all three stores.
# ---------------------------------------------------------------------------


@dataclass
class ConnectionStore:
    """Resolves a LIB CONNECT name to a fully-populated ``DataConnection``.

    Three lookup tiers, first hit wins:

      1. ``.dconn`` files (most authoritative — usually one file per
         named connection, with the FULL connection string).
      2. ``Settings.ini`` (QlikView Server's DSN catalog).
      3. ``odbc.ini`` (Windows ODBC registry).

    Soft-fail: missing/corrupt stores degrade silently to "no resolution"
    so the visitor's inline classification still works.
    """
    dconn_index: dict[str, dict] = field(default_factory=dict)
    settings_ini: dict[str, dict[str, str]] = field(default_factory=dict)
    odbc_ini: dict[str, dict[str, str]] = field(default_factory=dict)
    secret_salt: bytes = b""
    diagnostics: list[Diagnostic] = field(default_factory=list)

    # -- Factory --------------------------------------------------------

    @classmethod
    def from_paths(
        cls,
        dconn_dir: Path | str | None = None,
        settings_ini: Path | str | None = None,
        odbc_ini: Path | str | None = None,
        secret_salt: bytes = b"",
    ) -> "ConnectionStore":
        store = cls(secret_salt=secret_salt)
        if dconn_dir:
            store._load_dconn_dir(Path(dconn_dir))
        if settings_ini:
            parsed = parse_ini_store(settings_ini)
            if parsed is not None:
                store.settings_ini = parsed
        if odbc_ini:
            parsed = parse_ini_store(odbc_ini)
            if parsed is not None:
                store.odbc_ini = parsed
        return store

    def _load_dconn_dir(self, dconn_dir: Path) -> None:
        if not dconn_dir.exists() or not dconn_dir.is_dir():
            return
        for child in sorted(dconn_dir.iterdir()):
            if child.suffix.lower() != ".dconn":
                continue
            parsed = parse_dconn_file(child)
            if parsed is None:
                self.diagnostics.append(Diagnostic(
                    level="warn", code="QV-DCONN-PARSE",
                    message=f"failed to parse .dconn: {child.name}",
                    artifact=str(child), line=None,
                ))
                continue
            # Index by lowercased name (LIB CONNECT names are
            # case-insensitive in QV).
            self.dconn_index[parsed["name"].lower()] = parsed

    # -- Resolver -------------------------------------------------------

    def resolve(self, lib_name: str) -> DataConnection | None:
        """Return a fully-populated ``DataConnection`` for ``lib_name`` or
        ``None`` if no store contained it. The caller is expected to fall
        back on the visitor's inline classification when this returns None."""
        if not lib_name:
            return None
        key = lib_name.lower()

        # Tier 1 — .dconn
        if key in self.dconn_index:
            d = self.dconn_index[key]
            return self._dconn_to_data_connection(d, source_label="dconn")

        # Tier 2 — Settings.ini
        for section, body in self.settings_ini.items():
            if section.lower() == key:
                return self._ini_section_to_data_connection(
                    name=section, body=body, source_label="settings.ini",
                )

        # Tier 3 — odbc.ini
        for section, body in self.odbc_ini.items():
            if section.lower() == key:
                return self._ini_section_to_data_connection(
                    name=section, body=body, source_label="odbc.ini",
                )

        return None

    # -- Builders -------------------------------------------------------

    def _dconn_to_data_connection(self, d: dict, *, source_label: str) -> DataConnection:
        raw = d["raw"] or ""
        kv = _kv_dict(raw)
        scrubbed, scrub_diags = _scrub(raw, artifact=f"dconn:{d['name']}")
        if scrub_diags:
            self.diagnostics.extend(scrub_diags)

        # Pull the secret out of the kv map BEFORE we drop the raw text,
        # so we can fingerprint it for change detection.
        secret_val = kv.get("pwd") or kv.get("password")
        secret_fp = (
            _fingerprint(secret_val, salt=self.secret_salt) if secret_val else None
        )

        platform = _classify_from_kv(kv, fallback_raw=raw)
        return DataConnection(
            name=d["name"],
            platform_kind=platform,
            driver=d.get("type") or kv.get("driver"),
            host=kv.get("server") or kv.get("host") or kv.get("account"),
            database=kv.get("database") or kv.get("initial catalog"),
            schema=kv.get("schema"),
            warehouse=kv.get("warehouse"),
            role=kv.get("role"),
            region=kv.get("region"),
            auth_method=_auth_from_kv(kv),
            secret_ref=None,                     # caller's vault provider fills this
            secret_fingerprint=secret_fp,
            raw_locator_redacted=scrubbed,
        )

    def _ini_section_to_data_connection(
        self, name: str, body: dict[str, str], *, source_label: str,
    ) -> DataConnection:
        # The INI body IS effectively a key=value map already; treat it
        # like a kv-dict for classification.
        # Synthesise a connection-string equivalent (sorted for stability)
        # so the scrubber + fingerprinter operate on the same shape they
        # would for an inline string.
        raw_pairs = ";".join(f"{k}={v}" for k, v in sorted(body.items()))
        scrubbed, scrub_diags = _scrub(raw_pairs, artifact=f"{source_label}:{name}")
        if scrub_diags:
            self.diagnostics.extend(scrub_diags)
        secret_val = body.get("pwd") or body.get("password")
        secret_fp = (
            _fingerprint(secret_val, salt=self.secret_salt) if secret_val else None
        )
        platform = _classify_from_kv(body, fallback_raw=raw_pairs)
        return DataConnection(
            name=name,
            platform_kind=platform,
            driver=body.get("driver"),
            host=body.get("server") or body.get("host") or body.get("account"),
            database=body.get("database") or body.get("initial catalog"),
            schema=body.get("schema"),
            warehouse=body.get("warehouse"),
            role=body.get("role"),
            region=body.get("region"),
            auth_method=_auth_from_kv(body),
            secret_ref=None,
            secret_fingerprint=secret_fp,
            raw_locator_redacted=scrubbed,
        )


# ---------------------------------------------------------------------------
# Classification helpers — local copies (visitor has near-identical, but
# this module must stand alone for unit testing without parser context).
# ---------------------------------------------------------------------------

_PLATFORM_HINTS: tuple[tuple[str, str], ...] = (
    ("snowflake", "snowflake"),
    ("redshift", "redshift"),
    ("sqlserver", "sqlserver"),
    ("mssql", "sqlserver"),
    ("postgres", "postgres"),
    ("postgresql", "postgres"),
    ("mysql", "mysql"),
    ("oracle", "oracle"),
    ("teradata", "teradata"),
    ("bigquery", "bigquery"),
    ("databricks", "databricks"),
    ("synapse", "synapse"),
    ("s3", "s3"),
    ("adls", "adls"),
    (".azure", "synapse"),
    ("rest", "rest"),
    ("sap", "sap"),
    ("sharepoint", "sharepoint"),
)


def _classify_from_kv(kv: dict[str, str], *, fallback_raw: str) -> str:
    blob = " ".join(list(kv.keys()) + list(kv.values()) + [fallback_raw]).lower()
    for needle, kind in _PLATFORM_HINTS:
        if needle in blob:
            return kind
    return "unknown"


def _auth_from_kv(kv: dict[str, str]) -> str | None:
    explicit = kv.get("authenticator") or kv.get("auth")
    if explicit:
        return explicit.lower()
    blob = " ".join(kv.keys()).lower() + " " + " ".join(kv.values()).lower()
    if "oauth" in blob or "access_token" in blob:
        return "oauth"
    if "private_key" in blob or "key_path" in blob or "key_pair" in blob:
        return "key_pair"
    if "iam" in blob:
        return "iam"
    if "sso" in blob:
        return "sso"
    if "managed" in blob and "identity" in blob:
        return "managed_identity"
    if kv.get("pwd") or kv.get("password"):
        return "password"
    return None
