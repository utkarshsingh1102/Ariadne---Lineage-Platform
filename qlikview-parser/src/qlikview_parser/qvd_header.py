"""QVD header reader — v2 plan §5 Stage 4.

A ``.qvd`` is a QlikView optimized data file. The file starts with a
UTF-8 XML prelude (``<QvdTableHeader>...</QvdTableHeader>`` followed by
exactly one ``\\r\\n\\x00`` separator), then a binary block holding the
symbol tables and bit-packed record body. We **only** ever read the
header bytes — never the record body. The header gives us:

- ``TableName``                → :Dataset name
- ``NoOfRecords``              → cardinality hint
- ``QvdFieldHeader``+children  → :Attribute records (preserving ordinal,
                                  ``BitWidth``, ``NoOfSymbols``)
- ``Lineage/LineageInfo``      → declared upstream statements (free text
                                  the script author can rely on for joins
                                  the parser couldn't infer structurally)

Constraint hint: when a field's ``NoOfSymbols == NoOfRecords`` it carries
unique values for every row — a high-confidence ``unique`` candidate for
the constraint inference engine.

Safety: the reader caps the bytes it reads (``_MAX_HEADER_BYTES``) so a
malformed file claiming a multi-GB header can't OOM the parser. The
record body bytes are NEVER read.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from .models import Diagnostic

# Cap the prefix we'll buffer before giving up looking for the header
# terminator. Real QVD headers are 1-50 KB; this gives us 6× the worst
# case while bounding memory on adversarial / corrupt input.
_MAX_HEADER_BYTES = 1 * 1024 * 1024  # 1 MiB

# QVD's header terminator: the closing tag, a CRLF, and a single NUL byte.
_TERMINATOR = b"</QvdTableHeader>"


@dataclass(frozen=True)
class QvdField:
    name: str
    ordinal: int
    bit_width: int | None = None
    bit_offset: int | None = None
    bias: int | None = None
    no_of_symbols: int | None = None
    is_likely_unique: bool = False    # NoOfSymbols == NoOfRecords on this field


@dataclass
class QvdHeader:
    table_name: str
    no_of_records: int
    fields: list[QvdField] = field(default_factory=list)
    lineage_statements: list[str] = field(default_factory=list)
    creator_doc: str | None = None
    comment: str | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)


class QvdHeaderError(Exception):
    """Raised when the file isn't a valid QVD or the header is corrupt."""


