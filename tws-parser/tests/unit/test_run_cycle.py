"""
Run-cycle normalisation (plan §6 step 5 + §10.4).
"""
import pytest


def test_every_weekday_with_start_time():
    from tws_parser.parser.run_cycle import normalise
    nc = normalise("EVERY_WEEKDAY", start_time="05:30")
    assert nc.frequency == "weekly"
    assert set(nc.days_of_week) == {"MON", "TUE", "WED", "THU", "FRI"}
    assert nc.cron_equivalent == "30 5 * * 1-5"


def test_monthly_on_1st():
    from tws_parser.parser.run_cycle import normalise
    nc = normalise("MONTHLY ON 1ST", start_time="05:00")
    assert nc.frequency == "monthly"
    assert 1 in nc.days_of_month
    assert nc.cron_equivalent == "0 5 1 * *"


def test_hourly_daily():
    from tws_parser.parser.run_cycle import normalise
    nc = normalise("DAILY EVERY HOUR", start_time="00:00")
    assert nc.frequency == "daily"
    assert nc.cron_equivalent == "0 * * * *"


def test_unparseable_cycle_returns_partial_with_warning():
    """Plan §10.4: custom calendars → partial parse, cron=null."""
    from tws_parser.parser.run_cycle import normalise
    nc = normalise("WEEKDAY EXCEPT HOLIDAYS", start_time="05:30")
    assert nc.cron_equivalent is None
    # Should still capture frequency as best-effort
    assert nc.frequency in {"weekly", "unknown"}


def test_invalid_start_time_does_not_crash():
    from tws_parser.parser.run_cycle import normalise
    nc = normalise("EVERY_WEEKDAY", start_time=None)
    assert nc.cron_equivalent is None or "* *" in nc.cron_equivalent
