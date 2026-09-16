"""Pull a snapshot from ServiceTitan for every tenant of a profile.

READ-ONLY: only GET requests. Nothing is ever written back to ServiceTitan.

HOW THE DERIVED NUMBERS ARE BUILT
---------------------------------
These rules were worked out against real ServiceTitan tenants. Several exist
because the obvious approach produces a plausible-looking wrong answer.

IDS ARE NAMESPACED BY TENANT ("SPR:5510"). Separate tenants reuse raw id
numbers, so any raw id used as a key would silently merge two companies' data.

BOOKED ("scheduled") REVENUE reads ``job.total`` first and falls back to the
total of the job's SOLD estimate when the job has not been priced. Each booked
job is dated to its EARLIEST FUTURE appointment, found by scanning all future
appointments rather than trusting ``firstAppointmentId`` (a rescheduled job's
first appointment sits in the past).

STALE OPEN JOBS (Scheduled/Dispatched/InProgress/Hold with no future appointment) are
excluded rather than counted as future revenue; the count is recorded.

ACCOUNTS RECEIVABLE excludes invoices that carry a balance but no invoice
date: ServiceTitan pre-creates an invoice when a job is booked, so those are
unbilled future work, and undated rows would land in the oldest aging bucket.

OPPORTUNITIES, NOT ESTIMATES. A Good/Better/Best presentation writes one
estimate per option for a single chance to sell, so estimates are grouped into
one opportunity per job. An opportunity is WON if any option sold; its value
counts only the options that sold.

WHO OWNS AN OPPORTUNITY: ``soldBy`` on a sold estimate, else the job's
assigned technician. Owners still unresolved are looked up individually.

MEMBERSHIP ATTACH is approximate: same customer, same calendar day.

COMMITTED PO SPEND is not accounts payable; it is unreceived purchase-order
value, labelled that way. Vendor bills do exist in the API (accounting
ap-bills and inventory-bills); they are not part of the snapshot yet.

CALLS are rolled up to one row per (day, agent, campaign, direction); the
dashboard only shows counts, so this is lossless and much smaller.
"""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import __version__
from .api import ServiceTitanClient
from .dates import UTC, iso_z
from .errors import ApiError
from .output import progress as _progress

# Purchase-order statuses: Pending, Sent, PartiallyReceived, Received, Exported, Canceled. Exported follows
# Received (the PO has been pushed to accounting), so it is no longer open commitment.
PO_CLOSED_STATUSES = {"Received", "Exported", "Canceled", "Cancelled"}

# ServiceTitan's `active` filter defaults to active records only. Rosters and
# reference data must include inactive rows: completed jobs still reference
# technicians who have since left, and old invoices reference retired units.
ANY = {"active": "Any"}

# Every endpoint the snapshot pull touches, in the catalogue's path form, so
# `auth check` can compare the scopes a token was granted with what fetch needs.
FETCH_ENDPOINTS = (
    "/settings/v2/tenant/{tenant}/business-units", "/settings/v2/tenant/{tenant}/technicians",
    "/settings/v2/tenant/{tenant}/employees", "/jpm/v2/tenant/{tenant}/job-types",
    "/jpm/v2/tenant/{tenant}/jobs", "/jpm/v2/tenant/{tenant}/appointments",
    "/dispatch/v2/tenant/{tenant}/appointment-assignments", "/sales/v2/tenant/{tenant}/estimates",
    "/crm/v2/tenant/{tenant}/customers", "/memberships/v2/tenant/{tenant}/memberships",
    "/memberships/v2/tenant/{tenant}/membership-types", "/accounting/v2/tenant/{tenant}/invoices",
    "/inventory/v2/tenant/{tenant}/purchase-orders", "/inventory/v2/tenant/{tenant}/vendors",
    "/marketing/v2/tenant/{tenant}/campaigns", "/telecom/v3/tenant/{tenant}/calls",
)


