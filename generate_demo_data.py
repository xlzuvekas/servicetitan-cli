#!/usr/bin/env python3
"""
Generate synthetic demo data so the dashboard can be opened and explored before
any ServiceTitan credentials exist.

    python generate_demo_data.py     # writes demo_data.json
    python render_dashboard.py --demo

The output matches the schema fetch_dashboard_data.py produces, so every tab,
filter and drill-down behaves exactly as it will on real data. It is flagged
with "isDemo": true, and the dashboard shows a prominent banner whenever that
flag is set -- demo figures can never be mistaken for a real reading.

Names, customers and vendors here are invented. Any resemblance to a real
company is coincidence.
"""
import json
import random
from datetime import datetime, timedelta, timezone

from st_config import load_config, window_starts

SEED = 20260101  # fixed so the demo looks the same every run

FIRST = ["Alex", "Jordan", "Sam", "Riley", "Casey", "Morgan", "Taylor", "Jamie",
         "Avery", "Quinn", "Drew", "Rowan", "Skyler", "Cameron", "Harper"]
LAST = ["Nguyen", "Okafor", "Delgado", "Kowalski", "Haddad", "Bergstrom", "Ferrari",
        "Mwangi", "Lindqvist", "Rossi", "Novak", "Silva", "Dubois", "Yamada"]
CUSTOMERS = ["Cedar Ridge HOA", "Larkspur Apartments", "Bellweather Trust", "Ivy Chen",
             "Marcus Abetta", "Northgate Property Mgmt", "Priya Raman", "Sunfield Rentals",
             "Tomasz Wozniak", "Delta Fabrication", "Hollis Family", "Brightwater LLC",
             "Amara Osei", "Fen Zhao", "Redstone Builders", "Willow Court Condos"]
VENDORS = ["Summit HVAC Supply", "Corefield Distribution", "Ridgeline Parts",
           "Anvil Wholesale", "Trueline Equipment"]
CAMPAIGNS = [("Web Search", "Digital"), ("Referral", "Referral"), ("Repeat Customer", "Retention"),
             ("Social", "Social Media"), ("Yard Sign", "Local"), ("Direct Mail", "Print"),
             ("Home Show", "Events")]
PLANS = ["Comfort Plan", "Comfort Plan (2 System)", "Priority Service", "Labor Warranty"]
JOB_TITLES = ["System Replacement", "Condenser Swap", "Furnace Replacement", "Coil + Line Set",
              "Ductless Mini Split", "Rooftop Unit", "Air Handler Replacement"]
