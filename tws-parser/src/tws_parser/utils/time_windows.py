"""Parse `0530` / `09:00` style time literals into `datetime.time` instances."""

from __future__ import annotations

import re
from datetime import date, time

_TIME_HHMM_RE = re.compile(r"^(\d{2})(\d{2})$")
_TIME_COLON_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_DATE_MMDDYYYY_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_DATE_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse_time(raw: str | None) -> time | None:
    if raw is None:
        return None
    s = raw.strip().strip('"')
    if not s:
        return None
    m = _TIME_HHMM_RE.match(s)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh < 24 and 0 <= mm < 60:
            return time(hh, mm)
    m = _TIME_COLON_RE.match(s)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh < 24 and 0 <= mm < 60:
            return time(hh, mm)
    return None


def parse_date(raw: str | None) -> date | None:
    if raw is None:
        return None
    s = raw.strip().strip('"')
    if not s:
        return None
    m = _DATE_MMDDYYYY_RE.match(s)
    if m:
        mm, dd, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(yyyy, mm, dd)
        except ValueError:
            return None
    m = _DATE_ISO_RE.match(s)
    if m:
        yyyy, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(yyyy, mm, dd)
        except ValueError:
            return None
    return None