def num(v):
    """ServiceTitan returns money as a float or a decimal string."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def dept_for_business_unit(name, mapping):
    """Keyword match, case-insensitive, first list wins; None means excluded."""
    n = (name or "").lower()
    for kw in mapping.get("exclude", []) or []:
        if kw.lower() in n:
            return None
    for dept, keywords in mapping.items():
        if dept == "exclude":
            continue
        for kw in keywords or []:
            if kw.lower() in n:
                return dept
    return "Other"


def pull_tenant(client, cfg, tenant, op_start, ar_start, max_pages=100, log=None):
    """Snapshot slice for one tenant. Returns a dict of collections plus stats."""
    log = log or (lambda m: None)
    code = tenant["code"]
    mapping = cfg["departmentMapping"]
    low_ticket = cfg["rules"]["lowTicketThreshold"]
    warnings = []

    def key(raw):
        return None if raw is None else "%s:%s" % (code, raw)

    def pages(path, params, label):
        return client.get_all(path, params, max_pages=max_pages, label=label)

    out = {}
    log("[%s] business units, technicians, employees..." % code)
    bus = pages("settings/v2/business-units", ANY, "business-units")
    bu_dept = {b["id"]: dept_for_business_unit(b.get("name"), mapping) for b in bus}
    bu_name = {b["id"]: b.get("name") for b in bus}
    out["businessUnits"] = [{"id": key(b["id"]), "loc": code, "name": b.get("name"), "dept": bu_dept[b["id"]],
                             "active": b.get("active")} for b in bus]

    techs = pages("settings/v2/technicians", ANY, "technicians")
    out["technicians"] = [{"id": key(x["id"]), "name": x.get("name"), "loc": code,
                           "dept": bu_dept.get(x.get("businessUnitId")), "active": x.get("active")} for x in techs]
    employees = pages("settings/v2/employees", ANY, "employees")
    # Technicians and employees are separate resources with independent ids. Job and estimate
    # references (tech, soldBy) point at technicians, so technician names win on any id clash.
    people = {key(x["id"]): x.get("name") for x in techs}
    for x in employees:
        people.setdefault(key(x["id"]), x.get("name"))

    try:
        job_types = pages("jpm/v2/job-types", ANY, "job-types")
    except ApiError as e:
        warnings.append("job types not readable (%s); jobType will be empty" % e.message)
        job_types = []
    jt_name = {j["id"]: j.get("name") for j in job_types}

    log("[%s] jobs..." % code)
    completed_jobs = pages("jpm/v2/jobs", {"jobStatus": "Completed", "completedOnOrAfter": op_start}, "jobs-completed")
    # jobStatus values: Scheduled, Dispatched, InProgress, Hold, Completed, Canceled. Everything but
    # Completed/Canceled is open work; a Dispatched job with a later appointment is still on the board.
    open_jobs = []
    for status in ("Scheduled", "Dispatched", "InProgress", "Hold"):
        open_jobs += pages("jpm/v2/jobs", {"jobStatus": status, "createdOnOrAfter": op_start}, "jobs-%s" % status)

    now_iso = iso_z(dt.datetime.now(UTC))
    future_appts = pages("jpm/v2/appointments", {"startsOnOrAfter": now_iso}, "appointments")
    job_next_appt = {}
    for a in future_appts:
        jid = a.get("jobId")
        if jid is None or not a.get("start"):
            continue
        if jid not in job_next_appt or a["start"] < job_next_appt[jid]:
            job_next_appt[jid] = a["start"]

    assignments = pages("dispatch/v2/appointment-assignments", {"modifiedOnOrAfter": op_start}, "assignments")
    job_tech = {}
    for a in assignments:
        if a.get("jobId") is not None and a.get("technicianId") is not None:
            job_tech.setdefault(a["jobId"], a["technicianId"])
            if a.get("technicianName") and key(a["technicianId"]) not in people:
                people[key(a["technicianId"])] = a["technicianName"]

    log("[%s] sold-estimate fallback for unpriced booked jobs..." % code)
    job_sold_estimate = {}
    for j in open_jobs:
        if j["id"] not in job_next_appt or num(j.get("total")) > 0:
            continue
        for eid in (j.get("estimateIds") or [])[:15]:
            try:
                e = client.get("sales/v2/estimates/%s" % eid)
            except ApiError:
                continue
            if e and (e.get("status") or {}).get("name") == "Sold":
                job_sold_estimate[j["id"]] = job_sold_estimate.get(j["id"], 0) + \
                    sum(num(i.get("total")) for i in (e.get("items") or []))

    log("[%s] customers..." % code)
    customers = pages("crm/v2/customers", ANY, "customers")
    cust_name = {c["id"]: c.get("name") for c in customers}
    cust_city = {c["id"]: (c.get("address") or {}).get("city") for c in customers}

    log("[%s] memberships..." % code)
    memberships = pages("memberships/v2/memberships", ANY, "memberships")
    mtypes = pages("memberships/v2/membership-types", ANY, "membership-types")
    mtype_name = {m["id"]: m.get("name") for m in mtypes}
    membership_customer_days = {(m["customerId"], (m.get("createdOn") or "")[:10])
                                for m in memberships if m.get("customerId")}

    jobs_out = []
    for j in completed_jobs:
        dept = bu_dept.get(j.get("businessUnitId"))
        if dept is None:
            continue
        rev = num(j.get("total"))
        jobs_out.append({
            "id": key(j["id"]), "jobNumber": j.get("jobNumber"), "date": j["completedOn"], "loc": code, "dept": dept,
            "tech": key(job_tech.get(j["id"])), "revenue": rev, "future": False,
            "campaign": key(j.get("campaignId")),
            "membershipSold": (j.get("customerId"), (j.get("completedOn") or "")[:10]) in membership_customer_days,
            "lowTicket": 0 < rev < low_ticket,
            "options": len(j.get("estimateIds") or []),
            "customer": cust_name.get(j.get("customerId")), "businessUnit": bu_name.get(j.get("businessUnitId")),
            "jobType": jt_name.get(j.get("jobTypeId")), "noCharge": bool(j.get("noCharge")), "status": "Completed",
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
            "id": key(j["id"]), "jobNumber": j.get("jobNumber"), "date": start, "loc": code, "dept": dept,
            "tech": key(job_tech.get(j["id"])),
            "revenue": num(j.get("total")) or job_sold_estimate.get(j["id"], 0),
            "future": True, "campaign": key(j.get("campaignId")),
            "membershipSold": False, "lowTicket": False,
            "options": len(j.get("estimateIds") or []),
            "customer": cust_name.get(j.get("customerId")), "businessUnit": bu_name.get(j.get("businessUnitId")),
            "jobType": jt_name.get(j.get("jobTypeId")), "noCharge": bool(j.get("noCharge")),
            "status": j.get("jobStatus"),
        })
    out["jobs"] = jobs_out

    log("[%s] estimates..." % code)
    estimates = pages("sales/v2/estimates", {"createdOnOrAfter": op_start}, "estimates")

    def est_total(e):
        return sum(num(i.get("total")) for i in (e.get("items") or []))

    groups = {}
    for e in estimates:
        groups.setdefault(e.get("jobId") or "est-%s" % e["id"], []).append(e)
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
            "title": ((titled[0].get("name") or "").strip() or None) if titled else None,
            "presentedOn": created[0] if created else None,
            "wonOn": sold_on[0] if sold_on else None,
            "won": bool(sold), "options": len(group), "owner": key(owner),
            "value": sum(est_total(e) for e in sold),
            "topOption": max((est_total(e) for e in group), default=0.0),
        })
    for pid in {o["owner"] for o in opportunities if o["owner"] and o["owner"] not in people}:
        raw = pid.split(":", 1)[1]
        for endpoint in ("settings/v2/technicians/%s", "settings/v2/employees/%s"):
            try:
                rec = client.get(endpoint % raw)
            except ApiError:
                rec = None
            if rec and rec.get("name"):
                people[pid] = rec["name"]
                break
        else:
            warnings.append("owner %s could not be resolved to a name" % pid)
    out["opportunities"] = opportunities
    out["estimateCount"] = len(estimates)
    out["people"] = people

    log("[%s] invoices and purchase orders..." % code)
    # `invoicedOnOrAfter` is the documented date filter (the template used an undocumented name and pulled
    # everything); `balanceFilter` keeps the pull to invoices that still carry a balance, which is all AR needs.
    invoices = pages("accounting/v2/invoices", {"invoicedOnOrAfter": ar_start, "balanceFilter.Balance": 0,
                                                "balanceFilter.Comparer": "Greater"}, "invoices")
    unbilled = [i for i in invoices if num(i.get("balance")) > 0 and not i.get("invoiceDate")]
    out["ar"] = [{
        "id": key(i["id"]), "loc": code, "invoiceNumber": i.get("referenceNumber"),
        "customer": (i.get("customer") or {}).get("name") or "(no customer)",
        "invoiceDate": i.get("invoiceDate"), "dueDate": i.get("dueDate"),
        "balance": num(i.get("balance")), "total": num(i.get("total")),
        "businessUnit": (i.get("businessUnit") or {}).get("name"),
    } for i in invoices if num(i.get("balance")) > 0 and i.get("invoiceDate")]
    unbilled_total = sum(num(i.get("balance")) for i in unbilled)
    if unbilled:
        log("   [%s] %d unbilled invoice(s) excluded from AR ($%s of not-yet-issued work)"
            % (code, len(unbilled), "{:,.0f}".format(unbilled_total)))

    pos = pages("inventory/v2/purchase-orders", {}, "purchase-orders")
    vendors = pages("inventory/v2/vendors", ANY, "vendors")
    vendor_name = {v["id"]: v.get("name") for v in vendors}
    open_pos = [p for p in pos if p.get("status") not in PO_CLOSED_STATUSES]
    out["purchaseOrders"] = [{
        "id": key(p["id"]), "loc": code, "number": p.get("number"),
        "vendor": vendor_name.get(p.get("vendorId")) or "(unknown vendor)",
        "date": p.get("date"), "total": num(p.get("total")), "status": p.get("status"),
    } for p in open_pos]
    out["poTotals"] = {code: len(pos)}

    log("[%s] campaigns and calls..." % code)
    campaigns = pages("marketing/v2/campaigns", ANY, "campaigns")
    out["campaigns"] = [{"id": key(c["id"]), "loc": code, "name": (c.get("name") or "").strip(),
                         "category": (c.get("category") or {}).get("name"), "active": c.get("active")} for c in campaigns]
    # The v2 calls list is tagged deprecated; v3 returns the same {id, jobNumber, leadCall} wrapper.
    # callType is one of Abandoned, Unbooked, Excused, Booked, NotLead (null on outbound calls).
    calls = pages("telecom/v3/calls", {"createdOnOrAfter": op_start, "active": "Any"}, "calls")
    agg = {}
    for c in calls:
        lc = c.get("leadCall") or {}
        when = lc.get("receivedOn") or lc.get("createdOn")
        if not when:
            continue
        k = (when[:10], (lc.get("agent") or {}).get("name") or "",
             key((lc.get("campaign") or {}).get("id")) or "", lc.get("direction") == "Inbound")
        row = agg.setdefault(k, {"n": 0, "booked": 0, "abandoned": 0, "unbooked": 0, "excused": 0, "notLead": 0})
        row["n"] += 1
        ct = lc.get("callType")
        if ct == "Booked":
            row["booked"] += 1
        elif ct == "Abandoned":
            row["abandoned"] += 1
        elif ct == "Unbooked":
            row["unbooked"] += 1
        elif ct == "Excused":
            row["excused"] += 1
        elif ct == "NotLead":
            row["notLead"] += 1
    out["calls"] = [{"date": k[0], "loc": code, "agentName": k[1] or None, "campaign": k[2] or None,
                     "inbound": k[3], "n": v["n"], "booked": v["booked"], "abandoned": v["abandoned"],
                     "unbooked": v["unbooked"], "excused": v["excused"], "notLead": v["notLead"]}
                    for k, v in agg.items()]
    out["rawCallCount"] = len(calls)

    out["memberships"] = [{
        "id": key(m["id"]), "loc": code, "status": m.get("status"),
        "type": mtype_name.get(m.get("membershipTypeId")) or "(unknown plan)",
        "customer": cust_name.get(m.get("customerId")),
        "from": m.get("from"), "to": m.get("to"), "soldBy": key(m.get("soldById")),
    } for m in memberships]

    done = sum(1 for j in jobs_out if not j["future"])
    booked = [j for j in jobs_out if j["future"]]
    out["stats"] = {
        "completedJobs": done, "bookedJobs": len(booked), "bookedRevenue": round(sum(j["revenue"] for j in booked), 2),
        "staleOpenJobsSkipped": skipped, "opportunities": len(opportunities), "estimates": len(estimates),
        "openInvoices": len(out["ar"]), "unbilledInvoicesExcluded": len(unbilled),
        "unbilledBalanceExcluded": round(unbilled_total, 2), "purchaseOrders": len(pos), "openPurchaseOrders": len(open_pos),
        "calls": len(calls), "memberships": len(memberships), "customers": len(customers),
        "requests": client.requests_made, "warnings": warnings,
    }
    log("[%s] %d completed, %d booked ($%s), %d stale skipped, %d opportunities from %d estimates, %d open invoices, %d calls"
        % (code, done, len(booked), "{:,.0f}".format(out["stats"]["bookedRevenue"]), skipped, len(opportunities),
           len(estimates), len(out["ar"]), len(calls)))
    return out


def build_snapshot(ctx, tenants=None, max_pages=100, page_size=None, quiet=False, client_factory=None, workers=4):
    """Pull every (or the selected) tenant and merge into one snapshot dict.

    Tenants are pulled concurrently (``workers`` at a time): ServiceTitan's
    rate limit is per application *per tenant*, so parallel tenants do not
    compete for one budget. Credentials are resolved up front, before any
    thread starts, so keyring access stays on the main thread.
    """
    cfg = ctx.config
    tenants = tenants or ctx.tenants
    log = (lambda m: None) if quiet else (lambda m: _progress(m))
    creds = {t["code"]: ctx.credentials_for(t) for t in tenants}
    if len(tenants) == len(ctx.tenants):
        ctx.validate_credentials()
    op_start, ar_start = ctx.window_starts()
    collections = ("technicians", "jobs", "businessUnits", "opportunities", "ar", "purchaseOrders",
                   "campaigns", "calls", "memberships")
    merged = {k: [] for k in collections}
    people, po_totals, raw_calls, est_count, pulls = {}, {}, 0, 0, {}

    def pull_one(t):
        c = creds[t["code"]]
        client = (client_factory or ServiceTitanClient)(c, page_size=page_size or 200, progress=log)
        log("[%s] authenticating (%s, tenant %s)..." % (c.code, c.environment, c.tenant_id))
        return pull_tenant(client, cfg, t, op_start, ar_start, max_pages=max_pages, log=log)

    if len(tenants) > 1 and workers and workers > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(tenants))) as pool:
            results = list(pool.map(pull_one, tenants))
    else:
        results = [pull_one(t) for t in tenants]
    for t, res in zip(tenants, results):
        for k in collections:
            merged[k] += res[k]
        people.update(res["people"])
        po_totals.update(res["poTotals"])
        raw_calls += res["rawCallCount"]
        est_count += res["estimateCount"]
        pulls[t["code"]] = res["stats"]
    for pid, canonical in (cfg.get("personAliases") or {}).items():
        if pid in people:
            people[pid] = canonical
    snapshot = {
        "generatedAt": iso_z(dt.datetime.now(UTC)),
        "isDemo": False,
        "profile": ctx.profile_name,
        "environment": ctx.environment,
        "timezone": cfg.get("timezone") or "UTC",
        "tenants": [{"code": t["code"], "label": t.get("label"), "tenantId": t.get("tenantId")} for t in tenants],
        "window": {"operationalStart": op_start, "arStart": ar_start},
        "cli": {"version": __version__},
        "people": people, "poTotals": po_totals,
        "rawCallCount": raw_calls, "estimateCount": est_count,
        "pulls": pulls,
    }
    snapshot.update(merged)
    return snapshot
