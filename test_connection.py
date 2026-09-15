#!/usr/bin/env python3
"""
Check that every location in config.json can authenticate against ServiceTitan
and read data. Run this first -- it fails with a specific message rather than
letting the full pull die halfway through.

READ-ONLY: exchanges credentials for a token, then lists business units.

    python test_connection.py
"""
import requests

from st_config import (load_config, load_env, validate_credentials,
                       dept_for_business_unit)

BASE_API = "https://api.servicetitan.io"
BASE_AUTH = "https://auth.servicetitan.io"


def check(loc, creds, mapping):
    label = loc["label"]
    suffix = (loc.get("env") or "").strip()
    keys = f"env suffix {suffix}" if suffix else "unsuffixed .env keys"
    print(f"\n[{label}]  (location code {loc['code']}, {keys})")

    try:
        r = requests.post(f"{BASE_AUTH}/connect/token", timeout=20, data={
            "grant_type": "client_credentials",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
        })
    except requests.RequestException as e:
        print(f"  FAIL  could not reach {BASE_AUTH} -- {e}")
        print("        Check your internet connection or proxy.")
        return False

    if not r.ok:
        print(f"  FAIL  token request returned {r.status_code}")
        print(f"        {r.text[:300]}")
        if r.status_code in (400, 401):
            print("        Client ID or Client Secret is wrong. The secret is shown only once")
            print("        when created -- generate a new one in the developer portal if unsure.")
        return False
    token = r.json().get("access_token")
    print("  ok    authenticated")

    headers = {"Authorization": f"Bearer {token}", "ST-App-Key": creds["app_key"]}
    r = requests.get(f"{BASE_API}/settings/v2/tenant/{creds['tenant_id']}/business-units",
                     headers=headers, params={"pageSize": 200}, timeout=25)
    if not r.ok:
        print(f"  FAIL  business-units returned {r.status_code}")
        print(f"        {r.text[:300]}")
        if r.status_code == 401:
            print("        App Key is wrong, or the app is not connected to this tenant.")
        elif r.status_code == 403:
            print("        The app lacks read scope for Settings. Grant it in the developer portal.")
        elif r.status_code == 404:
            print("        Tenant ID looks wrong for these credentials.")
        return False

    units = r.json().get("data", [])
    print(f"  ok    {len(units)} business unit(s) readable")

    # Show how each unit will be classified, so a bad departmentMapping is
    # obvious now rather than as strange totals on the dashboard later.
    buckets = {}
    for u in units:
        buckets.setdefault(dept_for_business_unit(u.get("name"), mapping), []).append(u.get("name"))
    for dept in ("Service", "Install", "Sales", "Other", None):
        if dept in buckets:
            shown = ", ".join(buckets[dept][:4]) + ("  ..." if len(buckets[dept]) > 4 else "")
            print(f"          {str(dept or 'EXCLUDED'):<9} {len(buckets[dept]):>2}  {shown}")
    if "Other" in buckets:
        print('          ^ "Other" units count toward company totals but appear on no department tab.')
        print('            Add a keyword to departmentMapping in config.json if that is wrong.')
    return True


def main():
    cfg = load_config()
    env = load_env()
    mapping = cfg["departmentMapping"]
    print(f"Testing {len(cfg['locations'])} location(s) for {cfg['company']['name']}")
    creds = validate_credentials(env, cfg)
    results = [check(loc, creds[loc["code"]], mapping) for loc in cfg["locations"]]

    print()
    if all(results):
        print("All locations connected. Next:  python fetch_dashboard_data.py")
    else:
        print(f"{results.count(False)} of {len(results)} location(s) failed -- see above.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
