import datetime as dt

import pytest

from servicetitan_cli.dates import (UTC, bucket_date, forward_range, get_tz, granularity, parse_iso,
                                    previous_comparable_range, range_bounds)

DENVER = get_tz("America/Denver")
AS_OF = dt.datetime(2026, 9, 15, 17, 30, tzinfo=DENVER)


def test_parse_iso_handles_servicetitan_precision():
    d = parse_iso("2026-03-04T15:22:11.1234567Z")
    assert d == dt.datetime(2026, 3, 4, 15, 22, 11, 123456, tzinfo=UTC)
    assert parse_iso("2026-03-04T15:22:11Z").tzinfo == UTC
    assert parse_iso("2026-03-04T15:22:11+02:00").utcoffset() == dt.timedelta(hours=2)
    assert parse_iso("2026-03-04T15:22:11").tzinfo == UTC  # naive means UTC
    assert parse_iso("2026-03-04", tz=DENVER) == dt.datetime(2026, 3, 4, tzinfo=DENVER)
    assert parse_iso(None) is None and parse_iso("") is None and parse_iso("garbage") is None


def test_named_ranges_end_at_as_of():
    start, end = range_bounds("mtd", AS_OF)
    assert (start.year, start.month, start.day, start.hour) == (2026, 9, 1, 0) and end == AS_OF
    assert range_bounds("qtd", AS_OF)[0].month == 7
    assert range_bounds("ytd", AS_OF)[0] == dt.datetime(2026, 1, 1, tzinfo=DENVER)
    s7, _ = range_bounds("last7", AS_OF)
    assert (AS_OF.date() - s7.date()).days == 6
    ys, ye = range_bounds("yesterday", AS_OF)
    assert ys.day == 14 and ye.day == 14 and ye.hour == 23
    ns, ne = range_bounds("next7", AS_OF)
    assert ns == AS_OF and ne.day == 22 and ne.hour == 23


def test_custom_range_and_errors():
    s, e = range_bounds("custom", AS_OF, custom_start=dt.datetime(2026, 6, 1, tzinfo=DENVER),
                        custom_end=dt.datetime(2026, 6, 30, tzinfo=DENVER))
    assert s.day == 1 and e.day == 30 and e.hour == 23
    with pytest.raises(ValueError):
        range_bounds("custom", AS_OF)
    with pytest.raises(ValueError):
        range_bounds("fortnight", AS_OF)
    with pytest.raises(ValueError):
        range_bounds("custom", AS_OF, custom_start=dt.datetime(2026, 6, 30, tzinfo=DENVER),
                     custom_end=dt.datetime(2026, 6, 1, tzinfo=DENVER))


def test_previous_range_is_same_length_and_adjacent():
    s, e = range_bounds("mtd", AS_OF)
    ps, pe = previous_comparable_range(s, e)
    assert pe == s - dt.timedelta(days=1)
    assert (e - s) == (pe - ps)


def test_forward_range_matches_dashboard_periods():
    assert forward_range("mtd", AS_OF)[0].day == 30 and forward_range("mtd", AS_OF)[1] == "This Month"
    assert forward_range("qtd", AS_OF)[0].month == 9 and forward_range("qtd", AS_OF)[0].day == 30
    assert forward_range("ytd", AS_OF)[0].month == 12
    end, label = forward_range("custom", AS_OF, custom_end=dt.datetime(2026, 10, 3, tzinfo=DENVER))
    assert end.day == 3 and label == "Range"
    assert forward_range("last7", AS_OF) == (AS_OF, "Range")          # rolling windows have no forward part
    assert forward_range("wtd", AS_OF)[0].weekday() == 6 and forward_range("wtd", AS_OF)[1] == "This Week"
    assert forward_range("today", AS_OF)[0].day == 15 and forward_range("next7", AS_OF)[0].day == 22


def test_granularity_and_buckets():
    s = dt.datetime(2026, 9, 1, tzinfo=UTC)
    assert granularity(s, s + dt.timedelta(days=10)) == "day"
    assert granularity(s, s + dt.timedelta(days=100)) == "week"
    assert granularity(s, s + dt.timedelta(days=200)) == "month"
    wed = dt.datetime(2026, 9, 16, 14, tzinfo=UTC)
    assert bucket_date(wed, "week").weekday() == 0 and bucket_date(wed, "week").day == 14
    assert bucket_date(wed, "month").day == 1 and bucket_date(wed, "day").hour == 0


def test_timezone_lookup():
    assert get_tz("UTC") == UTC and get_tz(None) == UTC
    assert get_tz("Not/AZone") is None
    assert get_tz("America/Denver") is not None
