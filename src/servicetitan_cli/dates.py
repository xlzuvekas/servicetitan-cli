"""Date handling shared by fetch, metrics and list.

ServiceTitan timestamps are ISO-8601 UTC strings, often with seven fractional
digits ("2026-03-04T15:22:11.1234567Z"), which Python's ``fromisoformat`` does
not accept on every supported version. ``parse_iso`` normalises them.

Calendar ranges (month-to-date, quarter-to-date, ...) are computed in the
profile's timezone so that "this month" means the business's month.
"""
import datetime as dt
import re

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:  # pragma: no cover
    ZoneInfo = None

UTC = dt.timezone.utc
_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?\s*(Z|[+-]\d{2}:?\d{2})?$"
)

RANGE_KINDS = ("mtd", "qtd", "ytd", "wtd", "today", "yesterday", "last7", "last30", "last90",
               "next7", "next14", "next30", "next45", "next90", "custom")


def get_tz(name):
    """Return a tzinfo for an IANA name, or UTC when the name is empty/unknown."""
    if not name or name.upper() == "UTC":
        return UTC
    if ZoneInfo is None:
        return UTC
    try:
        return ZoneInfo(name)
    except Exception:
        return None


def parse_iso(value, tz=UTC):
    """Parse an ISO-8601 string (or date) into an aware datetime, or None.

    Naive strings are assumed UTC, which is what ServiceTitan sends. Date-only
    strings become midnight in ``tz`` so that "2026-03-04" means that day for
    the business rather than the UTC day.
    """
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, tzinfo=tz)
    m = _ISO_RE.match(str(value).strip())
    if not m:
        return None
    y, mo, d, hh, mm, ss, frac, off = m.groups()
    if hh is None:
        return dt.datetime(int(y), int(mo), int(d), tzinfo=tz)
    micro = int((frac or "0")[:6].ljust(6, "0"))
    if off is None or off == "Z":
        tzinfo = UTC
    else:
        sign = 1 if off[0] == "+" else -1
        digits = off[1:].replace(":", "")
        tzinfo = dt.timezone(sign * dt.timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
    try:
        return dt.datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss or 0), micro, tzinfo=tzinfo)
    except ValueError:
        return None


def iso_z(d):
    """Format an aware datetime as ServiceTitan-style UTC ("...Z"), seconds precision."""
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_key(d):
    return d.strftime("%Y-%m-%d")


def start_of_day(d):
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def end_of_day(d):
    return d.replace(hour=23, minute=59, second=59, microsecond=999999)


def quarter_start(d):
    q = (d.month - 1) // 3
    return start_of_day(d.replace(month=q * 3 + 1, day=1))


def month_end(d):
    nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return end_of_day(nxt - dt.timedelta(days=1))


def quarter_end(d):
    q = (d.month - 1) // 3
    last_month = q * 3 + 3
    return month_end(d.replace(month=last_month, day=1))


def year_end(d):
    return end_of_day(d.replace(month=12, day=31))


def range_bounds(kind, as_of, custom_start=None, custom_end=None):
    """[start, end] for a named range, ending at ``as_of`` (aware, in profile tz).

    ``custom`` uses the supplied bounds (start at 00:00, end at 23:59:59). A
    custom end in the future is allowed so booked work can be queried.
    """
    kind = (kind or "mtd").lower()
    if kind == "mtd":
        return start_of_day(as_of.replace(day=1)), as_of
    if kind == "qtd":
        return quarter_start(as_of), as_of
    if kind == "ytd":
        return start_of_day(as_of.replace(month=1, day=1)), as_of
    if kind == "wtd":
        return start_of_day(as_of - dt.timedelta(days=as_of.weekday())), as_of
    if kind == "today":
        return start_of_day(as_of), as_of
    if kind == "yesterday":
        y = as_of - dt.timedelta(days=1)
        return start_of_day(y), end_of_day(y)
    if kind in ("last7", "last30", "last90"):
        n = int(kind[4:])
        return start_of_day(as_of - dt.timedelta(days=n - 1)), as_of
    if kind in ("next7", "next14", "next30", "next45", "next90"):
        # Forward-looking: from now through N days ahead. Useful for booked jobs, renewals and due invoices.
        n = int(kind[4:])
        return as_of, end_of_day(as_of + dt.timedelta(days=n))
    if kind == "custom":
        if custom_start is None:
            raise ValueError("custom range needs a start date")
        start = start_of_day(custom_start)
        end = end_of_day(custom_end) if custom_end is not None else as_of
        if end < start:
            raise ValueError("custom range end (%s) is before its start (%s)" % (day_key(end), day_key(start)))
        return start, end
    raise ValueError("unknown range kind %r (choose from %s)" % (kind, ", ".join(RANGE_KINDS)))


def previous_comparable_range(start, end):
    """The same-length window immediately before [start, end]."""
    span = end - start
    prev_end = start - dt.timedelta(days=1)
    return prev_end - span, prev_end


def forward_range(kind, as_of, custom_end=None):
    """Where the period named by the range ends, for the "Rest of This Month"
    and run-rate tiles. Calendar ranges project to the end of their calendar
    period; rolling windows (last7 ...) end where they end, so their run rate is
    simply the actual and there is no "rest of period".

    Returns (end, label).
    """
    kind = (kind or "mtd").lower()
    if kind == "mtd":
        return month_end(as_of), "This Month"
    if kind == "qtd":
        return quarter_end(as_of), "This Quarter"
    if kind == "ytd":
        return year_end(as_of), "This Year"
    if kind == "wtd":
        return end_of_day(as_of + dt.timedelta(days=6 - as_of.weekday())), "This Week"
    if kind == "today":
        return end_of_day(as_of), "Today"
    if kind == "yesterday":
        return end_of_day(as_of - dt.timedelta(days=1)), "Yesterday"
    if kind in ("last7", "last30", "last90"):
        return as_of, "Range"
    if kind in ("next7", "next14", "next30", "next45", "next90"):
        return end_of_day(as_of + dt.timedelta(days=int(kind[4:]))), "Range"
    end = end_of_day(custom_end) if custom_end is not None else end_of_day(as_of)
    return end, "Range"


def granularity(start, end):
    days = (end - start).total_seconds() / 86400.0
    if days <= 48:
        return "day"
    if days <= 150:
        return "week"
    return "month"


def bucket_date(d, gran):
    d0 = start_of_day(d)
    if gran == "day":
        return d0
    if gran == "week":
        return d0 - dt.timedelta(days=d0.weekday())
    return d0.replace(day=1)


def hours_between(later, earlier):
    return (later - earlier).total_seconds() / 3600.0
