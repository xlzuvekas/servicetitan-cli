"""Metric math checked against a hand-built snapshot with known answers, plus
invariants on the synthetic demo snapshot."""
import datetime as dt

import pytest

from servicetitan_cli.dates import UTC
from servicetitan_cli.metrics import AREAS, Query, compute
from servicetitan_cli.snapshot import Snapshot

AS_OF = dt.datetime(2026, 9, 15, 17, 30, tzinfo=UTC)
TARGETS = {"avgTicket": 280, "membershipAttachPct": 12, "lowTicketPct": 10, "optionsPerCall": 2.5}
RULES = {"lowTicketThreshold": 150, "boardDays": 45}


def job(i, dept, date, revenue, tech="A:1", future=False, **kw):
    base = {"id": "A:%d" % i, "jobNumber": str(i), "date": date, "loc": "A", "dept": dept, "tech": tech,
            "revenue": revenue, "future": future, "campaign": None, "membershipSold": False,
            "lowTicket": 0 < revenue < 150 and not future, "options": 0, "customer": "Cust %d" % i}
    base.update(kw)
    return base


def hand_snapshot():
    return Snapshot({
        "generatedAt": "2026-09-15T17:00:00Z", "isDemo": False, "profile": "hand",
        "tenants": [{"code": "A", "label": "Alpha"}],
        "people": {"A:1": "Tech One", "A:2": "Tech Two", "A:9": "Rep Nine"},
        "technicians": [{"id": "A:1", "name": "Tech One", "loc": "A", "dept": "Service"},
                        {"id": "A:2", "name": "Tech Two", "loc": "A", "dept": "Install"}],
        "businessUnits": [{"id": "A:5", "loc": "A", "name": "Service", "dept": "Service"}],
        "campaigns": [{"id": "A:100", "loc": "A", "name": "Referral", "category": "Referral"}],
        "jobs": [
            job(1, "Service", "2026-09-05T15:00:00Z", 300, membershipSold=True, options=2, campaign="A:100"),
            job(2, "Service", "2026-09-10T15:00:00Z", 100),
            job(3, "Service", "2026-09-12T15:00:00Z", 0, noCharge=True),
            job(4, "Install", "2026-09-01T15:00:00Z", 10000, tech="A:2"),
            job(5, "Service", "2026-08-20T15:00:00Z", 200),
            job(6, "Service", "2026-09-20T15:00:00Z", 500, future=True),
            job(7, "Install", "2026-11-15T15:00:00Z", 8000, tech="A:2", future=True),
            job(8, "Service", "2026-10-05T15:00:00Z", 150, future=True),
        ],
        "opportunities": [
            {"id": "A:o1", "loc": "A", "dept": "Sales", "customer": "C1", "presentedOn": "2026-09-03T10:00:00Z",
             "wonOn": "2026-09-08T10:00:00Z", "won": True, "options": 3, "owner": "A:9", "value": 5000, "topOption": 6000},
            {"id": "A:o2", "loc": "A", "dept": "Sales", "customer": "C2", "presentedOn": "2026-09-06T10:00:00Z",
             "wonOn": None, "won": False, "options": 1, "owner": "A:9", "value": 0, "topOption": 4000},
            {"id": "A:o3", "loc": "A", "dept": "Service", "customer": "C3", "presentedOn": "2026-08-01T10:00:00Z",
             "wonOn": "2026-09-02T10:00:00Z", "won": True, "options": 1, "owner": "A:1", "value": 3000, "topOption": 3000},
            {"id": "A:o4", "loc": "A", "dept": "Sales", "customer": "C4", "presentedOn": "2026-09-07T10:00:00Z",
             "wonOn": None, "won": False, "options": 1, "owner": None, "value": 0, "topOption": 1000},
        ],
        "ar": [
            {"id": "A:i1", "loc": "A", "customer": "Cust A", "invoiceDate": "2026-08-25T00:00:00Z", "dueDate": "2026-09-10T00:00:00Z", "balance": 1000},
            {"id": "A:i2", "loc": "A", "customer": "Cust B", "invoiceDate": "2026-06-15T00:00:00Z", "dueDate": "2026-07-01T00:00:00Z", "balance": 2000},
            {"id": "A:i3", "loc": "A", "customer": "Cust A", "invoiceDate": "2026-03-01T00:00:00Z", "dueDate": None, "balance": 500},
        ],
        "purchaseOrders": [{"id": "A:p1", "loc": "A", "vendor": "Vendor V", "date": "2026-09-01T00:00:00Z", "total": 700, "status": "Pending"}],
        "poTotals": {"A": 10},
        "calls": [
            {"date": "2026-09-04", "loc": "A", "agentName": "Agent A", "campaign": "A:100", "inbound": True, "n": 10, "booked": 4, "abandoned": 1},
            {"date": "2026-09-05", "loc": "A", "agentName": None, "campaign": None, "inbound": True, "n": 3, "booked": 0, "abandoned": 3},
            {"date": "2026-09-06", "loc": "A", "agentName": "Agent A", "campaign": None, "inbound": False, "n": 5, "booked": 0, "abandoned": 0},
        ],
        "memberships": [
            {"id": "A:m1", "loc": "A", "status": "Active", "type": "Gold", "from": "2026-09-02T00:00:00Z", "to": "2026-10-01T00:00:00Z", "soldBy": "A:1"},
            {"id": "A:m2", "loc": "A", "status": "Active", "type": "Silver", "from": "2025-12-01T00:00:00Z", "to": "2026-12-01T00:00:00Z", "soldBy": "A:1"},
            {"id": "A:m3", "loc": "A", "status": "Expired", "type": "Gold", "from": "2025-08-01T00:00:00Z", "to": "2026-08-01T00:00:00Z", "soldBy": None},
        ],
    }, path="hand.json")


