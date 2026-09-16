"""Snapshots: the JSON files `fetch` writes and every query command reads.

A snapshot is a self-contained dump for one profile: rosters, jobs,
opportunities, receivables, purchase orders, calls, campaigns and memberships,
with every id namespaced by tenant code ("SPR:5510") because separate
ServiceTitan tenants reuse raw id numbers.
"""
import json
import os
import re
from collections import defaultdict

from .dates import UTC, hours_between, parse_iso
from .errors import DataError

COLLECTIONS = ("technicians", "jobs", "businessUnits", "opportunities", "ar", "purchaseOrders",
               "campaigns", "calls", "memberships")
DEFAULT_MAX_AGE_HOURS = 6.0


class Snapshot(object):
    def __init__(self, data, path=None):
        self.data = data
        self.path = path
        self.generated_at = parse_iso(data.get("generatedAt"))
        self.is_demo = bool(data.get("isDemo"))

    def age_hours(self, as_of=None):
        """Age against the real clock. ``as_of`` is accepted for signature symmetry but
        staleness is about wall-clock freshness, not the query's reference instant."""
        if not self.generated_at:
            return None
        import datetime as dt
        return round(max(0.0, hours_between(dt.datetime.now(UTC), self.generated_at)), 2)

    def tenant_codes(self):
        codes = []
        for t in self.data.get("tenants") or []:
            codes.append(t.get("code"))
        if not codes:
            codes = sorted({j.get("loc") for j in self.data.get("jobs") or [] if j.get("loc")})
        return codes

    def counts(self):
        return {k: len(self.data.get(k) or []) for k in COLLECTIONS}

    def describe(self, as_of=None, max_age_hours=DEFAULT_MAX_AGE_HOURS):
        age = self.age_hours(as_of)
        return {
            "path": self.path,
            "generatedAt": self.data.get("generatedAt"),
            "ageHours": age,
            "stale": (age is None or age > max_age_hours) and not self.is_demo,
            "isDemo": self.is_demo,
            "profile": self.data.get("profile"),
            "tenants": self.tenant_codes(),
            "counts": self.counts(),
            "cliVersion": (self.data.get("cli") or {}).get("version"),
        }


def load_snapshot(path):
    if not path or not os.path.exists(path):
        raise DataError("No snapshot at %s" % path, hint="Run `servicetitan fetch` to pull one from ServiceTitan.")
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise DataError("%s is not valid JSON: %s" % (path, e), hint="Re-run `servicetitan fetch`.")
    if not isinstance(data, dict) or "jobs" not in data:
        raise DataError("%s does not look like a snapshot" % path, hint="Re-run `servicetitan fetch`.")
    return Snapshot(data, path)


def check_snapshot(data, expected_codes, synthetic=False, max_age_hours=DEFAULT_MAX_AGE_HOURS, as_of=None,
                   floors=None):
    """Integrity checks. Returns a list of {check, ok, detail} dicts.

    These block `render` (a half-failed pull must not overwrite a good
    dashboard) and are reported by `check` so an unattended refresh can be
    monitored on its exit code.
    """
    results = []

    def add(check, ok, detail):
        results.append({"check": check, "ok": bool(ok), "detail": detail})

    floors = floors or {"jobs": 20, "technicians": 1, "businessUnits": 1}
    for coll, floor in floors.items():
        n = len(data.get(coll) or [])
        add("floor:%s" % coll, n >= floor, "%d record(s), floor %d" % (n, floor))

    present = {j.get("loc") for j in data.get("jobs") or []}
    for code in expected_codes or []:
        add("tenant-present:%s" % code, code in present,
            "jobs found" if code in present else "no jobs at all for tenant %s; did it fail to authenticate?" % code)

    leaks = 0
    for coll in ("jobs", "opportunities", "ar", "purchaseOrders", "calls", "memberships", "technicians"):
        for r in data.get(coll) or []:
            loc = r.get("loc")
            if not loc:
                continue
            for field in ("id", "tech", "owner", "campaign", "soldBy"):
                v = r.get(field)
                if isinstance(v, str) and ":" in v and not v.startswith(loc + ":"):
                    leaks += 1
    add("no-cross-tenant-ids", leaks == 0, "%d cross-tenant id reference(s)" % leaks)

    people = set(data.get("people") or {})
    unresolved = sorted({o["owner"] for o in (data.get("opportunities") or []) if o.get("owner") and o["owner"] not in people})
    add("owners-resolved", not unresolved, "all opportunity owners resolve to a name" if not unresolved
        else "%d unresolved owner id(s): %s" % (len(unresolved), unresolved[:3]))

    stamp = data.get("generatedAt")
    gen = parse_iso(stamp)
    if not gen:
        add("fresh", False, "generatedAt missing or unparsable")
    elif synthetic or data.get("isDemo"):
        add("fresh", True, "synthetic data; freshness not enforced")
    else:
        import datetime as dt
        age = hours_between(dt.datetime.now(UTC), gen)
        add("fresh", age <= max_age_hours, "%.1fh old (limit %.0fh)" % (age, max_age_hours))
    return results


def problems(results):
    return [r for r in results if not r["ok"]]


# ---------------------------------------------------------------- duplicates
def normalise_name(name):
    n = (name or "").lower()
    n = re.sub(r"\((?:t|tech|technician|office|admin|\d+)\)", " ", n)
    n = re.sub(r"[^a-z ]", " ", n)
    return " ".join(n.split())


def find_duplicates(data):
    """People who may hold more than one user record under different names."""
    people = data.get("people") or {}
    groups = defaultdict(list)
    for pid, name in people.items():
        if name:
            groups[normalise_name(name)].append((pid, name))
    activity = defaultdict(lambda: {"opportunities": 0, "won": 0, "value": 0.0, "jobs": 0})
    for o in data.get("opportunities") or []:
        if o.get("owner"):
            a = activity[o["owner"]]
            a["opportunities"] += 1
            if o.get("won"):
                a["won"] += 1
                a["value"] += o.get("value") or 0
    for j in data.get("jobs") or []:
        if j.get("tech"):
            activity[j["tech"]]["jobs"] += 1
    out = []
    for key, members in sorted(groups.items()):
        names = {n for _, n in members}
        if len(members) < 2 or len(names) < 2:
            continue
        keep = sorted(names, key=len)[0]
        out.append({
            "names": sorted(names),
            "records": [dict(id=pid, name=name, **activity.get(pid, {"opportunities": 0, "won": 0, "value": 0.0, "jobs": 0}))
                        for pid, name in sorted(members)],
            "suggestedAlias": {pid: keep for pid, name in sorted(members) if name != keep},
        })
    return out
