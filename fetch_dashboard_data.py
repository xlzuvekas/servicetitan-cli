#!/usr/bin/env python3
"""
Pull live data from ServiceTitan for every location in config.json and write
live_data.json.

READ-ONLY: only issues GET requests. Nothing is ever written back to
ServiceTitan.

    python fetch_dashboard_data.py

--------------------------------------------------------------------------
HOW THE DERIVED NUMBERS ARE BUILT
--------------------------------------------------------------------------
These rules were worked out against real ServiceTitan tenants. Several of them
exist because the obvious approach produces a plausible-looking wrong answer.
If a figure on the dashboard looks off, read this before assuming a bug.

IDS ARE NAMESPACED BY LOCATION ("SPR:5510"). Separate ServiceTitan tenants
reuse the same raw id numbers -- business unit 5510 can exist in both -- so any
raw id used as a key would silently merge two companies' data.

BOOKED ("scheduled") REVENUE. A booked job carries its priced value in
`job.total`; most booked jobs have no estimate attached at all. So booked
revenue reads `job.total` first and only falls back to the total of the job's
SOLD estimate when the job has not been priced that way. Each booked job is
dated to its EARLIEST FUTURE appointment, found by scanning all future
appointments rather than trusting the job's own `firstAppointmentId` -- a
rescheduled job's first appointment sits in the past while a later one is still
on the board, and keying off it undercounts badly.

STALE OPEN JOBS. Jobs stuck in Scheduled/InProgress with NO future appointment
are excluded rather than counted as future revenue. The script prints how many
it skipped; a large number is a dispatch-hygiene signal worth chasing.

ACCOUNTS RECEIVABLE. An invoice with a balance but NO invoice date is not a
receivable: ServiceTitan pre-creates an invoice when a job is booked, so those
are unbilled future work. Counting them both double-counts booked revenue and,
because undated rows fall through every aging bucket, dumps them into the
oldest bucket as if severely overdue. They are excluded, and the count is
printed.

OPPORTUNITIES, NOT ESTIMATES. A Good/Better/Best presentation writes one
estimate per option for what is a single chance to sell. Counting estimates
inflates the denominator and understates the close rate. Estimates are grouped
into one opportunity per job; an opportunity is WON if any option sold, and its
value counts only the options that actually sold, never the whole menu.

WHO OWNS AN OPPORTUNITY. ServiceTitan only stamps `soldBy` on estimates that
sold, so it cannot attribute the ones that did not -- keying off it alone makes
every rep look like a 100% closer. Ownership falls back to the job's assigned
technician. Owners still unresolved after that are looked up individually so
they never render as a raw id.

MEMBERSHIP ATTACH is approximate. Membership records carry an `activatedFromId`
that looks like a job reference but is not a Jobs-table id (a direct lookup
404s), so "a membership was sold on this job" is approximated by same customer
+ same calendar day.

ACCOUNTS PAYABLE is not available. ServiceTitan exposes no vendor-bills
endpoint on standard plans, so the Accounting tab reports COMMITTED SPEND on
purchase orders not yet received. It is labeled that way rather than called AP.

CALLS are rolled up to one row per (day, agent, campaign, direction). The
dashboard only ever shows call counts, so this is lossless and cuts the
payload substantially.
"""
import json
import sys
from datetime import datetime, timezone

import requests

from st_config import (load_config, load_env, credentials_for, validate_credentials,
                       window_starts,
                       dept_for_business_unit, num)

BASE_API = "https://api.servicetitan.io"
BASE_AUTH = "https://auth.servicetitan.io"
PO_CLOSED_STATUSES = {"Received", "Canceled"}