def query(kind="mtd", tenant=None, **kw):
    return Query(hand_snapshot(), AS_OF, UTC, range_kind=kind, tenant=tenant, targets=TARGETS, rules=RULES, **kw)


def test_revenue_area_exact():
    r = compute(query(), "revenue")
    k = r["kpis"]
    assert k["completedRevenue"] == 10400 and k["jobsCompleted"] == 4 and k["billedJobs"] == 3
    assert k["noChargeOrSalesVisits"] == 1 and k["avgBilledJob"] == 3466.67 and k["avgAllJobs"] == 2600
    assert k["deltaVsPriorPeriodPct"] == 5100.0 and k["priorPeriod"]["completedRevenue"] == 200
    assert k["restOfPeriod"] == {"label": "Rest of This Month", "through": "2026-09-30", "ended": False, "revenue": 500, "jobs": 1}
    assert k["board"] == {"days": 45, "through": "2026-10-30", "revenue": 650, "jobs": 2}
    assert k["runRate"]["period"] == "This Month" and k["runRate"]["periodComplete"] is False
    assert abs(k["runRate"]["value"] - 10400 * 30 / (14 + 17.5 / 24)) < 30
    assert r["ytd"]["completedRevenue"] == 10600 and r["ytd"]["jobsCompleted"] == 5
    assert r["revenueByIndividual"][0] == {"name": "Tech Two", "revenue": 10000}
    assert r["revenueOverTime"]["granularity"] == "day" and sum(p["total"] for p in r["revenueOverTime"]["points"]) == 10400
    assert r["range"]["kind"] == "mtd" and r["tenant"] == "all" and r["snapshot"]["profile"] == "hand"


def test_service_area_exact():
    s = compute(query(), "service", explain=True)
    k = s["kpis"]
    assert (k["completedRevenue"], k["callsCompleted"], k["billedCalls"], k["noChargeCalls"]) == (400, 3, 2, 1)
    assert k["avgTicket"] == 200 and k["revenuePerDispatch"] == 133.33
    assert k["membershipAttachPct"] == 33.3 and k["lowTicketPct"] == 50.0 and k["optionsPerCall"] == 0.67
    t = s["targets"]
    assert t["avgTicket"]["met"] is False and t["membershipAttachPct"]["met"] is True and t["lowTicketPct"]["met"] is False
    assert s["technicians"][0]["technician"] == "Tech One" and s["technicians"][0]["calls"] == 3
    assert "avgTicket" in s["definitions"]


def test_sales_area_exact():
    s = compute(query(), "sales")
    k = s["kpis"]
    assert k["opportunitiesPresented"] == 3 and k["presentedAndWon"] == 1 and k["closeRatePct"] == 33.3
    assert k["bookedValue"] == 8000 and k["wonInPeriod"] == 2 and k["presentedAndWonValue"] == 5000
    assert k["stillOpen"] == 2 and k["stillOpenValueAtBestOption"] == 5000 and k["optionsPresented"] == 5
    reps = {r["rep"]: r for r in s["reps"]}
    assert reps["Rep Nine"]["presented"] == 2 and reps["Rep Nine"]["closeRatePct"] == 50.0 and reps["Rep Nine"]["revenue"] == 5000
    assert reps["Tech One"]["revenue"] == 0 and reps["Tech One"]["ytdRevenue"] == 3000 and s["reps"][0]["rep"] == "Rep Nine"
    assert "Unassigned" not in reps and s["unassignedOpportunities"]["presented"] == 1
    assert s["ytd"]["presented"] == 4 and s["ytd"]["won"] == 2


