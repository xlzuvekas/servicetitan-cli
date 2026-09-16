"""Rendering results for agents (JSON) and people (text).

JSON is the default and is always a single document on stdout. Text mode is a
plain rendering of the same structure: dicts as ``key: value`` lines, lists of
dicts as aligned tables. Nothing is printed to stdout except the result, so
``servicetitan ... | jq`` always works. Progress and warnings go to stderr.
"""
import datetime as _dt
import decimal
import json
import sys

SECRET_KEYS = {"clientSecret", "client_secret", "appKey", "app_key", "access_token", "accessToken", "password", "secret"}


def json_default(o):
    if isinstance(o, (_dt.datetime, _dt.date)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return float(o)
    if isinstance(o, set):
        return sorted(o)
    raise TypeError("Object of type %s is not JSON serializable" % type(o).__name__)


def dumps(data, pretty=True):
    if pretty:
        return json.dumps(data, indent=2, default=json_default, ensure_ascii=False)
    return json.dumps(data, separators=(",", ":"), default=json_default, ensure_ascii=False)


def redact(value):
    """Return a copy with secret-looking keys replaced by a marker."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in SECRET_KEYS and v:
                out[k] = "***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _fmt_scalar(v):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if abs(v) >= 1000:
            return "{:,.0f}".format(v)
        return "{:,.2f}".format(v).rstrip("0").rstrip(".") if v != int(v) else "{:,.0f}".format(v)
    if isinstance(v, int):
        return "{:,}".format(v)
    if isinstance(v, (list, dict)):
        return json.dumps(v, default=json_default, ensure_ascii=False)
    return str(v)


def _table(rows, indent=""):
    cols = []
    for r in rows:
        for k in r.keys():
            if k not in cols:
                cols.append(k)
    cells = [[_fmt_scalar(r.get(c)) for c in cols] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]
    numeric = [all(isinstance(r.get(c), (int, float)) and not isinstance(r.get(c), bool) for r in rows if r.get(c) is not None) for c in cols]
    def line(vals):
        parts = []
        for i, v in enumerate(vals):
            parts.append(v.rjust(widths[i]) if numeric[i] else v.ljust(widths[i]))
        return indent + "  ".join(parts).rstrip()
    out = [line(cols), indent + "  ".join("-" * w for w in widths)]
    out += [line(row) for row in cells]
    return "\n".join(out)


def to_text(data, indent=""):
    if isinstance(data, dict):
        lines = []
        for k, v in data.items():
            if isinstance(v, dict):
                lines.append("%s%s:" % (indent, k))
                lines.append(to_text(v, indent + "  "))
            elif isinstance(v, list):
                if v and all(isinstance(x, dict) for x in v):
                    lines.append("%s%s: (%d)" % (indent, k, len(v)))
                    lines.append(_table(v, indent + "  "))
                elif not v:
                    lines.append("%s%s: (none)" % (indent, k))
                else:
                    lines.append("%s%s: %s" % (indent, k, ", ".join(_fmt_scalar(x) for x in v)))
            else:
                lines.append("%s%s: %s" % (indent, k, _fmt_scalar(v)))
        return "\n".join(lines)
    if isinstance(data, list):
        if data and all(isinstance(x, dict) for x in data):
            return _table(data, indent)
        return "\n".join(indent + _fmt_scalar(x) for x in data)
    return indent + _fmt_scalar(data)


def emit(data, fmt="json", pretty=True, stream=None):
    stream = stream or sys.stdout
    if fmt == "text":
        stream.write(to_text(data) + "\n")
    else:
        stream.write(dumps(data, pretty=pretty) + "\n")
    stream.flush()


def emit_error(err_dict, fmt="json", stream=None):
    stream = stream or sys.stderr
    if fmt == "text":
        e = err_dict.get("error", err_dict)
        stream.write("error [%s]: %s\n" % (e.get("code", "ERROR"), e.get("message", "")))
        if e.get("hint"):
            stream.write("  hint: %s\n" % e["hint"])
        if e.get("details"):
            stream.write("  details: %s\n" % json.dumps(e["details"], default=json_default))
    else:
        stream.write(dumps(err_dict) + "\n")
    stream.flush()


def progress(msg, quiet=False):
    """Human progress line on stderr. Never stdout, which is reserved for the result."""
    if not quiet:
        sys.stderr.write(msg.rstrip("\n") + "\n")
        sys.stderr.flush()
