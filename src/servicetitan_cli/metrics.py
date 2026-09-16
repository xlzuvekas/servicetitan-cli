"""Derived operations metrics: a faithful port of the dashboard's eight tabs.

Every number here is computed from a snapshot exactly the way the HTML
dashboard computes it, so an agent quoting ``servicetitan metrics service``
and a person reading the rendered page see the same figures.

Range and tenant filters mirror the dashboard's toggles. ``as_of`` is the
clock (defaults to now in the profile's timezone) so results are
reproducible with ``--as-of``.
"""
import datetime as dt
from collections import defaultdict

from .dates import (bucket_date, day_key, forward_range, granularity, previous_comparable_range, range_bounds,
                    start_of_day, end_of_day, parse_iso)

DEPTS = ("Service", "Install", "Sales")
AR_BUCKETS = ("0-30", "31-60", "61-90", "91-120", "120+")
AR_BUCKET_STATUS = {"0-30": "good", "31-60": "warning", "61-90": "serious", "91-120": "serious", "120+": "critical"}
AREAS = ("revenue", "service", "sales", "install", "accounting", "marketing", "office", "memberships")


def ar_bucket(days):
    if days <= 30:
        return "0-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    if days <= 120:
        return "91-120"
    return "120+"


def r2(x):
    return round(float(x or 0), 2)


def pct(n, d):
    return round(n / d * 100.0, 1) if d else 0.0


def div(n, d):
    return r2(n / d) if d else 0.0


