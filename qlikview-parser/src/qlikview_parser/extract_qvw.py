"""QVW (binary OLE compound) extractor — v2 plan §5 Stage 1.

A ``.qvw`` is a Microsoft OLE compound document. Each QlikView version
lays out its streams slightly differently — the load script stream
might be ``Settings7``, ``Script``, or buried in a sub-storage. Per the
plan's risk register ("OLE container variance HIGH"), this extractor
walks ALL streams and **content-sniffs for the script signature**
(SET / LET / LOAD / SQL / DIRECTORY / CONNECT) rather than relying on
a hard-coded stream name. The best candidate (highest signature hits,
longest decoded text) wins.

Decodes UTF-16LE (with optional BOM), UTF-8 (with optional BOM), and
windows-1252 in that order. Real-world QVWs almost always store the
script as UTF-16LE with a BOM, but we don't trust that as a hard rule.

Diagnostics:

- ``QV-QVW-NOT-OLE``      — file is not a valid OLE compound document.
- ``QV-QVW-NO-SCRIPT``    — no stream decoded into recognisable QV script.
- ``QV-QVW-CORRUPT``      — olefile raised on stream read; partial recovery.
"""
from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass

from .models import Diagnostic


# Tokens whose presence in a decoded stream strongly suggests "this is a
# QlikView load script". Matched case-insensitively against word boundaries.
# Each unique hit scores 1; longer scripts win ties.
_SIGNATURE_TOKENS = (
    "LOAD",
    "SQL ",
    "SELECT ",
    "FROM ",
    "RESIDENT ",
    "STORE ",
    "INTO ",
    "CONNECT ",
    "LIB CONNECT",
    "SET ",
    "LET ",
    "SUB ",
    "END SUB",
    "DIRECTORY",
)
_SIG_RE = re.compile(r"(?i)\b(?:" + "|".join(re.escape(t) for t in _SIGNATURE_TOKENS) + r")")


@dataclass
class QvwExtraction:
    script_text: str
    source_encoding: str
    app_name: str
    chosen_stream: str
    diagnostics: list[Diagnostic]


class QvwExtractionError(Exception):
    """Raised when the file can't be opened as OLE or no script is found."""


def extract(file_path: str | os.PathLike[str]) -> QvwExtraction:
    """Extract the QlikView load script from a binary ``.qvw``.

    Returns a ``QvwExtraction`` with the best-candidate script text plus
    diagnostics collected during the walk. Raises ``QvwExtractionError``
    only on fatal conditions (not an OLE file, or zero script-shaped
    candidates anywhere in the container).
    """
    # Import lazily so a parser instance that never touches .qvw doesn't pay
    # the olefile import cost (and tests can still run without it installed
    # — they'll just skip the QVW paths).
    try:
        import olefile  # type: ignore
    except ImportError as e:  # pragma: no cover — install-time only
        raise QvwExtractionError(
            "olefile not installed — add `olefile>=0.47` to dependencies"
        ) from e

    path_str = os.fspath(file_path)
    diagnostics: list[Diagnostic] = []
    if not olefile.isOleFile(path_str):
        raise QvwExtractionError(
            f"{path_str!r} is not a valid OLE compound document (not a QVW?)"
        )

    app_name = _app_name_from_path(path_str)
    try:
        ole = olefile.OleFileIO(path_str)
    except Exception as e:
        raise QvwExtractionError(f"olefile failed to open {path_str!r}: {e}") from e

    try:
        candidates: list[tuple[int, str, str, str]] = []  # (score, stream_path, encoding, text)
        for stream_parts in ole.listdir(streams=True, storages=False):
            stream_path = "/".join(stream_parts)
            try:
                raw = ole.openstream(stream_parts).read()
            except Exception as e:
                diagnostics.append(Diagnostic(
                    level="warn", code="QV-QVW-CORRUPT",
                    message=f"Failed to read stream {stream_path!r}: {e}",
                    artifact=path_str, line=None,
                ))
                continue
            if not raw or len(raw) < 16:
                continue
            for encoding in _candidate_encodings(raw):
                try:
                    text = raw.decode(encoding, errors="strict")
                except UnicodeDecodeError:
                    continue
                # Strip an embedded BOM that some streams keep as a literal char.
                text = text.lstrip("﻿")
                score = len(_SIG_RE.findall(text))
                if score >= 3:
                    candidates.append((score, stream_path, encoding, text))
                break  # one successful decode per stream is enough

        if not candidates:
            diagnostics.append(Diagnostic(
                level="error", code="QV-QVW-NO-SCRIPT",
                message=(
                    "No stream in this QVW container decoded into a "
                    "recognisable QlikView load script (no SET/LET/LOAD/SQL"
                    " signature hits)."
                ),
                artifact=path_str, line=None,
            ))
            raise QvwExtractionError(
                f"No load-script stream found in {path_str!r}; "
                f"streams walked: {len(list(ole.listdir(streams=True)))}"
            )

        # Best candidate: highest signature score, then longest text.
        candidates.sort(key=lambda c: (c[0], len(c[3])), reverse=True)
        score, stream_path, encoding, text = candidates[0]

        return QvwExtraction(
            script_text=text,
            source_encoding=encoding,
            app_name=app_name,
            chosen_stream=stream_path,
            diagnostics=diagnostics,
        )
    finally:
        ole.close()


