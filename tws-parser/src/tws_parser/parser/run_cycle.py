"""Normalize TWS run-cycle expressions into a `RunCycle` IR."""

from __future__ import annotations

import re

from tws_parser.models.domain import RunCycle


_DAYS_FULL_TO_ABBR = {
    "MONDAY": "MON", "TUESDAY": "TUE", "WEDNESDAY": "WED",
    "THURSDAY": "THU", "FRIDAY": "FRI", "SATURDAY": "SAT", "SUNDAY": "SUN",
    "MON": "MON", "TUE": "TUE", "WED": "WED", "THU": "THU",
    "FRI": "FRI", "SAT": "SAT", "SUN": "SUN",
}


def normalise(name: str, start_time: str | None = None) -> RunCycle:
    """Best-effort normalisation. `cron_equivalent` is `None` when ambiguous.

    `start_time` is a `"HH:MM"` string (matches the format used in `ScheduleIR`).
    """
    out = RunCycle(raw=name or "")
    if not name:
        return out

    key = name.upper().strip()
    minute, hour = _split_time(start_time)

    # ---- handled cases -----------------------------------------------------

    if key in {"EVERYDAY", "DAILY", "EVERY_DAY"}:
        out.frequency = "daily"
        if minute is not None:
            out.cron_equivalent = f"{minute} {hour} * * *"
        return out

    if key in {"EVERY_WEEKDAY", "WEEKDAY", "WEEKDAYS"}:
        out.frequency = "weekly"
        out.days_of_week = ["MON", "TUE", "WED", "THU", "FRI"]
        if minute is not None:
            out.cron_equivalent = f"{minute} {hour} * * 1-5"
        return out

    if key in {"WEEKEND", "WEEKENDS"}:
        out.frequency = "weekly"
        out.days_of_week = ["SAT", "SUN"]
        if minute is not None:
            out.cron_equivalent = f"{minute} {hour} * * 0,6"
        return out

    if "DAILY" in key and "HOUR" in key:
        # `DAILY EVERY HOUR`
        out.frequency = "daily"
        m_str = minute if minute is not None else "0"
        out.cron_equivalent = f"{m_str} * * * *"
        return out

    monthly_match = re.search(r"MONTHLY\s+ON\s+(\d+)", key)
    if monthly_match:
        out.frequency = "monthly"
        day = int(monthly_match.group(1))
        out.days_of_month = [day]
        if minute is not None:
            out.cron_equivalent = f"{minute} {hour} {day} * *"
        return out

    if key.startswith("MONTHLY"):
        # `MONTHLY ON LAST WORKDAY`, `MONTHLY` etc. — frequency known, cron unsafe.
        out.frequency = "monthly"
        return out

    # Comma- or underscore-separated day list (`MON_WED_FRI`)
    if any(sep in key for sep in (",", "_")):
        candidates = [p.strip() for p in re.split(r"[_,]", key) if p.strip()]
        days = [_DAYS_FULL_TO_ABBR[c] for c in candidates if c in _DAYS_FULL_TO_ABBR]
        if days and len(days) == len(candidates):
            out.frequency = "weekly"
            out.days_of_week = days
            if minute is not None:
                cron_dows = ",".join(_cron_dow(d) for d in days)
                out.cron_equivalent = f"{minute} {hour} * * {cron_dows}"
            return out

    # ---- best-effort fallbacks --------------------------------------------

    if "WEEKDAY" in key:
        out.frequency = "weekly"
        out.days_of_week = ["MON", "TUE", "WED", "THU", "FRI"]
        # Don't set cron — there's an "EXCEPT HOLIDAYS" qualifier we can't model
        return out

    out.frequency = "unknown"
    return out


def _split_time(hhmm_or_colon: str | None) -> tuple[str | None, str | None]:
    """Accept both `0530` and `05:30`. Returns (`minute`, `hour`) as strings."""
    if not hhmm_or_colon:
        return (None, None)
    s = hhmm_or_colon.strip()
    if ":" in s:
        try:
            hh, mm = s.split(":")
            return (str(int(mm)), str(int(hh)))
        except ValueError:
            return (None, None)
    if len(s) == 4 and s.isdigit():
        return (str(int(s[2:])), str(int(s[:2])))
    return (None, None)


def _cron_dow(day: str) -> str:
    return {"SUN": "0", "MON": "1", "TUE": "2", "WED": "3", "THU": "4",
            "FRI": "5", "SAT": "6"}[day]