def get_token(client_id, client_secret):
    r = requests.post(f"{BASE_AUTH}/connect/token", timeout=20, data={
        "grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret})
    r.raise_for_status()
    return r.json()["access_token"]


def get_all_pages(url, headers, params, label="", max_pages=60):
    items, page = [], 1
    while page <= max_pages:
        r = requests.get(url, headers=headers, params=dict(params, page=page), timeout=30)
        if not r.ok:
            print(f"   [{label}] request failed ({r.status_code}): {r.text[:200]}", file=sys.stderr)
            break
        body = r.json()
        items.extend(body.get("data", []))
        if not body.get("hasMore"):
            break
        page += 1
    else:
        print(f"   [{label}] hit the {max_pages}-page cap -- results may be truncated", file=sys.stderr)
    return items


def pull_location(cfg, env, loc):
    creds = credentials_for(env, loc)
    code = loc["code"]
    mapping = cfg["departmentMapping"]
    low_ticket = cfg["rules"]["lowTicketThreshold"]
    op_start, ar_start = window_starts(cfg)

    def key(raw):
        return None if raw is None else f"{code}:{raw}"

    print(f"[{code}] authenticating...")
    headers = {"Authorization": f"Bearer {get_token(creds['client_id'], creds['client_secret'])}",
               "ST-App-Key": creds["app_key"]}
    t = creds["tenant_id"]
    out = {}

    # ------------------------------------------------------------ roster
    print(f"[{code}] business units, technicians, employees...")
    bus = get_all_pages(f"{BASE_API}/settings/v2/tenant/{t}/business-units", headers,
                        {"pageSize": 200}, "business-units")
    bu_dept = {b["id"]: dept_for_business_unit(b.get("name"), mapping) for b in bus}
    out["businessUnits"] = [{"id": key(b["id"]), "loc": code, "name": b.get("name"),
                             "dept": bu_dept[b["id"]]} for b in bus]

    techs = get_all_pages(f"{BASE_API}/settings/v2/tenant/{t}/technicians", headers,
                          {"pageSize": 200}, "technicians")
    # Inactive techs are kept: historical completed jobs still reference them, and
    # the name lookup needs the full roster to avoid showing raw ids.
    out["technicians"] = [{"id": key(x["id"]), "name": x.get("name"), "loc": code,
                           "dept": bu_dept.get(x.get("businessUnitId"))} for x in techs]

    employees = get_all_pages(f"{BASE_API}/settings/v2/tenant/{t}/employees", headers,
                              {"pageSize": 200}, "employees")
    people = {key(x["id"]): x.get("name") for x in techs}
    people.update({key(x["id"]): x.get("name") for x in employees})

    # ------------------------------------------------------------ jobs
    print(f"[{code}] jobs...")
    completed_jobs = get_all_pages(f"{BASE_API}/jpm/v2/tenant/{t}/jobs", headers,
        {"pageSize": 200, "jobStatus": "Completed", "completedOnOrAfter": op_start}, "jobs-completed")
    open_jobs = []
    for status in ("Scheduled", "InProgress", "Hold"):
        open_jobs += get_all_pages(f"{BASE_API}/jpm/v2/tenant/{t}/jobs", headers,
            {"pageSize": 200, "jobStatus": status, "createdOnOrAfter": op_start}, f"jobs-{status}")

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    future_appts = get_all_pages(f"{BASE_API}/jpm/v2/tenant/{t}/appointments", headers,
        {"pageSize": 200, "startsOnOrAfter": now_iso}, "appointments")
    job_next_appt = {}
    for a in future_appts:
        jid = a.get("jobId")
        if jid is None:
            continue
        if jid not in job_next_appt or a["start"] < job_next_appt[jid]:
            job_next_appt[jid] = a["start"]

    assignments = get_all_pages(f"{BASE_API}/dispatch/v2/tenant/{t}/appointment-assignments",
        headers, {"pageSize": 200, "modifiedOnOrAfter": op_start}, "assignments")
    job_tech = {}
    for a in assignments:
        job_tech.setdefault(a["jobId"], a["technicianId"])

    print(f"[{code}] sold-estimate fallback for unpriced booked jobs...")
    job_sold_estimate = {}
    for j in open_jobs:
        if j["id"] not in job_next_appt or num(j.get("total")) > 0:
            continue
        for eid in (j.get("estimateIds") or [])[:15]:
            r = requests.get(f"{BASE_API}/sales/v2/tenant/{t}/estimates/{eid}", headers=headers, timeout=20)
            if r.ok and (r.json().get("status") or {}).get("name") == "Sold":
                job_sold_estimate[j["id"]] = job_sold_estimate.get(j["id"], 0) + \
                    sum(num(i.get("total")) for i in (r.json().get("items") or []))

    print(f"[{code}] memberships...")
    memberships = get_all_pages(f"{BASE_API}/memberships/v2/tenant/{t}/memberships", headers,
                                {"pageSize": 200}, "memberships")
    mtypes = get_all_pages(f"{BASE_API}/memberships/v2/tenant/{t}/membership-types", headers,
                           {"pageSize": 200}, "membership-types")
    mtype_name = {m["id"]: m.get("name") for m in mtypes}
    membership_customer_days = {(m["customerId"], m["createdOn"][:10])
                                for m in memberships if m.get("customerId")}

    jobs_out = []
    for j in completed_jobs:
        dept = bu_dept.get(j.get("businessUnitId"))
        if dept is None:
            continue
        rev = num(j.get("total"))
        jobs_out.append({
            "id": key(j["id"]), "date": j["completedOn"], "loc": code, "dept": dept,
            "tech": key(job_tech.get(j["id"])), "revenue": rev, "future": False,
            "campaign": key(j.get("campaignId")),
            "membershipSold": (j.get("customerId"), j["completedOn"][:10]) in membership_customer_days,
            "lowTicket": 0 < rev < low_ticket,
            "options": len(j.get("estimateIds") or []),
        })

    skipped = 0
    for j in open_jobs:
        dept = bu_dept.get(j.get("businessUnitId"))
        if dept is None:
            continue
        start = job_next_appt.get(j["id"])
        if not start:
            skipped += 1
            continue
        jobs_out.append({
            "id": key(j["id"]), "date": start, "loc": code, "dept": dept,
            "tech": key(job_tech.get(j["id"])),
            "revenue": num(j.get("total")) or job_sold_estimate.get(j["id"], 0),
            "future": True, "campaign": key(j.get("campaignId")),
            "membershipSold": False, "lowTicket": False,
            "options": len(j.get("estimateIds") or []),
        })
    out["jobs"] = jobs_out

    # ------------------------------------------------------------ opportunities
    print(f"[{code}] estimates and customers...")
    estimates = get_all_pages(f"{BASE_API}/sales/v2/tenant/{t}/estimates", headers,
                              {"pageSize": 200, "createdOnOrAfter": op_start}, "estimates")
    customers = get_all_pages(f"{BASE_API}/crm/v2/tenant/{t}/customers", headers,
                              {"pageSize": 200}, "customers")
    cust_name = {c["id"]: c.get("name") for c in customers}
    cust_city = {c["id"]: (c.get("address") or {}).get("city") for c in customers}

    est_total = lambda e: sum(num(i.get("total")) for i in (e.get("items") or []))
    groups = {}
    for e in estimates:
        groups.setdefault(e.get("jobId") or f"est-{e['id']}", []).append(e)

    opportunities = []
    for gk, group in groups.items():
        job_id = None if str(gk).startswith("est-") else gk
        sold = [e for e in group if (e.get("status") or {}).get("name") == "Sold"]
        created = sorted(e["createdOn"] for e in group if e.get("createdOn"))
        sold_on = sorted(e["soldOn"] for e in sold if e.get("soldOn"))
        owner = next((e.get("soldBy") for e in sold if e.get("soldBy")), None)
        if owner is None and job_id is not None:
            owner = job_tech.get(job_id)
        cust_id = next((e.get("customerId") for e in group if e.get("customerId")), None)
        titled = sold or sorted(group, key=est_total, reverse=True)
        opportunities.append({
            "id": key(gk), "loc": code,
            "dept": bu_dept.get(group[0].get("businessUnitId")),
            "customer": cust_name.get(cust_id) or "(no customer)",
            "city": cust_city.get(cust_id),
            "jobNumber": next((e.get("jobNumber") for e in group if e.get("jobNumber")), None),
            "title": (titled[0].get("name") or "").strip() or None,
            "presentedOn": created[0] if created else None,
            "wonOn": sold_on[0] if sold_on else None,
            "won": bool(sold), "options": len(group), "owner": key(owner),
            "value": sum(est_total(e) for e in sold),
            "topOption": max((est_total(e) for e in group), default=0.0),
        })

    # An owner can be a deactivated user the roster pull missed; look those up
    # individually so they never render as a raw id like "SPR:1471873".
    for pid in {o["owner"] for o in opportunities if o["owner"] and o["owner"] not in people}:
        raw = pid.split(":", 1)[1]
        for endpoint in ("technicians", "employees"):
            r = requests.get(f"{BASE_API}/settings/v2/tenant/{t}/{endpoint}/{raw}",
                             headers=headers, timeout=20)
            if r.ok and r.json().get("name"):
                people[pid] = r.json()["name"]
                break
        else:
            print(f"   [{code}] owner {pid} could not be resolved to a name", file=sys.stderr)

    out["opportunities"] = opportunities
    out["estimateCount"] = len(estimates)
    out["people"] = people

    # ------------------------------------------------------------ accounting
    print(f"[{code}] invoices and purchase orders...")
    invoices = get_all_pages(f"{BASE_API}/accounting/v2/tenant/{t}/invoices", headers,
                             {"pageSize": 200, "invoiceDateOnOrAfter": ar_start}, "invoices")
    unbilled = [i for i in invoices if num(i.get("balance")) > 0 and not i.get("invoiceDate")]
    out["ar"] = [{
        "id": key(i["id"]), "loc": code,
        "customer": (i.get("customer") or {}).get("name") or "(no customer)",
        "invoiceDate": i.get("invoiceDate"), "dueDate": i.get("dueDate"),
        "balance": num(i.get("balance")),
        "businessUnit": (i.get("businessUnit") or {}).get("name"),
    } for i in invoices if num(i.get("balance")) > 0 and i.get("invoiceDate")]
    if unbilled:
        print(f"   [{code}] {len(unbilled)} unbilled invoice(s) excluded from AR "
              f"(${sum(num(i.get('balance')) for i in unbilled):,.0f} of not-yet-issued work)")

    pos = get_all_pages(f"{BASE_API}/inventory/v2/tenant/{t}/purchase-orders", headers,
                        {"pageSize": 200}, "purchase-orders")
    vendors = get_all_pages(f"{BASE_API}/inventory/v2/tenant/{t}/vendors", headers,
                            {"pageSize": 200}, "vendors")
    vendor_name = {v["id"]: v.get("name") for v in vendors}
    open_pos = [p for p in pos if p.get("status") not in PO_CLOSED_STATUSES]
    out["purchaseOrders"] = [{
        "id": key(p["id"]), "loc": code,
        "vendor": vendor_name.get(p.get("vendorId")) or "(unknown vendor)",
        "date": p.get("date"), "total": num(p.get("total")), "status": p.get("status"),
    } for p in open_pos]
    out["poTotals"] = {code: len(pos)}

    # ------------------------------------------------------------ marketing / office
    print(f"[{code}] campaigns and calls...")
    campaigns = get_all_pages(f"{BASE_API}/marketing/v2/tenant/{t}/campaigns", headers,
                              {"pageSize": 200}, "campaigns")
    out["campaigns"] = [{"id": key(c["id"]), "loc": code, "name": (c.get("name") or "").strip(),
                         "category": (c.get("category") or {}).get("name")} for c in campaigns]

    calls = get_all_pages(f"{BASE_API}/telecom/v2/tenant/{t}/calls", headers,
                          {"pageSize": 200, "createdOnOrAfter": op_start}, "calls")
    agg = {}
    for c in calls:
        lc = c.get("leadCall") or {}
        when = lc.get("receivedOn") or lc.get("createdOn")
        if not when:
            continue
        k = (when[:10], (lc.get("agent") or {}).get("name") or "",
             key((lc.get("campaign") or {}).get("id")) or "", lc.get("direction") == "Inbound")
        row = agg.setdefault(k, {"n": 0, "booked": 0, "abandoned": 0})
        row["n"] += 1
        if lc.get("callType") == "Booked":
            row["booked"] += 1
        elif lc.get("callType") == "Abandoned":
            row["abandoned"] += 1
    out["calls"] = [{"date": k[0], "loc": code, "agentName": k[1] or None,
                     "campaign": k[2] or None, "inbound": k[3],
                     "n": v["n"], "booked": v["booked"], "abandoned": v["abandoned"]}
                    for k, v in agg.items()]
    out["rawCallCount"] = len(calls)

    out["memberships"] = [{
        "id": key(m["id"]), "loc": code, "status": m.get("status"),
        "type": mtype_name.get(m.get("membershipTypeId")) or "(unknown plan)",
        "from": m.get("from"), "to": m.get("to"), "soldBy": key(m.get("soldById")),
    } for m in memberships]

    done = sum(1 for j in jobs_out if not j["future"])
    booked = [j for j in jobs_out if j["future"]]
    print(f"[{code}] {done} completed, {len(booked)} booked "
          f"(${sum(j['revenue'] for j in booked):,.0f}), {skipped} stale skipped, "
          f"{len(opportunities)} opportunities from {len(estimates)} estimates, "
          f"{len(out['ar'])} open invoices, {len(calls)} calls")
    return out


def main():
    cfg = load_config()
    env = load_env()
    # Resolve every location's credentials before the first request, so a typo
    # in .env fails now rather than partway through a multi-minute pull.
    validate_credentials(env, cfg)
    collections = ("technicians", "jobs", "businessUnits", "opportunities",
                   "ar", "purchaseOrders", "campaigns", "calls", "memberships")
    merged = {k: [] for k in collections}
    people, po_totals, raw_calls, est_count = {}, {}, 0, 0

    for loc in cfg["locations"]:
        res = pull_location(cfg, env, loc)
        for k in collections:
            merged[k] += res[k]
        people.update(res["people"])
        po_totals.update(res["poTotals"])
        raw_calls += res["rawCallCount"]
        est_count += res["estimateCount"]

    # Collapse confirmed duplicate user records onto one canonical name. Records
    # that already share a name merge on their own, because every per-person
    # total is keyed by name rather than by id.
    for pid, canonical in (cfg.get("personAliases") or {}).items():
        if pid in people:
            people[pid] = canonical

    out = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "isDemo": False,
        "people": people, "poTotals": po_totals,
        "rawCallCount": raw_calls, "estimateCount": est_count,
        **merged,
    }
    with open("live_data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print("\nWrote live_data.json")
    for k in collections:
        print(f"   {k}: {len(merged[k])}")
    print("\nNext:  python render_dashboard.py")


if __name__ == "__main__":
    main()