def read_header(path: str | os.PathLike[str]) -> QvdHeader:
    """Extract the QVD header. Never touches the binary record body.

    Raises ``QvdHeaderError`` only on irrecoverable conditions — a
    missing file, no recognisable XML, or a malformed XML payload. Soft
    issues (e.g. one field missing its ``BitWidth``) get reported as
    diagnostics on the returned ``QvdHeader``.
    """
    p = Path(path)
    if not p.exists():
        raise QvdHeaderError(f"QVD file not found: {p}")

    with open(p, "rb") as f:
        chunk = f.read(_MAX_HEADER_BYTES)

    terminator_at = chunk.find(_TERMINATOR)
    if terminator_at < 0:
        raise QvdHeaderError(
            f"{p}: no QVD header terminator found in first "
            f"{_MAX_HEADER_BYTES} bytes — not a QVD?"
        )
    # End of header XML = terminator_at + len(_TERMINATOR).
    header_xml = chunk[: terminator_at + len(_TERMINATOR)]

    # Strip a leading UTF-8 BOM if present.
    if header_xml.startswith(b"\xef\xbb\xbf"):
        header_xml = header_xml[3:]

    diagnostics: list[Diagnostic] = []
    try:
        root = etree.fromstring(header_xml)
    except etree.XMLSyntaxError as e:
        raise QvdHeaderError(f"{p}: malformed QVD header XML: {e}") from e

    if root.tag != "QvdTableHeader":
        raise QvdHeaderError(
            f"{p}: root element is {root.tag!r}, not 'QvdTableHeader'"
        )

    table_name = _text(root, "TableName") or p.stem
    try:
        no_of_records = int(_text(root, "NoOfRecords") or "0")
    except ValueError:
        no_of_records = 0
        diagnostics.append(Diagnostic(
            level="warn", code="QV-QVD-BAD-COUNT",
            message="QVD header had non-integer NoOfRecords; treating as 0",
            artifact=str(p), line=None,
        ))

    fields: list[QvdField] = []
    fields_node = root.find("Fields")
    if fields_node is not None:
        for ordinal, field_node in enumerate(
            fields_node.findall("QvdFieldHeader")
        ):
            name = _text(field_node, "FieldName")
            if not name:
                diagnostics.append(Diagnostic(
                    level="warn", code="QV-QVD-FIELD-NAMELESS",
                    message=f"QvdFieldHeader at ordinal {ordinal} has no FieldName",
                    artifact=str(p), line=None,
                ))
                continue
            bit_width = _int(field_node, "BitWidth")
            bit_offset = _int(field_node, "BitOffset")
            bias = _int(field_node, "Bias")
            no_of_symbols = _int(field_node, "NoOfSymbols")
            fields.append(QvdField(
                name=name,
                ordinal=ordinal,
                bit_width=bit_width,
                bit_offset=bit_offset,
                bias=bias,
                no_of_symbols=no_of_symbols,
                # The hint the constraint engine consumes: every distinct
                # value seen → candidate UNIQUE / primary key.
                is_likely_unique=(
                    no_of_symbols is not None
                    and no_of_records > 0
                    and no_of_symbols == no_of_records
                ),
            ))

    lineage_statements: list[str] = []
    lineage_node = root.find("Lineage")
    if lineage_node is not None:
        for li in lineage_node.findall("LineageInfo"):
            stmt = _text(li, "Statement")
            if stmt:
                lineage_statements.append(stmt.strip())

    return QvdHeader(
        table_name=table_name,
        no_of_records=no_of_records,
        fields=fields,
        lineage_statements=lineage_statements,
        creator_doc=_text(root, "CreatorDoc"),
        comment=_text(root, "Comment"),
        diagnostics=diagnostics,
    )


def _text(node: etree._Element, tag: str) -> str | None:
    el = node.find(tag)
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None


def _int(node: etree._Element, tag: str) -> int | None:
    raw = _text(node, tag)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def write_synthetic_qvd(
    path: str | os.PathLike[str],
    *,
    table_name: str,
    fields: list[tuple[str, int, int]],   # (name, bit_width, no_of_symbols)
    no_of_records: int,
    lineage_statements: list[str] | None = None,
) -> None:
    """Test helper — write a synthetic .qvd whose header matches the
    shape ``read_header()`` parses. No record body is written; the file
    is just the XML header + terminator. Tests can use this without
    checking real binary QVDs into the repo."""
    from xml.sax.saxutils import escape

    field_xml = "\n".join(
        f"    <QvdFieldHeader>\n"
        f"      <FieldName>{escape(name)}</FieldName>\n"
        f"      <BitOffset>0</BitOffset>\n"
        f"      <BitWidth>{bw}</BitWidth>\n"
        f"      <Bias>0</Bias>\n"
        f"      <NoOfSymbols>{nos}</NoOfSymbols>\n"
        f"    </QvdFieldHeader>"
        for name, bw, nos in fields
    )
    lineage_xml = ""
    if lineage_statements:
        items = "\n".join(
            f"    <LineageInfo>\n"
            f"      <Discriminator>STORE</Discriminator>\n"
            f"      <Statement>{escape(s)}</Statement>\n"
            f"    </LineageInfo>"
            for s in lineage_statements
        )
        lineage_xml = f"  <Lineage>\n{items}\n  </Lineage>\n"

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<QvdTableHeader>\n'
        f"  <CreatorDoc>synthetic.qvw</CreatorDoc>\n"
        f"  <TableName>{escape(table_name)}</TableName>\n"
        f"  <NoOfRecords>{no_of_records}</NoOfRecords>\n"
        f"  <RecordByteSize>16</RecordByteSize>\n"
        f"{lineage_xml}"
        f"  <Fields>\n{field_xml}\n  </Fields>\n"
        '</QvdTableHeader>\r\n\x00'
    )
    with open(path, "wb") as f:
        f.write(xml.encode("utf-8"))
