#!/usr/bin/env python3
"""
Shared configuration loading for the ServiceTitan dashboard template.

Everything tenant-specific lives in config.json (structure, targets, locations)
and .env (credentials). No script in this package hardcodes a company name,
a location, a business-unit name or a target.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

CONFIG_PATH = "config.json"
ENV_PATH = ".env"

# The four credentials, and the base name of the .env key each is read from.
# A location reads "<BASE>_<its env suffix>" first and plain "<BASE>" second,
# so one value can serve every location or each can have its own.
CRED_FIELDS = {
    "client_id": "SERVICETITAN_CLIENT_ID",
    "client_secret": "SERVICETITAN_CLIENT_SECRET",
    "tenant_id": "SERVICETITAN_TENANT_ID",
    "app_key": "SERVICETITAN_APP_KEY",
}

# A location's env suffix becomes part of an environment-variable name.
ENV_SUFFIX_RE = re.compile(r"^[A-Za-z0-9_]+$")


def die(msg):
    print(f"\n{msg}\n", file=sys.stderr)
    raise SystemExit(1)


def load_env(path=ENV_PATH):
    """Parse a .env file into a dict. Values are taken literally after the first
    '=', so secrets containing '=' survive intact."""
    if not os.path.exists(path):
        die(f"No {path} found.\n"
            f"  Copy .env.example to .env and fill in your ServiceTitan credentials.\n"
            f"  See README.md, 'Step 2: connect ServiceTitan'.")
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def load_config(path=CONFIG_PATH):
    if not os.path.exists(path):
        die(f"No {path} found.\n"
            f"  Copy config.example.json to config.json and edit it.\n"
            f"  See README.md, 'Step 1: configure'.")
    with open(path, encoding="utf-8") as f:
        try:
            cfg = json.load(f)
        except json.JSONDecodeError as e:
            die(f"{path} is not valid JSON: {e}\n"
                f"  A trailing comma after the last item in a list or object is the usual cause.")

    # Drop the "_comment" style documentation keys so they never reach the page.
    cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}

    locs = cfg.get("locations") or []
    if not locs:
        die("config.json has no locations. Add at least one, e.g.\n"
            '  "locations": [{ "code": "MAIN", "label": "Main Office" }]')
    seen, seen_env = set(), {}
    for loc in locs:
        for field in ("code", "label"):
            if not loc.get(field):
                die(f'Every location needs a "{field}". Offending entry: {json.dumps(loc)}')
        if loc["code"] in seen:
            die(f'Duplicate location code "{loc["code"]}". Codes must be unique -- '
                f"they namespace every id, and duplicates would merge two tenants' data.")
        seen.add(loc["code"])

        # "env" is the suffix on this location's .env keys. One location may
        # omit it and use the unsuffixed keys. Several locations each need their
        # own, or they would all read one tenant and report its numbers as
        # everybody's.
        suffix = (loc.get("env") or "").strip()
        if suffix and not ENV_SUFFIX_RE.match(suffix):
            die(f'Location "{loc["label"]}" has env "{suffix}", which cannot be part of an\n'
                f"  environment-variable name. Letters, digits and underscores only --\n"
                f'  e.g. "env": "SPRINGFIELD", which reads SERVICETITAN_TENANT_ID_SPRINGFIELD.')
        if len(locs) > 1:
            if not suffix:
                die(f'Location "{loc["label"]}" needs an "env" suffix.\n'
                    f"  With more than one location, each needs its own suffix so each reads its\n"
                    f'  own credentials -- e.g. "env": "SPRINGFIELD" reads\n'
                    f"  SERVICETITAN_TENANT_ID_SPRINGFIELD. Whatever is genuinely shared, which\n"
                    f"  is usually the app key, can still be set once with no suffix.")
            if suffix.upper() in seen_env:
                die(f'Locations "{seen_env[suffix.upper()]}" and "{loc["label"]}" both use '
                    f'env "{suffix}".\n'
                    f"  Each location needs its own suffix, or both read the same tenant.")
            seen_env[suffix.upper()] = loc["label"]

    cfg.setdefault("company", {}).setdefault("name", "Operations Dashboard")
    cfg.setdefault("targets", {})
    cfg.setdefault("rules", {})
    cfg["rules"].setdefault("lowTicketThreshold", 150)
    cfg["rules"].setdefault("boardDays", 45)
    cfg.setdefault("personAliases", {})
    cfg.setdefault("departmentMapping", {
        "Install": ["install"], "Sales": ["sales"], "Service": ["service", "maint"],
        "exclude": ["warehouse"],
    })
    cfg.setdefault("window", {})
    return cfg


def window_starts(cfg):
    """(operational_start, ar_start) as ISO-8601 UTC strings.

    Operational data covers the current year by default so 'year to date' means
    what it says. AR reaches further back, otherwise the oldest aging bucket is
    an artifact of the pull window rather than a real balance.
    """
    w = cfg.get("window") or {}
    start = w.get("operationalStart")
    if start:
        op = f"{start}T00:00:00Z" if len(start) == 10 else start
    else:
        op = f"{datetime.now(timezone.utc).year}-01-01T00:00:00Z"
    months = int(w.get("arMonthsBack") or 20)
    ar_dt = datetime.now(timezone.utc) - timedelta(days=months * 31)
    return op, ar_dt.strftime("%Y-%m-%dT00:00:00Z")


def credentials_for(env, loc):
    """Resolve one location's four credentials from the parsed .env.

    Each credential is looked up twice -- this location's own key first, then
    the unsuffixed key shared by every location:

        SERVICETITAN_APP_KEY_SPRINGFIELD   this location only
        SERVICETITAN_APP_KEY               every location without its own

    The first non-empty value wins. So a single location can use the unsuffixed
    names throughout and needs no "env" in config.json at all, while several
    locations set once whatever is genuinely the same -- normally the app key,
    which belongs to your app rather than to any one tenant -- and suffix only
    what differs. A per-location key always beats the shared one, so adding one
    never disturbs the others.
    """
    suffix = (loc.get("env") or "").strip()
    creds, missing = {}, []
    for name, base in CRED_FIELDS.items():
        specific = f"{base}_{suffix}" if suffix else ""
        value = (env.get(specific) if specific else "") or env.get(base) or ""
        if value:
            creds[name] = value
        else:
            missing.append((specific, base))
    if missing:
        lines = []
        for specific, base in missing:
            lines.append(f"  {specific or base}=\n")
            if specific:
                lines.append(f"      or {base}= to share one value across locations\n")
        die(f"Location \"{loc['label']}\" is missing credentials in .env:\n"
            + "".join(lines)
            + "  Add them to .env, or remove that location from config.json.")
    return creds


def validate_credentials(env, cfg):
    """Resolve every location's credentials up front. Returns {code: creds}.

    Two reasons to do this before any network call: a missing key should fail
    in a second rather than halfway through a long pull, and two locations
    resolving to the SAME tenant id needs catching. That last one is the way a
    multi-location .env goes wrong -- an unsuffixed SERVICETITAN_TENANT_ID is
    inherited by every location that lacks its own, so one forgotten suffixed
    key silently pulls the same tenant twice and doubles it into the totals.
    """
    resolved, by_tenant = {}, {}
    for loc in cfg["locations"]:
        creds = credentials_for(env, loc)
        resolved[loc["code"]] = creds
        tid = creds["tenant_id"]
        if tid in by_tenant:
            die(f'Locations "{by_tenant[tid]}" and "{loc["label"]}" resolve to the same\n'
                f"  ServiceTitan tenant id, so both would pull identical data.\n"
                f"  Give each its own SERVICETITAN_TENANT_ID_<suffix> in .env -- an\n"
                f"  unsuffixed SERVICETITAN_TENANT_ID is shared by every location that\n"
                f"  does not set its own.")
        by_tenant[tid] = loc["label"]
    return resolved


def dept_for_business_unit(name, mapping):
    """Map a ServiceTitan business-unit name to a department.

    Keyword match, case-insensitive, first list wins. Returns None for units
    that should be dropped entirely (warehouses and the like).
    """
    n = (name or "").lower()
    for kw in mapping.get("exclude", []):
        if kw.lower() in n:
            return None
    for dept, keywords in mapping.items():
        if dept == "exclude":
            continue
        for kw in keywords:
            if kw.lower() in n:
                return dept
    return "Other"


def num(v):
    """ServiceTitan returns money as either a float or a decimal string."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
