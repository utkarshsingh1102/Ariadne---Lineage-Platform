"""Sniff whether an input file is XML export or composer-text."""

from __future__ import annotations

from pathlib import Path


class FormatDetectionError(ValueError):
    """Raised when we can't confidently classify the file."""


# Top-level keywords that can appear as the FIRST meaningful line of a
# composer-format file. SCHEDULE alone isn't enough — real composer
# exports routinely lead with workstation, calendar, resource, prompt,
# or event-rule definitions and only declare schedules later. Matched
# case-insensitively against the first non-comment, non-blank token.
#
# Includes both the "composer modify" object-keyword style (CALENDAR foo …)
# and the "composer add" dollar-section style ($CALENDAR / $PARM / $JOBS
# / …) used by real-world batch definition files.
_COMPOSER_TOP_LEVEL_KEYWORDS = frozenset({
    "SCHEDULE",         # job stream
    "CPUNAME",          # workstation
    "WKSTATION",        # alt workstation
    "DOMAIN",
    "CALENDAR",
    "RESOURCE",
    "PROMPT",
    "PARMS", "PARAMETERS",
    "USEROBJ",
    "EVENTRULE",
    "FOLDER",
    "JOBSTREAM",        # rare older syntax
    # composer-add dollar sections
    "$CALENDAR", "$CAL",
    "$CPU", "$WORKSTATION",
    "$RESOURCE",
    "$PROMPT",
    "$PARM", "$PARMS", "$PARAMETERS",
    "$JOBS", "$JOB",
    "$SCHEDULE", "$SCHED",
    "$USEROBJ",
    "$EVENTRULE",
    "$FOLDER",
})

# Read enough to clear a sizable comment-header block. Composer files in
# the wild start with multi-line ``#-----`` banners; 512 bytes is too
# small to reach the first real keyword once a documentation header is
# in front. 4 KiB lands the keyword in every fixture we've seen.
_PEEK_BYTES = 4096


def detect_format(path: str | Path) -> str:
    """Returns one of: `xml`, `composer`. Raises `FormatDetectionError` otherwise."""
    p = Path(path)
    if not p.exists():
        raise FormatDetectionError(f"File not found: {p}")
    suffix = p.suffix.lower()
    if suffix == ".xml":
        return "xml"
    # Always content-sniff — the extension is only a hint, never an answer.
    return _peek(p)


def _peek(path: Path) -> str:
    with open(path, "rb") as f:
        head = f.read(_PEEK_BYTES)
    text = head.decode("utf-8", errors="ignore").lstrip()
    if not text.strip():
        raise FormatDetectionError(f"Empty/blank file: {path}")
    if text.startswith("<?xml") or text.startswith("<scheduleDefinitions"):
        return "xml"
    # Composer text begins with comments, blank lines, or a top-level
    # keyword. Examine the first non-comment, non-blank line.
    #   ``#``  Python/shell style (our own fixtures)
    #   ``/*`` C block style
    #   ``//`` C++ line style
    #   ``*``  TWS native asterisk-comment (real-world IBM TWS dumps)
    #          — only when followed by space or '-' / '=' / '*', so a
    #          bare ``*'' on a line counts but a line starting with
    #          ``*FOO`` (no separator) doesn't get mis-classified.
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("/*") or s.startswith("//"):
            continue
        if s == "*" or s.startswith(("* ", "*-", "*=", "**", "*\t")):
            continue
        first_token = s.split(None, 1)[0].upper() if s else ""
        if first_token in _COMPOSER_TOP_LEVEL_KEYWORDS:
            return "composer"
        # First meaningful line wasn't a known composer keyword — bail
        # rather than guess so the user gets a clear error.
        break
    raise FormatDetectionError(f"Could not classify {path}")
