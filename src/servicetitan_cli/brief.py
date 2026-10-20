"""`servicetitan brief`: one call, the day's operating picture from the snapshot.

Everything here is a selection from the metrics areas (same math, same
definitions), arranged the way a person or an agent asks about it first thing:
today, the coming week, month to date, receivables, memberships, pipeline, and
a few flags worth a look. Nothing new is computed; `metrics <area> --explain`
carries the definitions.
"""
from .listing import list_records
from .metrics import Query, compute

FLAG_AR_PAST_30_PCT = 25.0
FLAG_ABANDON_RATE_PCT = 10.0


def _pick(d, keys):
    return {k: d.get(k) for k in keys if k in d}


def build_brief(snap, as_of, tz, tenant=None, targets=None, rules=None, max_age_hours=6.0):
    def q(kind):
        return Query(snap, as_of, tz, range_kind=kind, tenant=tenant, targets=targets, rules=rules)

    today, mtd, ytd, next7 = q("today"), q("mtd"), q("ytd"), q("next7")
    rev_t = compute(today, "revenue")["kpis"]
    off_t = compute(today, "office")["kpis"]
    rev = compute(mtd, "revenue")
    svc = compute(mtd, "service")["kpis"]
    sales = compute(mtd, "sales")["kpis"]
    office = compute(mtd, "office")["kpis"]
    members = compute(mtd, "memberships")["kpis"]
    acc = compute(mtd, "accounting")["kpis"]
    rev_y = compute(ytd, "revenue")
    scheduled_today = list_records(today, "jobs", {"status": "booked"}, limit=0, use_range=True)["total"]
    week = list_records(next7, "jobs", {"status": "booked"}, limit=0, use_range=True)
    week_revenue = round(sum(float(r.get("revenue") or 0) for r in week["records"]), 2)
    kp = rev["kpis"]
    snapshot = snap.describe(as_of=as_of, max_age_hours=max_age_hours)

    flags = []
    if snapshot["complete"] is False:
        flags.append("snapshot is incomplete, so totals are too low: %s; run `servicetitan fetch` again, without "
                     "--max-pages" % "; ".join(snapshot["truncated"]))
    if snapshot["stale"]:
        flags.append("snapshot is %sh old; run `servicetitan fetch`" % (snapshot["ageHours"] if snapshot["ageHours"] is not None else "?"))
    if (acc.get("arPast30Pct") or 0) >= FLAG_AR_PAST_30_PCT:
        flags.append("%.0f%% of receivables are more than 30 days past due" % acc["arPast30Pct"])
    if (office.get("abandonRatePct") or 0) >= FLAG_ABANDON_RATE_PCT:
        flags.append("%.0f%% of inbound calls were abandoned this month" % office["abandonRatePct"])
    if members.get("pastRenewalDate"):
        flags.append("%d membership(s) are past their renewal date" % members["pastRenewalDate"])
    if kp.get("deltaVsPriorPeriodPct") is not None and kp["deltaVsPriorPeriodPct"] <= -20:
        flags.append("completed revenue is %.0f%% below the prior period" % abs(kp["deltaVsPriorPeriodPct"]))

    return {
        "source": "snapshot",
        "profile": rev.get("profile"),
        "tenant": rev.get("tenant"),
        "asOf": rev.get("asOf"),
        "timezone": rev.get("timezone"),
        "snapshot": snapshot,
        "today": {"jobsScheduled": scheduled_today, "jobsCompleted": rev_t.get("jobsCompleted"),
                  "completedRevenue": rev_t.get("completedRevenue"), "inboundCalls": off_t.get("inboundCalls"),
                  "booked": off_t.get("booked"), "abandoned": off_t.get("abandoned")},
        "next7Days": {"jobsBooked": week["total"], "bookedRevenue": week_revenue},
        "monthToDate": dict(_pick(kp, ("completedRevenue", "deltaVsPriorPeriodPct", "jobsCompleted", "billedJobs", "avgBilledJob")),
                            runRate=(kp.get("runRate") or {}).get("value"),
                            restOfMonthBooked=(kp.get("restOfPeriod") or {}).get("revenue"),
                            serviceAvgTicket=svc.get("avgTicket"), membershipAttachPct=svc.get("membershipAttachPct"),
                            closeRatePct=sales.get("closeRatePct"), opportunitiesPresented=sales.get("opportunitiesPresented"),
                            bookedValue=sales.get("bookedValue"), membershipsSold=members.get("soldInPeriod"),
                            inboundCalls=office.get("inboundCalls"), bookRatePct=office.get("bookRatePct"),
                            abandonRatePct=office.get("abandonRatePct")),
        "yearToDate": _pick(rev_y.get("ytd") or {}, ("completedRevenue", "annualizedRunRate")),
        "board": kp.get("board"),
        "receivables": _pick(acc, ("totalAR", "openInvoices", "arPast30Days", "arPast30Pct", "invoicesWithoutDueDate", "committedOnOpenPOs", "openPOs")),
        "memberships": _pick(members, ("activeMembers", "renewalsDue30d", "renewalsDue90d", "pastRenewalDate")),
        "openPipeline": _pick(sales, ("stillOpen", "stillOpenValueAtBestOption")),
        "flags": flags,
        "next": "servicetitan metrics <area> --explain for definitions; servicetitan list jobs --range next7 --booked for the week",
    }
