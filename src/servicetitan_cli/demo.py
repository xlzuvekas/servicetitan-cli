"""Synthetic snapshot so every command can be exercised before credentials exist.

Matches the schema `fetch` produces, flagged ``isDemo: true`` so the dashboard
shows its banner and `status` never reports it as live data. Names, customers
and vendors are invented.
"""
import datetime as dt
import random

from . import __version__
from .dates import UTC, iso_z, parse_iso

DEFAULT_SEED = 20260101
FIRST = ["Alex", "Jordan", "Sam", "Riley", "Casey", "Morgan", "Taylor", "Jamie",
         "Avery", "Quinn", "Drew", "Rowan", "Skyler", "Cameron", "Harper"]
LAST = ["Nguyen", "Okafor", "Delgado", "Kowalski", "Haddad", "Bergstrom", "Ferrari",
        "Mwangi", "Lindqvist", "Rossi", "Novak", "Silva", "Dubois", "Yamada"]
CUSTOMERS = ["Cedar Ridge HOA", "Larkspur Apartments", "Bellweather Trust", "Ivy Chen",
             "Marcus Abetta", "Northgate Property Mgmt", "Priya Raman", "Sunfield Rentals",
             "Tomasz Wozniak", "Delta Fabrication", "Hollis Family", "Brightwater LLC",
             "Amara Osei", "Fen Zhao", "Redstone Builders", "Willow Court Condos"]
VENDORS = ["Summit HVAC Supply", "Corefield Distribution", "Ridgeline Parts", "Anvil Wholesale", "Trueline Equipment"]
CAMPAIGNS = [("Web Search", "Digital"), ("Referral", "Referral"), ("Repeat Customer", "Retention"),
             ("Social", "Social Media"), ("Yard Sign", "Local"), ("Direct Mail", "Print"), ("Home Show", "Events")]
PLANS = ["Comfort Plan", "Comfort Plan (2 System)", "Priority Service", "Labor Warranty"]
JOB_TITLES = ["System Replacement", "Condenser Swap", "Furnace Replacement", "Coil + Line Set",
              "Ductless Mini Split", "Rooftop Unit", "Air Handler Replacement"]
OPTION_NAMES = ["Good", "Better", "Best", "Premium"]
JOB_TYPES = {"Service": ["No Cool", "Maintenance", "Repair", "Warranty"], "Install": ["Install", "Replacement"],
             "Sales": ["Estimate", "Sales Visit"]}