OPTION_NAMES = ["Good", "Better", "Best", "Premium"]


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    cfg = load_config()
    rng = random.Random(SEED)
    now = datetime.now(timezone.utc)
    op_start, _ = window_starts(cfg)
    start = datetime.strptime(op_start[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    days = max(1, (now - start).days)
    low_ticket = cfg["rules"]["lowTicketThreshold"]
    board_days = cfg["rules"]["boardDays"]

    depts = [d for d in cfg["departmentMapping"] if d != "exclude"] or ["Service"]
    out = {k: [] for k in ("technicians", "jobs", "businessUnits", "opportunities",
                           "ar", "purchaseOrders", "campaigns", "calls", "memberships")}
    people, po_totals = {}, {}
    uid = 1000

    for loc in cfg["locations"]:
        code = loc["code"]
        k = lambda n: f"{code}:{n}"

        # --- roster -------------------------------------------------------
        crew = []
        for dept in depts:
            for _ in range(3 if dept == "Service" else 2):
                uid += 1
                name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
                crew.append({"id": k(uid), "name": name, "dept": dept})
                people[k(uid)] = name
        out["technicians"] += [{"id": c["id"], "name": c["name"], "loc": code, "dept": c["dept"]}
                               for c in crew]
        for i, dept in enumerate(depts):
            out["businessUnits"].append({"id": k(5500 + i), "loc": code,
                                         "name": f"{code}-{dept}", "dept": dept})

        camps = []
        for i, (nm, cat) in enumerate(CAMPAIGNS):
            camps.append(k(600 + i))
            out["campaigns"].append({"id": k(600 + i), "loc": code, "name": nm, "category": cat})

        # --- completed jobs ----------------------------------------------
        # Volume scales with the length of the window so a mid-year run and a
        # January run both look sensible.
        n_jobs = int(days * rng.uniform(1.8, 2.4))
        for _ in range(n_jobs):
            uid += 1
            when = start + timedelta(days=rng.randint(0, days - 1), hours=rng.randint(7, 18))
            dept = rng.choices(depts, weights=[3 if d == "Service" else 2 for d in depts])[0]
            tech = rng.choice([c for c in crew if c["dept"] == dept])
            if dept == "Sales":
                rev = 0.0                       # a sales visit produces an estimate, not revenue
            elif dept == "Install":
                rev = round(rng.uniform(4200, 19500), 2)
            else:
                rev = 0.0 if rng.random() < 0.28 else round(rng.uniform(89, 1400), 2)
            out["jobs"].append({
                "id": k(uid), "date": iso(when), "loc": code, "dept": dept,
                "tech": tech["id"], "revenue": rev, "future": False,
                "campaign": rng.choice(camps),
                "membershipSold": dept == "Service" and rev > 0 and rng.random() < 0.11,
                "lowTicket": 0 < rev < low_ticket,
                "options": rng.choice([0, 0, 1, 2, 3, 4]),
            })

        # --- booked jobs --------------------------------------------------
        for _ in range(int(rng.uniform(18, 34))):
            uid += 1
            when = now + timedelta(days=rng.randint(0, board_days + 25), hours=rng.randint(7, 17))
            dept = rng.choices(depts, weights=[3 if d == "Service" else 2 for d in depts])[0]
            tech = rng.choice([c for c in crew if c["dept"] == dept])
            rev = round(rng.uniform(5200, 21000), 2) if dept == "Install" else \
                  (0.0 if rng.random() < 0.5 else round(rng.uniform(120, 900), 2))
            out["jobs"].append({
                "id": k(uid), "date": iso(when), "loc": code, "dept": dept,
                "tech": tech["id"], "revenue": rev, "future": True,
                "campaign": rng.choice(camps), "membershipSold": False,
                "lowTicket": False, "options": rng.choice([0, 1, 2, 3]),
            })

        # --- opportunities -------------------------------------------------
        sellers = [c for c in crew if c["dept"] in ("Sales", "Install")] or crew
        for _ in range(int(days * rng.uniform(0.35, 0.5))):
            uid += 1
            presented = start + timedelta(days=rng.randint(0, days - 1))
            n_opts = rng.choices([1, 2, 3, 4], weights=[45, 22, 18, 15])[0]
            won = rng.random() < 0.52
            top = round(rng.uniform(3800, 24000), 2)
            owner = rng.choice(sellers)
            won_on = presented + timedelta(days=rng.randint(0, 21))
            if won_on > now:
                won_on, won = None, False
            out["opportunities"].append({
                "id": k(uid), "loc": code, "dept": owner["dept"],
                "customer": rng.choice(CUSTOMERS),
                "city": loc["label"],
                "jobNumber": str(rng.randint(100000, 999999)) if rng.random() < 0.85 else None,
                "title": rng.choice(OPTION_NAMES) + " - " + rng.choice(JOB_TITLES)
                         if n_opts > 1 else rng.choice(JOB_TITLES),
                "presentedOn": iso(presented),
                "wonOn": iso(won_on) if won else None,
                "won": won, "options": n_opts, "owner": owner["id"],
                "value": round(top * rng.uniform(0.55, 1.0), 2) if won else 0.0,
                "topOption": top,
            })

        # --- receivables ----------------------------------------------------
        for _ in range(int(rng.uniform(16, 30))):
            uid += 1
            age = int(abs(rng.gauss(0, 45)))
            d = now - timedelta(days=min(age, 210))
            out["ar"].append({
                "id": k(uid), "loc": code, "customer": rng.choice(CUSTOMERS),
                "invoiceDate": iso(d), "dueDate": iso(d + timedelta(days=15)),
                "balance": round(rng.uniform(180, 14000), 2),
                "businessUnit": f"{code}-{rng.choice(depts)}",
            })

        # --- purchase orders -------------------------------------------------
        n_open = int(rng.uniform(8, 20))
        for _ in range(n_open):
            uid += 1
            out["purchaseOrders"].append({
                "id": k(uid), "loc": code, "vendor": rng.choice(VENDORS),
                "date": iso(now - timedelta(days=rng.randint(0, 30))),
                "total": round(rng.uniform(120, 9800), 2),
                "status": rng.choice(["Pending", "Sent", "Partially Received"]),
            })
        po_totals[code] = n_open + int(rng.uniform(120, 400))

        # --- calls -------------------------------------------------------------
        agents = [f"{rng.choice(FIRST)} {rng.choice(LAST)}" for _ in range(4)]
        for day in range(days):
            d = (start + timedelta(days=day)).strftime("%Y-%m-%d")
            if (start + timedelta(days=day)).weekday() >= 5 and rng.random() < 0.6:
                continue
            for agent in agents:
                n = rng.randint(0, 9)
                if not n:
                    continue
                booked = sum(1 for _ in range(n) if rng.random() < 0.34)
                out["calls"].append({"date": d, "loc": code, "agentName": agent,
                                     "campaign": rng.choice(camps), "inbound": True,
                                     "n": n, "booked": booked, "abandoned": 0})
            unanswered = rng.randint(0, 4)
            if unanswered:
                out["calls"].append({"date": d, "loc": code, "agentName": None,
                                     "campaign": rng.choice(camps), "inbound": True,
                                     "n": unanswered, "booked": 0, "abandoned": unanswered})
            outbound = rng.randint(0, 6)
            if outbound:
                out["calls"].append({"date": d, "loc": code, "agentName": rng.choice(agents),
                                     "campaign": None, "inbound": False,
                                     "n": outbound, "booked": 0, "abandoned": 0})

        # --- memberships ---------------------------------------------------------
        for _ in range(int(rng.uniform(40, 90))):
            uid += 1
            begin = now - timedelta(days=rng.randint(0, 330))
            out["memberships"].append({
                "id": k(uid), "loc": code, "status": "Active", "type": rng.choice(PLANS),
                "from": iso(begin), "to": iso(begin + timedelta(days=365)),
                "soldBy": rng.choice(crew)["id"],
            })

    payload = {
        "generatedAt": iso(datetime.now(timezone.utc)),
        "isDemo": True,
        "people": people, "poTotals": po_totals,
        "rawCallCount": sum(c["n"] for c in out["calls"]),
        "estimateCount": sum(o["options"] for o in out["opportunities"]),
        **out,
    }
    with open("demo_data.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    done = [j for j in out["jobs"] if not j["future"]]
    print(f"Wrote demo_data.json  ({len(cfg['locations'])} location(s), seed {SEED})")
    print(f"   {len(done)} completed jobs, ${sum(j['revenue'] for j in done):,.0f}")
    print(f"   {len(out['opportunities'])} opportunities, {len(out['ar'])} open invoices")
    print("\nNext:  python render_dashboard.py --demo")


if __name__ == "__main__":
    main()