# Candidate encodings to try per stream. UTF-16LE first because real QVWs
# almost always use it; UTF-8 second; windows-1252 as a fallback for
# ancient ANSI exports.
def _candidate_encodings(raw: bytes) -> tuple[str, ...]:
    # If a UTF-16LE BOM is at the head, that's a near-certainty.
    if raw[:2] == b"\xff\xfe":
        return ("utf-16-le", "utf-8", "windows-1252")
    if raw[:3] == b"\xef\xbb\xbf":
        return ("utf-8-sig", "utf-16-le", "windows-1252")
    # Heuristic: if every other byte is 0x00 (typical UTF-16LE ASCII), try LE first.
    head = raw[:256]
    if len(head) >= 2 and sum(1 for i in range(1, len(head), 2) if head[i] == 0) > len(head) // 4:
        return ("utf-16-le", "utf-8", "windows-1252")
    return ("utf-8", "utf-16-le", "windows-1252")


def _app_name_from_path(path: str) -> str:
    base = os.path.basename(path)
    name, _ = os.path.splitext(base)
    return name or "<unnamed>"


def extract_to_text(file_path: str | os.PathLike[str]) -> tuple[str, str, str]:
    """Convenience shim returning ``(script_text, encoding, app_name)``.

    Used by the orchestrator when it only needs the text to feed into the
    existing preprocessor pipeline and doesn't care about diagnostics.
    """
    r = extract(file_path)
    return r.script_text, r.source_encoding, r.app_name