class Query(object):
    """A snapshot plus the filters of one question."""

    def __init__(self, snapshot, as_of, tz, range_kind="mtd", custom_start=None, custom_end=None, tenant=None,
                 targets=None, rules=None):
        self.snapshot = snapshot
        self.data = snapshot.data
        self.tz = tz
        self.as_of = as_of.astimezone(tz)
        self.range_kind = (range_kind or "mtd").lower()
        self.custom_start = custom_start
        self.custom_end = custom_end
        self.tenant = None if not tenant or str(tenant).lower() == "all" else str(tenant).upper()
        self.targets = targets or {}
        self.rules = rules or {}
        self.board_days = int(self.rules.get("boardDays") or 45)
        self.start, self.end = range_bounds(self.range_kind, self.as_of, custom_start, custom_end)
        self.people = self.data.get("people") or {}
        self._prepare()

    # ------------------------------------------------------------ parsing
    def _dt(self, s):
        d = parse_iso(s, tz=self.tz)
        return d.astimezone(self.tz) if d else None

    def _prepare(self):
        self.jobs = []
        for j in self.data.get("jobs") or []:
            d = self._dt(j.get("date"))
            if d is None:
                continue
            self.jobs.append(dict(j, dt=d, revenue=float(j.get("revenue") or 0), options=int(j.get("options") or 0)))
        self.opportunities = []
        for o in self.data.get("opportunities") or []:
            self.opportunities.append(dict(o, presented_dt=self._dt(o.get("presentedOn")), won_dt=self._dt(o.get("wonOn")),
                                           value=float(o.get("value") or 0), top_option=float(o.get("topOption") or 0),
                                           won=bool(o.get("won")), options=int(o.get("options") or 0)))
        self.ar = []
        for i in self.data.get("ar") or []:
            basis = self._dt(i.get("dueDate") or i.get("invoiceDate"))
            if basis is None:
                continue
            days = max(0, int((self.as_of - basis).total_seconds() // 86400))
            self.ar.append(dict(i, basis_dt=basis, days_ago=days, amount=float(i.get("balance") or 0)))
        self.purchase_orders = [dict(p, date_dt=self._dt(p.get("date")), amount=float(p.get("total") or 0))
                                for p in self.data.get("purchaseOrders") or []]
        self.calls = []
        for c in self.data.get("calls") or []:
            d = parse_iso(c.get("date"), tz=self.tz)
            if d is None:
                continue
            self.calls.append(dict(c, dt=d.replace(hour=12), n=int(c.get("n") or 0), booked=int(c.get("booked") or 0),
                                   abandoned=int(c.get("abandoned") or 0), unbooked=int(c.get("unbooked") or 0),
                                   excused=int(c.get("excused") or 0), inbound=bool(c.get("inbound"))))
        self.memberships = [dict(m, start_dt=self._dt(m.get("from")), renew_dt=self._dt(m.get("to")),
                                 active=(m.get("status") == "Active")) for m in self.data.get("memberships") or []]
        self.technicians = self.data.get("technicians") or []
        self.campaigns = self.data.get("campaigns") or []
        self.campaign_by_id = {c["id"]: c for c in self.campaigns}
        counts = defaultdict(int)
        for c in self.campaigns:
            counts[(c.get("name") or "").strip()] += 1
        self._campaign_name_counts = counts

    # ------------------------------------------------------------ helpers
    def loc_match(self, loc):
        return self.tenant is None or loc == self.tenant

    def in_range(self, d, start=None, end=None):
        start = self.start if start is None else start
        end = self.end if end is None else end
        return d is not None and start <= d <= end

    def person_name(self, pid):
        if pid is None:
            return "Unassigned"
        return self.people.get(pid) or pid

    def campaign_name(self, cid):
        c = self.campaign_by_id.get(cid)
        if not c:
            return "No campaign"
        n = (c.get("name") or "").strip() or "(unnamed)"
        return "%s (%s)" % (n, c.get("loc")) if self._campaign_name_counts.get(n, 0) > 1 else n

    def jobs_in_range(self, start=None, end=None, dept=None):
        return [j for j in self.jobs if not j.get("future") and self.in_range(j["dt"], start, end)
                and self.loc_match(j.get("loc")) and (dept is None or j.get("dept") == dept)]

    def scheduled_jobs(self, end=None, dept=None):
        return [j for j in self.jobs if j.get("future") and j["dt"] > self.as_of and (end is None or j["dt"] <= end)
                and self.loc_match(j.get("loc")) and (dept is None or j.get("dept") == dept)]

    def ytd_range(self):
        return start_of_day(self.as_of.replace(month=1, day=1)), self.as_of

    def ytd_jobs(self, dept=None):
        a, b = self.ytd_range()
        return self.jobs_in_range(a, b, dept)

    def board_end(self):
        return self.as_of + dt.timedelta(days=self.board_days)

    def forward(self):
        end, label = forward_range(self.range_kind, self.as_of, self.custom_end)
        return end, label

    def annualised_run_rate(self, total):
        y = self.as_of.year
        start = start_of_day(self.as_of.replace(month=1, day=1))
        end = end_of_day(self.as_of.replace(month=12, day=31))
        elapsed = max(1.0, (self.as_of - start).total_seconds())
        return r2(total * (end - start).total_seconds() / elapsed)

    def rest_of_period(self, dept=None):
        end, label = self.forward()
        ended = end <= self.as_of
        rest = [] if ended else self.scheduled_jobs(end, dept)
        return {"label": "Rest of %s" % label, "through": day_key(end), "ended": ended,
                "revenue": r2(sum(j["revenue"] for j in rest)), "jobs": len(rest)}

    def board(self, dept=None):
        end = self.board_end()
        rows = self.scheduled_jobs(end, dept)
        return {"days": self.board_days, "through": day_key(end), "revenue": r2(sum(j["revenue"] for j in rows)),
                "jobs": len(rows)}

    def run_rate(self, completed_total):
        end, label = self.forward()
        elapsed = max(1.0, (self.as_of - self.start).total_seconds())
        full = max(elapsed, (end - self.start).total_seconds())
        done = end <= self.as_of
        return {"period": label, "value": r2(completed_total if done else completed_total * full / elapsed),
                "pctElapsed": round(min(100.0, elapsed / full * 100.0)), "periodComplete": done}

    def header(self, area):
        end, label = self.forward()
        return {
            "area": area,
            "source": "snapshot",
            "profile": self.data.get("profile"),
            "tenant": self.tenant or "all",
            "asOf": self.as_of.isoformat(),
            "timezone": str(self.tz),
            "range": {"kind": self.range_kind, "start": self.start.isoformat(), "end": self.end.isoformat(),
                      "period": label},
            "snapshot": self.snapshot.describe(as_of=self.as_of),
        }


# ---------------------------------------------------------------- areas
def revenue(q):
    jobs = q.jobs_in_range()
    completed = sum(j["revenue"] for j in jobs)
    p_start, p_end = previous_comparable_range(q.start, q.end)
    prev_jobs = q.jobs_in_range(p_start, p_end)
    prev_completed = sum(j["revenue"] for j in prev_jobs)
    has_prior = len(prev_jobs) > 0 and prev_completed > 0
    billed = [j for j in jobs if j["revenue"] > 0]
    gran = granularity(q.start, q.end)
    by_bucket = defaultdict(lambda: defaultdict(float))
    for j in jobs:
        by_bucket[day_key(bucket_date(j["dt"], gran))][j.get("dept") or "Other"] += j["revenue"]
    series = []
    for k in sorted(by_bucket):
        row = {"date": k}
        for dept in DEPTS:
            row[dept] = r2(by_bucket[k].get(dept, 0))
        if by_bucket[k].get("Other"):
            row["Other"] = r2(by_bucket[k]["Other"])
        row["total"] = r2(sum(by_bucket[k].values()))
        series.append(row)
    by_person = defaultdict(float)
    for j in jobs:
        if j.get("tech") and j["revenue"] > 0:
            by_person[q.person_name(j["tech"])] += j["revenue"]
    top = sorted(by_person.items(), key=lambda kv: -kv[1])[:10]
    ytd_jobs = q.ytd_jobs()
    ytd_rev = sum(j["revenue"] for j in ytd_jobs)
    ytd_billed = [j for j in ytd_jobs if j["revenue"] > 0]
    out = q.header("revenue")
    out.update({
        "kpis": {
            "completedRevenue": r2(completed),
            "deltaVsPriorPeriodPct": round((completed - prev_completed) / prev_completed * 100.0, 1) if has_prior else None,
            "priorPeriod": {"start": day_key(p_start), "end": day_key(p_end), "completedRevenue": r2(prev_completed),
                            "jobs": len(prev_jobs)} if has_prior else None,
            "jobsCompleted": len(jobs), "billedJobs": len(billed), "noChargeOrSalesVisits": len(jobs) - len(billed),
            "avgBilledJob": div(completed, len(billed)), "avgAllJobs": div(completed, len(jobs)),
            "restOfPeriod": q.rest_of_period(), "runRate": q.run_rate(completed), "board": q.board(),
        },
        "ytd": {"completedRevenue": r2(ytd_rev), "jobsCompleted": len(ytd_jobs),
                "avgBilledJob": div(sum(j["revenue"] for j in ytd_billed), len(ytd_billed)),
                "annualizedRunRate": q.annualised_run_rate(ytd_rev)},
        "revenueOverTime": {"granularity": gran, "points": series},
        "revenueByIndividual": [{"name": n, "revenue": r2(v)} for n, v in top],
    })
    return out


def service(q):
    jobs = q.jobs_in_range(dept="Service")
    completed = sum(j["revenue"] for j in jobs)
    billed = [j for j in jobs if j["revenue"] > 0]
    avg_ticket = div(completed, len(billed))
    membership_rate = pct(sum(1 for j in jobs if j.get("membershipSold")), len(jobs))
    low_rate = pct(sum(1 for j in billed if j.get("lowTicket")), len(billed))
    options_per_call = round(sum(j["options"] for j in jobs) / len(jobs), 2) if jobs else 0.0
    by_tech = {}
    for j in jobs:
        t = by_tech.setdefault(q.person_name(j.get("tech")), {"revenue": 0.0, "jobs": 0, "billed": 0, "low": 0,
                                                                "memberships": 0, "options": 0})
        t["revenue"] += j["revenue"]
        t["jobs"] += 1
        if j["revenue"] > 0:
            t["billed"] += 1
            t["low"] += 1 if j.get("lowTicket") else 0
        t["memberships"] += 1 if j.get("membershipSold") else 0
        t["options"] += j["options"]
    rows = [{"technician": n, "calls": t["jobs"], "billed": t["billed"], "revenue": r2(t["revenue"]),
             "avgTicket": div(t["revenue"], t["billed"]), "lowTicketPct": pct(t["low"], t["billed"]),
             "membershipPct": pct(t["memberships"], t["jobs"]),
             "optionsPerCall": round(t["options"] / t["jobs"], 2) if t["jobs"] else 0.0}
            for n, t in by_tech.items()]
    rows.sort(key=lambda r: -r["revenue"])
    y_jobs = q.ytd_jobs("Service")
    y_rev = sum(j["revenue"] for j in y_jobs)
    y_billed = [j for j in y_jobs if j["revenue"] > 0]
    targets = {}
    tg = q.targets or {}
    if tg.get("avgTicket"):
        targets["avgTicket"] = {"target": tg["avgTicket"], "actual": avg_ticket, "met": avg_ticket >= tg["avgTicket"],
                                "higherIsBetter": True}
    if tg.get("membershipAttachPct"):
        targets["membershipAttachPct"] = {"target": tg["membershipAttachPct"], "actual": membership_rate,
                                          "met": membership_rate >= tg["membershipAttachPct"], "higherIsBetter": True}
    if tg.get("lowTicketPct"):
        targets["lowTicketPct"] = {"target": tg["lowTicketPct"], "actual": low_rate, "met": low_rate <= tg["lowTicketPct"],
                                   "higherIsBetter": False}
    if tg.get("optionsPerCall"):
        targets["optionsPerCall"] = {"target": tg["optionsPerCall"], "actual": options_per_call,
                                     "met": options_per_call >= tg["optionsPerCall"], "higherIsBetter": True}
    out = q.header("service")
    out.update({
        "kpis": {
            "completedRevenue": r2(completed), "callsCompleted": len(jobs), "billedCalls": len(billed),
            "noChargeCalls": len(jobs) - len(billed), "avgTicket": avg_ticket,
            "revenuePerDispatch": div(completed, len(jobs)), "membershipAttachPct": membership_rate,
            "lowTicketPct": low_rate, "optionsPerCall": options_per_call,
            "restOfPeriod": q.rest_of_period("Service"), "runRate": q.run_rate(completed), "board": q.board("Service"),
        },
        "targets": targets,
        "ytd": {"completedRevenue": r2(y_rev), "callsCompleted": len(y_jobs),
                "avgTicket": div(sum(j["revenue"] for j in y_billed), len(y_billed)),
                "membershipAttachPct": pct(sum(1 for j in y_jobs if j.get("membershipSold")), len(y_jobs)),
                "annualizedRunRate": q.annualised_run_rate(y_rev)},
        "technicians": rows,
    })
    return out


def sales(q):
    cohort = [o for o in q.opportunities if o["presented_dt"] is not None and q.in_range(o["presented_dt"]) and q.loc_match(o.get("loc"))]
    cohort_won = [o for o in cohort if o["won"]]
    cohort_open = [o for o in cohort if not o["won"]]
    cohort_value = sum(o["value"] for o in cohort_won)
    won_now = [o for o in q.opportunities if o["won_dt"] is not None and q.in_range(o["won_dt"]) and q.loc_match(o.get("loc"))]
    booked_value = sum(o["value"] for o in won_now)
    open_pipeline = sum(o["top_option"] for o in cohort_open)
    y_start, y_end = q.ytd_range()
    y_cohort = [o for o in q.opportunities if o["presented_dt"] is not None and q.in_range(o["presented_dt"], y_start, y_end) and q.loc_match(o.get("loc"))]
    y_won = [o for o in y_cohort if o["won"]]
    y_won_value = sum(o["value"] for o in y_won)
    y_booked = sum(o["value"] for o in q.opportunities if o["won_dt"] is not None and q.in_range(o["won_dt"], y_start, y_end) and q.loc_match(o.get("loc")))
    by_rep = {}

    def bucket(name):
        return by_rep.setdefault(name, {"run": 0, "won": 0, "value": 0.0, "ytdRun": 0, "ytdWon": 0, "ytdValue": 0.0})
    for o in cohort:
        bucket(q.person_name(o.get("owner")))["run"] += 1
    for o in cohort_won:
        b = bucket(q.person_name(o.get("owner")))
        b["won"] += 1
        b["value"] += o["value"]
    for o in y_cohort:
        bucket(q.person_name(o.get("owner")))["ytdRun"] += 1
    for o in y_won:
        b = bucket(q.person_name(o.get("owner")))
        b["ytdWon"] += 1
        b["ytdValue"] += o["value"]
    reps = [{"rep": n, "presented": b["run"], "won": b["won"], "closeRatePct": pct(b["won"], b["run"]),
             "avgTicket": div(b["value"], b["won"]), "revenue": r2(b["value"]),
             "ytdPresented": b["ytdRun"], "ytdWon": b["ytdWon"], "ytdRevenue": r2(b["ytdValue"])}
            for n, b in by_rep.items() if n != "Unassigned"]
    reps.sort(key=lambda r: (-r["revenue"], -r["ytdRevenue"]))
    unassigned = by_rep.get("Unassigned")
    out = q.header("sales")
    out.update({
        "kpis": {
            "bookedValue": r2(booked_value), "wonInPeriod": len(won_now),
            "opportunitiesPresented": len(cohort), "optionsPresented": sum(o["options"] for o in cohort),
            "presentedAndWon": len(cohort_won), "presentedAndWonValue": r2(cohort_value),
            "closeRatePct": pct(len(cohort_won), len(cohort)), "avgSoldTicket": div(cohort_value, len(cohort_won)),
            "stillOpen": len(cohort_open), "stillOpenValueAtBestOption": r2(open_pipeline),
        },
        "ytd": {"bookedValue": r2(y_booked), "presented": len(y_cohort), "won": len(y_won),
                "closeRatePct": pct(len(y_won), len(y_cohort)), "avgSoldTicket": div(y_won_value, len(y_won)),
                "annualizedRunRate": q.annualised_run_rate(y_booked)},
        "reps": reps,
        "unassignedOpportunities": {"presented": unassigned["run"], "won": unassigned["won"],
                                    "revenue": r2(unassigned["value"])} if unassigned else None,
    })
    return out


def install(q):
    jobs = q.jobs_in_range(dept="Install")
    scheduled = q.scheduled_jobs(q.board_end(), "Install")
    completed = sum(j["revenue"] for j in jobs)
    by_tech = {}

    def bucket(name):
        return by_tech.setdefault(name, {"completed": 0.0, "scheduled": 0.0})
    for t in q.technicians:
        if t.get("dept") == "Install" and q.loc_match(t.get("loc")):
            bucket(q.person_name(t["id"]))
    for j in jobs:
        bucket(q.person_name(j.get("tech")))["completed"] += j["revenue"]
    for j in scheduled:
        bucket(q.person_name(j.get("tech")))["scheduled"] += j["revenue"]
    rows = [{"installer": n, "completed": r2(v["completed"]), "scheduled": r2(v["scheduled"])} for n, v in by_tech.items()]
    rows.sort(key=lambda r: -(r["completed"] + r["scheduled"]))
    y_jobs = q.ytd_jobs("Install")
    y_rev = sum(j["revenue"] for j in y_jobs)
    y_billed = [j for j in y_jobs if j["revenue"] > 0]
    out = q.header("install")
    out.update({
        "kpis": {"completedRevenue": r2(completed), "installsCompleted": len(jobs),
                 "avgInstallTicket": div(completed, len(jobs)),
                 "restOfPeriod": q.rest_of_period("Install"), "runRate": q.run_rate(completed), "board": q.board("Install")},
        "ytd": {"completedRevenue": r2(y_rev), "installsCompleted": len(y_jobs),
                "avgInstallTicket": div(sum(j["revenue"] for j in y_billed), len(y_billed)),
                "annualizedRunRate": q.annualised_run_rate(y_rev)},
        "installers": rows,
    })
    return out


def accounting(q):
    pos = [p for p in q.purchase_orders if q.loc_match(p.get("loc"))]
    po_total = sum(p["amount"] for p in pos)
    all_po_count = sum(n for loc, n in (q.data.get("poTotals") or {}).items() if q.loc_match(loc))
    ar = [a for a in q.ar if q.loc_match(a.get("loc"))]
    ar_total = sum(a["amount"] for a in ar)
    overdue = [a for a in ar if a["days_ago"] > 30]
    overdue_total = sum(a["amount"] for a in overdue)
    buckets = []
    for b in AR_BUCKETS:
        rows = [a for a in ar if ar_bucket(a["days_ago"]) == b]
        buckets.append({"bucket": b, "status": AR_BUCKET_STATUS[b], "invoices": len(rows),
                        "amount": r2(sum(a["amount"] for a in rows))})
    by_customer = {}
    for a in ar:
        c = by_customer.setdefault(a.get("customer"), {"amount": 0.0, "invoices": 0, "oldest": 0})
        c["amount"] += a["amount"]
        c["invoices"] += 1
        c["oldest"] = max(c["oldest"], a["days_ago"])
    customers = [{"customer": n, "invoices": c["invoices"], "oldestDaysPastDue": c["oldest"],
                  "bucket": ar_bucket(c["oldest"]), "balance": r2(c["amount"])} for n, c in by_customer.items()]
    customers.sort(key=lambda r: -r["balance"])
    by_vendor = defaultdict(lambda: {"amount": 0.0, "orders": 0})
    for p in pos:
        by_vendor[p.get("vendor")]["amount"] += p["amount"]
        by_vendor[p.get("vendor")]["orders"] += 1
    vendors = sorted([{"vendor": v, "openOrders": d["orders"], "committed": r2(d["amount"])} for v, d in by_vendor.items()],
                     key=lambda r: -r["committed"])
    out = q.header("accounting")
    out.update({
        "kpis": {"totalAR": r2(ar_total), "openInvoices": len(ar), "arPast30Days": r2(overdue_total),
                 "invoicesPast30Days": len(overdue), "arPast30Pct": pct(overdue_total, ar_total),
                 "committedOnOpenPOs": r2(po_total), "openPOs": len(pos), "allPOs": all_po_count},
        "arAging": buckets,
        "arByCustomer": customers[:50],
        "openPOsByVendor": vendors[:25],
    })
    return out


def marketing(q):
    jobs = q.jobs_in_range()
    calls = [c for c in q.calls if c["inbound"] and q.in_range(c["dt"]) and q.loc_match(c.get("loc"))]
    agg = {}

    def bucket(cid):
        return agg.setdefault(cid, {"calls": 0, "booked": 0, "jobs": 0, "revenue": 0.0})
    for c in calls:
        b = bucket(c.get("campaign"))
        b["calls"] += c["n"]
        b["booked"] += c["booked"]
    for j in jobs:
        b = bucket(j.get("campaign"))
        b["jobs"] += 1
        b["revenue"] += j["revenue"]
    rows = []
    for cid, a in agg.items():
        c = q.campaign_by_id.get(cid) or {}
        rows.append({"campaign": q.campaign_name(cid), "category": c.get("category"), "tenant": c.get("loc"),
                     "inboundCalls": a["calls"], "bookedCalls": a["booked"], "bookRatePct": pct(a["booked"], a["calls"]),
                     "jobs": a["jobs"], "revenue": r2(a["revenue"])})
    rows.sort(key=lambda r: -r["revenue"])
    total_rev = sum(r["revenue"] for r in rows)
    total_calls = sum(r["inboundCalls"] for r in rows)
    total_jobs = sum(r["jobs"] for r in rows)
    out = q.header("marketing")
    out.update({
        "kpis": {"attributedRevenue": r2(total_rev), "campaignsProducing": sum(1 for r in rows if r["revenue"] > 0),
                 "inboundCalls": total_calls, "jobsGenerated": total_jobs, "revenuePerJob": div(total_rev, total_jobs)},
        "campaigns": rows,
    })
    return out


def office(q):
    allc = [c for c in q.calls if q.in_range(c["dt"]) and q.loc_match(c.get("loc"))]
    inbound = [c for c in allc if c["inbound"]]
    inbound_n = sum(c["n"] for c in inbound)
    outbound = sum(c["n"] for c in allc if not c["inbound"])
    booked = sum(c["booked"] for c in inbound)
    abandoned = sum(c["abandoned"] for c in inbound)
    unbooked = sum(c["unbooked"] for c in inbound)
    excused = sum(c["excused"] for c in inbound)
    gran = granularity(q.start, q.end)
    by_bucket = defaultdict(lambda: {"calls": 0, "booked": 0, "abandoned": 0})
    for c in inbound:
        k = day_key(bucket_date(c["dt"], gran))
        by_bucket[k]["calls"] += c["n"]
        by_bucket[k]["booked"] += c["booked"]
        by_bucket[k]["abandoned"] += c["abandoned"]
    series = [dict(date=k, **by_bucket[k]) for k in sorted(by_bucket)]
    by_agent = {}
    for c in inbound:
        answered = c["n"] - c["abandoned"]
        if not c.get("agentName") or answered <= 0:
            continue
        a = by_agent.setdefault(c["agentName"], {"calls": 0, "booked": 0})
        a["calls"] += answered
        a["booked"] += c["booked"]
    agents = [{"agent": n, "answered": a["calls"], "booked": a["booked"], "bookRatePct": pct(a["booked"], a["calls"])}
              for n, a in by_agent.items()]
    agents.sort(key=lambda r: -r["answered"])
    unanswered = sum(c["n"] for c in inbound if not c.get("agentName"))
    out = q.header("office")
    out.update({
        "kpis": {"inboundCalls": inbound_n, "outboundCalls": outbound, "booked": booked,
                 "bookRatePct": pct(booked, inbound_n), "abandoned": abandoned, "abandonRatePct": pct(abandoned, inbound_n),
                 "unbooked": unbooked, "excused": excused,
                 "bookRateOfBookableCallsPct": pct(booked, booked + unbooked) if (booked + unbooked) else None,
                 "inboundReachedNoAgent": unanswered},
        "callVolume": {"granularity": gran, "points": series},
        "agents": agents,
    })
    return out


def memberships(q):
    rows = [m for m in q.memberships if q.loc_match(m.get("loc"))]
    active = [m for m in rows if m["active"]]
    plan_counts = defaultdict(int)
    for m in active:
        plan_counts[m.get("type")] += 1
    by_plan = sorted([{"plan": p, "members": n} for p, n in plan_counts.items()], key=lambda r: -r["members"])

    def days_out(m):
        return (m["renew_dt"] - q.as_of).total_seconds() / 86400.0 if m["renew_dt"] else None
    ren30 = [m for m in active if days_out(m) is not None and 0 <= days_out(m) <= 30]
    ren90 = [m for m in active if days_out(m) is not None and 0 <= days_out(m) <= 90]
    expired = [m for m in rows if days_out(m) is not None and days_out(m) < 0]
    sold = [m for m in rows if m["start_dt"] is not None and q.in_range(m["start_dt"])]
    upcoming = sorted(ren90, key=lambda m: m["renew_dt"])
    out = q.header("memberships")
    out.update({
        "kpis": {"activeMembers": len(active), "soldInPeriod": len(sold), "renewalsDue30d": len(ren30),
                 "renewalsDue90d": len(ren90), "pastRenewalDate": len(expired)},
        "activeByPlan": by_plan,
        "upcomingRenewals": [{"plan": m.get("type"), "tenant": m.get("loc"), "customer": m.get("customer"),
                              "soldBy": q.person_name(m.get("soldBy")), "renews": day_key(m["renew_dt"])}
                             for m in upcoming[:100]],
    })
    return out


AREA_FUNCS = {"revenue": revenue, "service": service, "sales": sales, "install": install,
              "accounting": accounting, "marketing": marketing, "office": office, "memberships": memberships}

DEFINITIONS = {
    "revenue": {
        "completedRevenue": "Sum of job.total over jobs completed in the range (completedOn), excluding business units mapped to 'exclude'.",
        "deltaVsPriorPeriodPct": "Change versus the same-length window immediately before the range; null when that window has no completed revenue in the snapshot (e.g. YTD).",
        "avgBilledJob": "Completed revenue divided by jobs that billed more than $0. Warranty/callback work and sales visits bill nothing and would drag the average down, so they are excluded here and counted in noChargeOrSalesVisits.",
        "restOfPeriod": "Revenue on booked jobs (job.total, else the sold estimate) dated by their next future appointment, from now to the end of the period the range names.",
        "board": "Booked revenue from now through the rolling boardDays window, independent of the range.",
        "runRate": "Completed revenue scaled by the share of the period elapsed; equals the actual once the period has closed. Based on completed work only, so it never double-counts the booked tiles.",
        "ytd.annualizedRunRate": "Year-to-date completed revenue scaled by the share of the calendar year elapsed.",
    },
    "service": {
        "avgTicket": "Completed Service revenue divided by billed Service calls (revenue > 0).",
        "revenuePerDispatch": "Completed Service revenue divided by all completed Service calls, no-charge included.",
        "membershipAttachPct": "Share of completed Service calls where the same customer bought a membership on the same calendar day (approximate; the API offers no direct membership-to-job link).",
        "lowTicketPct": "Share of billed Service calls billing more than $0 but under rules.lowTicketThreshold.",
        "optionsPerCall": "Average number of estimates attached per completed Service call.",
        "targets": "Configured targets from the profile; met is computed in the direction that is good for each metric.",
    },
    "sales": {
        "opportunitiesPresented": "Chances to sell first presented in the range: estimates grouped one per job, so a Good/Better/Best set counts once.",
        "closeRatePct": "Of the opportunities presented in the range, the share that has since been won (cohort logic, never won-this-month over presented-this-month).",
        "bookedValue": "Value of opportunities won during the range whenever first presented; counts only the options that sold.",
        "avgSoldTicket": "presentedAndWonValue divided by presentedAndWon.",
        "stillOpenValueAtBestOption": "Undecided opportunities from the range valued at their single largest option, i.e. what is realistically still winnable.",
        "reps": "Keyed by person name so one person holding two ServiceTitan user records appears once; owner is soldBy on the sold estimate, else the job's assigned technician.",
    },
    "install": {
        "avgInstallTicket": "Completed Install revenue divided by all completed Install jobs (unlike Service, no billed-only filter).",
        "installers": "Completed revenue in the range versus revenue scheduled inside the board window, seeded with every Install technician so idle crew still appear.",
    },
    "accounting": {
        "totalAR": "Balances on invoices that carry an invoice date. Balances on undated (not yet issued) invoices are excluded because they are unbilled future work.",
        "arAging": "Days past the due date (falling back to the invoice date), bucketed 0-30/31-60/61-90/91-120/120+.",
        "committedOnOpenPOs": "Total of purchase orders in Pending, Sent or PartiallyReceived status (Received, Exported and Canceled are closed). This is committed spend, not accounts payable; vendor bills are available live via `servicetitan api accounting ap-bills list` and are not in the snapshot.",
    },
    "marketing": {
        "attributedRevenue": "Completed revenue in the range attributed directly to the job's campaignId; inbound calls come from the call log's campaign.",
        "bookRatePct": "Booked inbound calls over inbound calls for the campaign.",
    },
    "office": {
        "bookRatePct": "Booked inbound calls over all inbound calls (outbound dials are reported separately and never dilute the rate).",
        "bookRateOfBookableCallsPct": "Booked over booked plus unbooked, i.e. ignoring excused, abandoned and not-a-lead calls; null when the snapshot predates these counts. ServiceTitan call types: Booked, Unbooked, Excused, Abandoned, NotLead.",
        "abandonRatePct": "Abandoned inbound calls over all inbound calls; reported company-wide because abandoned calls reach no agent.",
        "agents": "Answered inbound calls per agent (calls minus abandoned) and how many were booked.",
    },
    "memberships": {
        "activeMembers": "Memberships with status Active.",
        "renewalsDue30d": "Active memberships whose end date falls within the next 30 days (90d likewise).",
        "pastRenewalDate": "Memberships of any status whose end date has passed.",
        "soldInPeriod": "Memberships whose start date falls inside the range.",
    },
}


def compute(q, area, explain=False):
    if area == "all":
        out = q.header("all")
        out["areas"] = {}
        for name in AREAS:
            res = AREA_FUNCS[name](q)
            for k in ("area", "source", "profile", "tenant", "asOf", "timezone", "range", "snapshot"):
                res.pop(k, None)
            if explain:
                res["definitions"] = DEFINITIONS.get(name, {})
            out["areas"][name] = res
        return out
    if area not in AREA_FUNCS:
        raise ValueError("unknown area %r" % area)
    res = AREA_FUNCS[area](q)
    if explain:
        res["definitions"] = DEFINITIONS.get(area, {})
    return res
