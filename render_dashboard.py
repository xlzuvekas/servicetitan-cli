#!/usr/bin/env python3
"""
Build dashboard.html from the template, your config, and the data on disk.

    python render_dashboard.py            # uses live_data.json
    python render_dashboard.py --demo     # uses demo_data.json

The result is ONE self-contained HTML file. Data, configuration, styles and any
logos are inlined, so it opens straight from disk, emails, or drops onto any
static host with no server and no build step.

The data is VALIDATED before anything is written (see check_data). This matters
most for unattended refreshes: a partial ServiceTitan response would otherwise
sail through to a dashboard that looks authoritative and is quietly wrong. On
failure this writes nothing and exits non-zero, so the last good dashboard
stays where it is rather than being replaced by a broken one.
"""
import base64
import json
import os
import sys
from datetime import datetime, timezone

from st_config import load_config, die

TEMPLATE = "dashboard_template.html"
OUTPUT = "dashboard.html"
LOGO_LIGHT = "brand/logo-light.png"
LOGO_DARK = "brand/logo-dark.png"


def check_data(d, cfg, demo):
    """Problems that should block writing a dashboard."""
    problems = []
    codes = [l["code"] for l in cfg["locations"]]

    floors = {"jobs": 20, "technicians": 1, "businessUnits": 1}
    for coll, floor in floors.items():
        n = len(d.get(coll) or [])
        if n < floor:
            problems.append(f"{coll}: only {n} record(s), expected at least {floor}")

    # Every configured location must be represented. A silent auth failure on one
    # tenant would otherwise just look like a quiet quarter for that location.
    present = {j.get("loc") for j in d.get("jobs") or []}
    for code in codes:
        if code not in present:
            problems.append(f'no jobs at all for location "{code}" -- '
                            f"did that tenant fail to authenticate?")

    # Ids are namespaced by location; a mismatch means two tenants' data is
    # merging, which silently corrupts every per-person and per-campaign total.
    leaks = 0
    for coll in ("jobs", "opportunities", "ar", "purchaseOrders", "calls",
                 "memberships", "technicians"):
        for r in d.get(coll) or []:
            loc = r.get("loc")
            if not loc:
                continue
            for field in ("id", "tech", "owner", "campaign", "soldBy"):
                v = r.get(field)
                if isinstance(v, str) and ":" in v and not v.startswith(loc + ":"):
                    leaks += 1
    if leaks:
        problems.append(f"{leaks} cross-location id reference(s) -- two tenants' data is mixing")

    # Names that fail to resolve render as raw ids like "SPR:1471873".
    people = set(d.get("people") or {})
    unresolved = {o["owner"] for o in (d.get("opportunities") or [])
                  if o.get("owner") and o["owner"] not in people}
    if unresolved:
        problems.append(f"{len(unresolved)} opportunity owner(s) unresolved to a name: "
                        f"{sorted(unresolved)[:3]}")

    stamp = d.get("generatedAt")
    if not stamp:
        problems.append("generatedAt missing from the data file")
    elif not demo:
        age_h = (datetime.now(timezone.utc)
                 - datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                 ).total_seconds() / 3600
        if age_h > 6:
            problems.append(f"live_data.json is {age_h:.1f}h old -- "
                            f"did fetch_dashboard_data.py actually run?")
    return problems


def shrink(value):
    """Round money to cents so long binary fractions don't bloat the page, and
    drop nulls, which the front end treats the same as absent."""
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, dict):
        return {k: shrink(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [shrink(v) for v in value]
    return value


def inline_png(path):
    if not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def main():
    demo = "--demo" in sys.argv
    source = "demo_data.json" if demo else "live_data.json"
    cfg = load_config()

    if not os.path.exists(source):
        die(f"No {source} found.\n" + (
            "  Run:  python generate_demo_data.py" if demo else
            "  Run:  python fetch_dashboard_data.py\n"
            "  Or, to preview without credentials:  python generate_demo_data.py"
            " && python render_dashboard.py --demo"))
    with open(source, encoding="utf-8") as f:
        data = json.load(f)

    issues = check_data(data, cfg, demo)
    if issues:
        print(f"VALIDATION FAILED -- {OUTPUT} was not written:", file=sys.stderr)
        for i in issues:
            print(f"  - {i}", file=sys.stderr)
        raise SystemExit(1)

    if not os.path.exists(TEMPLATE):
        die(f"{TEMPLATE} is missing -- it should have shipped with this package.")
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()

    # The payload sits inside <script type="application/json">, so a literal
    # "</script>" in any customer or vendor name would end the tag early.
    # Escaping the slash keeps the JSON valid while making that impossible.
    payload = json.dumps(shrink(data), separators=(",", ":")).replace("</", "<\\/")
    settings = json.dumps(shrink({
        "company": cfg["company"], "locations": cfg["locations"],
        "targets": cfg["targets"], "rules": cfg["rules"],
    }), separators=(",", ":")).replace("</", "<\\/")

    for token, value in (("__LIVE_DATA_JSON__", payload),
                         ("__CONFIG_JSON__", settings),
                         ("__LOGO_LIGHT__", inline_png(LOGO_LIGHT)),
                         ("__LOGO_DARK__", inline_png(LOGO_DARK))):
        if token not in html:
            die(f"{TEMPLATE} is missing the {token} placeholder -- has it been edited?")
        html = html.replace(token, value)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(html)

    kb = len(html.encode("utf-8")) / 1024
    logos = "with your logos" if os.path.exists(LOGO_LIGHT) else "wordmark only (no logo files found)"
    print(f"Wrote {OUTPUT}  ({kb:,.0f} KB, {logos})")
    if demo:
        print("  DEMO DATA -- the dashboard shows a banner saying so.")
    print(f"\nOpen it: double-click {OUTPUT}, or drag it into a browser window.")


if __name__ == "__main__":
    main()