def build_demo(cfg, op_start, seed=DEFAULT_SEED, now=None, profile="demo"):
    rng = random.Random(seed)
    now = now or dt.datetime.now(UTC)
    start = parse_iso(op_start[:10]).replace(tzinfo=UTC)
    days = max(1, (now - start).days)
    low_ticket = cfg["rules"]["lowTicketThreshold"]
    board_days = cfg["rules"]["boardDays"]
    depts = [d for d in cfg["departmentMapping"] if d != "exclude"] or ["Service"]
    out = {k: [] for k in ("technicians", "jobs", "businessUnits", "opportunities", "ar", "purchaseOrders",
                           "campaigns", "calls", "memberships")}
    people, po_totals = {}, {}
    uid = 1000
    tenants = cfg.get("tenants") or [{"code": "MAIN", "label": "Main"}]

    for tenant in tenants:
        code = tenant["code"]
        k = lambda n, code=code: "%s:%s" % (code, n)
        crew = []
        for dept in depts:
            for _ in range(3 if dept == "Service" else 2):
                uid += 1
                name = "%s %s" % (rng.choice(FIRST), rng.choice(LAST))
                crew.append({"id": k(uid), "name": name, "dept": dept})
                people[k(uid)] = name
        out["technicians"] += [{"id": c["id"], "name": c["name"], "loc": code, "dept": c["dept"], "active": True} for c in crew]
        for i, dept in enumerate(depts):
            out["businessUnits"].append({"id": k(5500 + i), "loc": code, "name": "%s-%s" % (code, dept), "dept": dept, "active": True})
        camps = []
        for i, (nm, cat) in enumerate(CAMPAIGNS):
            camps.append(k(600 + i))
            out["campaigns"].append({"id": k(600 + i), "loc": code, "name": nm, "category": cat, "active": True})

        n_jobs = int(days * rng.uniform(1.8, 2.4))
        for _ in range(n_jobs):
            uid += 1
            when = start + dt.timedelta(days=rng.randint(0, days - 1), hours=rng.randint(7, 18))
            dept = rng.choices(depts, weights=[3 if d == "Service" else 2 for d in depts])[0]
            tech = rng.choice([c for c in crew if c["dept"] == dept])
            if dept == "Sales":
                rev = 0.0
            elif dept == "Install":
                rev = round(rng.uniform(4200, 19500), 2)
            else:
                rev = 0.0 if rng.random() < 0.28 else round(rng.uniform(89, 1400), 2)
            out["jobs"].append({
                "id": k(uid), "jobNumber": str(uid), "date": iso_z(when), "loc": code, "dept": dept,
                "tech": tech["id"], "revenue": rev, "future": False, "campaign": rng.choice(camps),
                "membershipSold": dept == "Service" and rev > 0 and rng.random() < 0.11,
                "lowTicket": 0 < rev < low_ticket, "options": rng.choice([0, 0, 1, 2, 3, 4]),
                "customer": rng.choice(CUSTOMERS), "businessUnit": "%s-%s" % (code, dept),
                "jobType": rng.choice(JOB_TYPES.get(dept, ["Job"])), "noCharge": rev == 0 and dept != "Sales",
                "status": "Completed",
            })
        for _ in range(int(rng.uniform(18, 34))):
            uid += 1
            when = now + dt.timedelta(days=rng.randint(0, board_days + 25), hours=rng.randint(7, 17))
            dept = rng.choices(depts, weights=[3 if d == "Service" else 2 for d in depts])[0]
            tech = rng.choice([c for c in crew if c["dept"] == dept])
            rev = round(rng.uniform(5200, 21000), 2) if dept == "Install" else \
                (0.0 if rng.random() < 0.5 else round(rng.uniform(120, 900), 2))
            out["jobs"].append({
                "id": k(uid), "jobNumber": str(uid), "date": iso_z(when), "loc": code, "dept": dept,
                "tech": tech["id"], "revenue": rev, "future": True, "campaign": rng.choice(camps),
                "membershipSold": False, "lowTicket": False, "options": rng.choice([0, 1, 2, 3]),
                "customer": rng.choice(CUSTOMERS), "businessUnit": "%s-%s" % (code, dept),
                "jobType": rng.choice(JOB_TYPES.get(dept, ["Job"])), "noCharge": False, "status": "Scheduled",
            })
        sellers = [c for c in crew if c["dept"] in ("Sales", "Install")] or crew
        for _ in range(int(days * rng.uniform(0.35, 0.5))):
            uid += 1
            presented = start + dt.timedelta(days=rng.randint(0, days - 1))
            n_opts = rng.choices([1, 2, 3, 4], weights=[45, 22, 18, 15])[0]
            won = rng.random() < 0.52
            top = round(rng.uniform(3800, 24000), 2)
            owner = rng.choice(sellers)
            won_on = presented + dt.timedelta(days=rng.randint(0, 21))
            if won_on > now:
                won_on, won = None, False
            out["opportunities"].append({
                "id": k(uid), "loc": code, "dept": owner["dept"], "customer": rng.choice(CUSTOMERS),
                "city": tenant.get("label"),
                "jobNumber": str(rng.randint(100000, 999999)) if rng.random() < 0.85 else None,
                "title": (rng.choice(OPTION_NAMES) + " - " + rng.choice(JOB_TITLES)) if n_opts > 1 else rng.choice(JOB_TITLES),
                "presentedOn": iso_z(presented), "wonOn": iso_z(won_on) if won else None,
                "won": won, "options": n_opts, "owner": owner["id"],
                "value": round(top * rng.uniform(0.55, 1.0), 2) if won else 0.0, "topOption": top,
            })
        for _ in range(int(rng.uniform(16, 30))):
            uid += 1
            age = int(abs(rng.gauss(0, 45)))
            d = now - dt.timedelta(days=min(age, 210))
            out["ar"].append({
                "id": k(uid), "loc": code, "invoiceNumber": str(uid), "customer": rng.choice(CUSTOMERS),
                "invoiceDate": iso_z(d), "dueDate": iso_z(d + dt.timedelta(days=15)),
                "balance": round(rng.uniform(180, 14000), 2), "total": round(rng.uniform(180, 14000), 2),
                "businessUnit": "%s-%s" % (code, rng.choice(depts)),
            })
        n_open = int(rng.uniform(8, 20))
        for _ in range(n_open):
            uid += 1
            out["purchaseOrders"].append({
                "id": k(uid), "loc": code, "number": str(uid), "vendor": rng.choice(VENDORS),
                "date": iso_z(now - dt.timedelta(days=rng.randint(0, 30))),
                "total": round(rng.uniform(120, 9800), 2),
                "status": rng.choice(["Pending", "Sent", "PartiallyReceived"]),
            })
        po_totals[code] = n_open + int(rng.uniform(120, 400))
        agents = ["%s %s" % (rng.choice(FIRST), rng.choice(LAST)) for _ in range(4)]
        for day in range(days):
            d0 = start + dt.timedelta(days=day)
            d = d0.strftime("%Y-%m-%d")
            if d0.weekday() >= 5 and rng.random() < 0.6:
                continue
            for agent in agents:
                n = rng.randint(0, 9)
                if not n:
                    continue
                booked = sum(1 for _ in range(n) if rng.random() < 0.34)
                excused = sum(1 for _ in range(n - booked) if rng.random() < 0.2)
                out["calls"].append({"date": d, "loc": code, "agentName": agent, "campaign": rng.choice(camps),
                                     "inbound": True, "n": n, "booked": booked, "abandoned": 0,
                                     "unbooked": n - booked - excused, "excused": excused, "notLead": 0})
            unanswered = rng.randint(0, 4)
            if unanswered:
                out["calls"].append({"date": d, "loc": code, "agentName": None, "campaign": rng.choice(camps),
                                     "inbound": True, "n": unanswered, "booked": 0, "abandoned": unanswered})
            outbound = rng.randint(0, 6)
            if outbound:
                out["calls"].append({"date": d, "loc": code, "agentName": rng.choice(agents), "campaign": None,
                                     "inbound": False, "n": outbound, "booked": 0, "abandoned": 0})
        for _ in range(int(rng.uniform(40, 90))):
            uid += 1
            begin = now - dt.timedelta(days=rng.randint(0, 380))
            out["memberships"].append({
                "id": k(uid), "loc": code, "status": "Active", "type": rng.choice(PLANS),
                "customer": rng.choice(CUSTOMERS),
                "from": iso_z(begin), "to": iso_z(begin + dt.timedelta(days=365)), "soldBy": rng.choice(crew)["id"],
            })

    payload = {
        "generatedAt": iso_z(now), "isDemo": True, "profile": profile,
        "environment": "demo", "timezone": cfg.get("timezone") or "UTC",
        "tenants": [{"code": t["code"], "label": t.get("label"), "tenantId": None} for t in tenants],
        "window": {"operationalStart": op_start, "arStart": None},
        "cli": {"version": __version__}, "seed": seed,
        "people": people, "poTotals": po_totals,
        "rawCallCount": sum(c["n"] for c in out["calls"]),
        "estimateCount": sum(o["options"] for o in out["opportunities"]),
    }
    payload.update(out)
    return payload