def test_install_accounting_marketing_office_memberships_exact():
    i = compute(query(), "install")
    assert i["kpis"]["completedRevenue"] == 10000 and i["kpis"]["installsCompleted"] == 1 and i["kpis"]["board"]["revenue"] == 0
    assert i["installers"] == [{"installer": "Tech Two", "completed": 10000, "scheduled": 0}]

    a = compute(query(), "accounting")
    assert a["kpis"]["totalAR"] == 3500 and a["kpis"]["arPast30Days"] == 2500 and a["kpis"]["arPast30Pct"] == 71.4
    assert a["kpis"]["committedOnOpenPOs"] == 700 and a["kpis"]["openPOs"] == 1 and a["kpis"]["allPOs"] == 10
    buckets = {b["bucket"]: b["amount"] for b in a["arAging"]}
    assert buckets == {"0-30": 1000, "31-60": 0, "61-90": 2000, "91-120": 0, "120+": 500}
    assert a["arByCustomer"][0] == {"customer": "Cust B", "invoices": 1, "oldestDaysPastDue": 76, "bucket": "61-90", "balance": 2000}

    m = compute(query(), "marketing")
    rows = {r["campaign"]: r for r in m["campaigns"]}
    assert rows["Referral"]["inboundCalls"] == 10 and rows["Referral"]["bookRatePct"] == 40.0 and rows["Referral"]["revenue"] == 300
    assert rows["No campaign"]["jobs"] == 3 and m["kpis"]["revenuePerJob"] == 2600 and m["kpis"]["inboundCalls"] == 13

    o = compute(query(), "office")
    assert o["kpis"] == {"inboundCalls": 13, "outboundCalls": 5, "booked": 4, "bookRatePct": 30.8, "abandoned": 4,
                         "abandonRatePct": 30.8, "inboundReachedNoAgent": 3}
    assert o["agents"] == [{"agent": "Agent A", "answered": 9, "booked": 4, "bookRatePct": 44.4}]

    mem = compute(query(), "memberships")
    assert mem["kpis"] == {"activeMembers": 2, "soldInPeriod": 1, "renewalsDue30d": 1, "renewalsDue90d": 2, "pastRenewalDate": 1}
    assert mem["upcomingRenewals"][0]["plan"] == "Gold" and mem["upcomingRenewals"][0]["renews"] == "2026-10-01"


def test_tenant_filter_and_all():
    assert compute(query(tenant="B"), "revenue")["kpis"]["completedRevenue"] == 0
    assert compute(query(tenant="a"), "revenue")["kpis"]["completedRevenue"] == 10400
    everything = compute(query(), "all", explain=True)
    assert set(everything["areas"]) == set(AREAS) and everything["areas"]["office"]["definitions"]
    with pytest.raises(ValueError):
        compute(query(), "nope")


def test_ranges_change_cohorts():
    ytd = compute(query("ytd"), "sales")["kpis"]
    assert ytd["opportunitiesPresented"] == 4
    custom = query("custom", custom_start=dt.datetime(2026, 8, 1, tzinfo=UTC), custom_end=dt.datetime(2026, 8, 31, tzinfo=UTC))
    r = compute(custom, "revenue")
    assert r["kpis"]["completedRevenue"] == 200 and r["kpis"]["runRate"]["periodComplete"] is True
    assert r["kpis"]["restOfPeriod"]["ended"] is True


def test_demo_invariants(demo_data):
    snap = Snapshot(demo_data, "demo.json")
    q = Query(snap, AS_OF, UTC, "ytd", targets=TARGETS, rules=RULES)
    rev = compute(q, "revenue")["kpis"]
    expected = sum(j["revenue"] for j in demo_data["jobs"] if not j["future"])
    assert abs(rev["completedRevenue"] - round(expected, 2)) < 0.01
    assert rev["billedJobs"] + rev["noChargeOrSalesVisits"] == rev["jobsCompleted"]
    svc = compute(q, "service")["kpis"]
    assert abs(svc["avgTicket"] * svc["billedCalls"] - svc["completedRevenue"]) < 1
    acc = compute(q, "accounting")
    assert abs(sum(b["amount"] for b in acc["arAging"]) - acc["kpis"]["totalAR"]) < 0.05
    sales = compute(q, "sales")["kpis"]
    assert sales["presentedAndWon"] + sales["stillOpen"] == sales["opportunitiesPresented"]
    per_tenant = sum(compute(Query(snap, AS_OF, UTC, "ytd", tenant=c), "revenue")["kpis"]["completedRevenue"]
                     for c in ("SPR", "LOC2"))
    assert abs(per_tenant - rev["completedRevenue"]) < 0.05
