#!/usr/bin/env python3
"""
List people who may hold more than one ServiceTitan user record.

    python fetch_dashboard_data.py     # first, so live_data.json exists
    python check_duplicates.py

Why this exists: every per-person total on the dashboard is keyed by NAME, so
two records carrying the SAME name merge on their own. Two records for one
person under DIFFERENT names ("Robin Vance" and "Robin Vance (T)", say) stay
split, and that person's numbers are quietly halved across two rows.

This finds likely pairs. It deliberately does NOT merge them: two similar names
can easily be two real people, and merging on a guess combines their revenue
into a figure nobody can trace. Confirm with the person or your ServiceTitan
admin, then add the mapping to "personAliases" in config.json.

Best fixed at the source -- merging the duplicate user records inside
ServiceTitan corrects every report you run, not just this dashboard.
"""
import json
import os
import re
import sys
from collections import defaultdict

SOURCE = "live_data.json"


def normalise(name):
    """Strip case, punctuation, and common disambiguating suffixes so that
    'Robin Vance (T)' and 'Robin Vance' collapse to the same key."""
    n = (name or "").lower()
    n = re.sub(r"\((?:t|tech|technician|office|admin|\d+)\)", " ", n)
    n = re.sub(r"[^a-z ]", " ", n)
    return " ".join(n.split())


def main():
    if not os.path.exists(SOURCE):
        print(f"No {SOURCE} found. Run:  python fetch_dashboard_data.py", file=sys.stderr)
        raise SystemExit(1)
    with open(SOURCE, encoding="utf-8") as f:
        d = json.load(f)

    people = d.get("people") or {}
    groups = defaultdict(list)
    for pid, name in people.items():
        if name:
            groups[normalise(name)].append((pid, name))

    # Activity per person id, so the report shows what is actually at stake.
    activity = defaultdict(lambda: {"opps": 0, "won": 0, "value": 0.0, "jobs": 0})
    for o in d.get("opportunities") or []:
        if o.get("owner"):
            a = activity[o["owner"]]
            a["opps"] += 1
            if o.get("won"):
                a["won"] += 1
                a["value"] += o.get("value") or 0
    for j in d.get("jobs") or []:
        if j.get("tech"):
            activity[j["tech"]]["jobs"] += 1

    found = 0
    for key, members in sorted(groups.items()):
        names = {n for _, n in members}
        if len(members) < 2 or len(names) < 2:
            continue          # identical names already merge on their own
        found += 1
        print(f"\nPossible duplicate: {' / '.join(sorted(names))}")
        for pid, name in sorted(members):
            a = activity.get(pid, {})
            print(f"   {pid:<18} {name!r:<28} "
                  f"{a.get('opps',0):>3} opps, {a.get('won',0):>3} won, "
                  f"${a.get('value',0):>10,.0f}, {a.get('jobs',0):>4} jobs")
        keep = sorted(names, key=len)[0]
        print("   If these are the same person, add to config.json > personAliases:")
        for pid, name in sorted(members):
            if name != keep:
                print(f'      "{pid}": "{keep}",')

    print()
    if not found:
        print("No likely duplicates found. Every name in the roster is distinct,")
        print("and records sharing a name already merge automatically.")
    else:
        print(f"{found} possible duplicate(s). Confirm each is really one person before")
        print("adding it -- merging two different people is worse than leaving them split.")


if __name__ == "__main__":
    main()