def write_synthetic_qvw(
    path: str | os.PathLike[str],
    script_text: str,
    *,
    stream_name: str = "Settings7",
) -> None:
    """Test helper — write a minimal but valid OLE compound document
    containing one stream (default ``Settings7``) holding ``script_text``
    encoded as UTF-16LE with a BOM.

    olefile is read-only, so we build the bytes by hand per MS-CFB §2.
    Single-sector script content only (script_text ≤ ~250 UTF-16 chars
    minus BOM). Generates a 4-sector file: header / FAT / dir / stream.

    Used by the integration tests so we never need to check real binary
    QVWs into the repo.
    """
    import struct

    SECTOR = 512
    FREESECT = 0xFFFFFFFF
    ENDOFCHAIN = 0xFFFFFFFE
    FATSECT = 0xFFFFFFFD
    # olefile forces the mini-stream cutoff back to 0x1000 (4096) if we
    # write any other value (see olefile.py line 1374). So small streams
    # would route through the mini-FAT, which this minimal writer doesn't
    # build. Workaround: pad the stream so its declared size is ≥ 4096,
    # forcing the regular-FAT path. The padding is UTF-16LE whitespace,
    # which the script tokenizer ignores.
    MIN_STREAM_SIZE = 0x1000

    # Encode script as UTF-16LE with BOM (matches real QVWs in the wild).
    script_bytes_raw = b"\xff\xfe" + script_text.encode("utf-16-le")
    if len(script_bytes_raw) < MIN_STREAM_SIZE:
        # Pad with UTF-16LE space characters (0x20 0x00) to reach the
        # threshold. Each space is 2 bytes; pad in whole-pair increments.
        deficit = MIN_STREAM_SIZE - len(script_bytes_raw)
        if deficit % 2:
            deficit += 1
        padding = b"\x20\x00" * (deficit // 2)
        script_bytes = script_bytes_raw + padding
    else:
        script_bytes = script_bytes_raw

    # Number of sectors the stream needs.
    stream_sector_count = (len(script_bytes) + SECTOR - 1) // SECTOR
    # Stream lives at sectors [2, 2 + stream_sector_count).
    stream_first_sector = 2
    stream_last_sector = stream_first_sector + stream_sector_count - 1

    # --- Header (sector −1, offset 0) ---------------------------------------
    header = bytearray(SECTOR)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"     # magic
    struct.pack_into("<H", header, 0x18, 0x003E)          # minor version
    struct.pack_into("<H", header, 0x1A, 0x0003)          # major version (512-byte sectors)
    struct.pack_into("<H", header, 0x1C, 0xFFFE)          # byte order = little-endian
    struct.pack_into("<H", header, 0x1E, 0x0009)          # sector shift (2^9 = 512)
    struct.pack_into("<H", header, 0x20, 0x0006)          # mini-sector shift (2^6 = 64)
    struct.pack_into("<I", header, 0x28, 0)               # number of dir sectors (0 for v3)
    struct.pack_into("<I", header, 0x2C, 1)               # number of FAT sectors
    struct.pack_into("<I", header, 0x30, 1)               # first directory sector
    struct.pack_into("<I", header, 0x34, 0)               # transaction signature
    struct.pack_into("<I", header, 0x38, 0x00001000)      # mini-stream cutoff (mandatory)
    struct.pack_into("<I", header, 0x3C, ENDOFCHAIN)      # first mini-FAT sector
    struct.pack_into("<I", header, 0x40, 0)               # number of mini-FAT sectors
    struct.pack_into("<I", header, 0x44, ENDOFCHAIN)      # first DIFAT sector
    struct.pack_into("<I", header, 0x48, 0)               # number of DIFAT sectors
    # DIFAT — first entry = sector 0 (the FAT); rest = FREESECT.
    struct.pack_into("<I", header, 0x4C, 0)
    for i in range(1, 109):
        struct.pack_into("<I", header, 0x4C + i * 4, FREESECT)

    # --- Sector 0: FAT ------------------------------------------------------
    fat = bytearray(SECTOR)
    struct.pack_into("<I", fat, 0, FATSECT)              # sector 0 IS the FAT
    struct.pack_into("<I", fat, 4, ENDOFCHAIN)           # sector 1 (dir) — one sector
    # Stream chain: sectors stream_first_sector .. stream_last_sector
    for s in range(stream_first_sector, stream_last_sector):
        struct.pack_into("<I", fat, s * 4, s + 1)
    struct.pack_into("<I", fat, stream_last_sector * 4, ENDOFCHAIN)
    # Mark all higher entries free.
    for i in range(stream_last_sector + 1, 128):
        struct.pack_into("<I", fat, i * 4, FREESECT)

    # --- Sector 1: Directory (4 entries × 128 bytes) -----------------------
    dir_sector = bytearray(SECTOR)

    def _dir_entry(buf: bytearray, offset: int, name: str, obj_type: int,
                   start_sector: int, stream_size: int) -> None:
        name_utf16 = (name + "\0").encode("utf-16-le")
        buf[offset:offset + len(name_utf16)] = name_utf16
        struct.pack_into("<H", buf, offset + 0x40, len(name_utf16))
        buf[offset + 0x42] = obj_type
        buf[offset + 0x43] = 1
        struct.pack_into("<I", buf, offset + 0x44, FREESECT)
        struct.pack_into("<I", buf, offset + 0x48, FREESECT)
        struct.pack_into("<I", buf, offset + 0x4C, FREESECT)
        struct.pack_into("<I", buf, offset + 0x74, start_sector)
        struct.pack_into("<Q", buf, offset + 0x78, stream_size)

    _dir_entry(dir_sector, 0 * 128, "Root Entry", obj_type=5,
               start_sector=ENDOFCHAIN, stream_size=0)
    # Root child sid = 1 so olefile discovers the stream
    struct.pack_into("<I", dir_sector, 0 * 128 + 0x4C, 1)

    _dir_entry(dir_sector, 1 * 128, stream_name, obj_type=2,
               start_sector=stream_first_sector, stream_size=len(script_bytes))

    # --- Stream sectors -----------------------------------------------------
    # Pad to whole-sector boundary so each sector slot in the FAT chain
    # has exactly SECTOR bytes backing it on disk.
    padded = script_bytes + b"\x00" * (
        stream_sector_count * SECTOR - len(script_bytes)
    )

    # --- Write -------------------------------------------------------------
    with open(path, "wb") as f:
        f.write(bytes(header))
        f.write(bytes(fat))
        f.write(bytes(dir_sector))
        f.write(padded)
