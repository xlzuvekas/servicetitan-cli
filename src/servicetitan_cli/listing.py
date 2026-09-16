"""Record listings from a snapshot with agent-friendly filters.

Every kind supports ``--tenant``, an optional date range, ``--limit``,
``--sort`` (prefix ``-`` for descending), ``--fields`` to trim the payload,
and ``--count`` to return only the count.
"""
from .dates import day_key
from .errors import UsageError
from .metrics import ar_bucket

KINDS = ("jobs", "opportunities", "invoices", "purchase-orders", "calls", "memberships", "technicians",
         "campaigns", "business-units", "people")


def _contains(value, needle):
    return needle is None or (value is not None and str(needle).lower() in str(value).lower())


def _rows(q, kind, f):
    """Return (rows, date_field_for_range) for the kind, before range/limit."""
    if kind == "jobs":
        rows = []
        for j in q.jobs:
            status = f.get("status") or "all"
            if status == "completed" and j.get("future"):
                continue
            if status == "booked" and not j.get("future"):
                continue
            if f.get("dept") and (j.get("dept") or "").lower() != f["dept"].lower():
                continue
            if f.get("billed") is True and not j["revenue"] > 0:
                continue
            if f.get("billed") is False and j["revenue"] > 0:
                continue
            if f.get("min_revenue") is not None and j["revenue"] < f["min_revenue"]:
                continue
            tech_name = q.person_name(j.get("tech")) if j.get("tech") else None
            if not _contains(tech_name, f.get("tech")):
                continue
            if not _contains(j.get("customer"), f.get("customer")):
                continue
            if f.get("job_number") and str(j.get("jobNumber")) != str(f["job_number"]):
                continue
            camp = q.campaign_name(j.get("campaign")) if j.get("campaign") else None
            if not _contains(camp, f.get("campaign")):
                continue
            rows.append({"id": j["id"], "jobNumber": j.get("jobNumber"), "date": j["dt"].isoformat(), "tenant": j.get("loc"),
                         "dept": j.get("dept"), "status": j.get("status") or ("Scheduled" if j.get("future") else "Completed"),
                         "booked": bool(j.get("future")), "revenue": round(j["revenue"], 2),
                         "technician": tech_name, "customer": j.get("customer"), "jobType": j.get("jobType"),
                         "businessUnit": j.get("businessUnit"), "campaign": camp, "options": j.get("options"),
                         "lowTicket": bool(j.get("lowTicket")), "membershipSold": bool(j.get("membershipSold")),
                         "noCharge": bool(j.get("noCharge")), "_dt": j["dt"]})
        return rows
    if kind == "opportunities":
        rows = []
        date_field = f.get("date_field") or "presented"
        for o in q.opportunities:
            if f.get("won") is True and not o["won"]:
                continue
            if f.get("won") is False and o["won"]:
                continue
            owner = q.person_name(o.get("owner"))
            if not _contains(owner, f.get("rep")):
                continue
            if not _contains(o.get("customer"), f.get("customer")):
                continue
            if f.get("min_value") is not None and max(o["value"], o["top_option"]) < f["min_value"]:
                continue
            d = o["won_dt"] if date_field == "won" else o["presented_dt"]
            rows.append({"id": o["id"], "tenant": o.get("loc"), "dept": o.get("dept"), "customer": o.get("customer"),
                         "city": o.get("city"), "jobNumber": o.get("jobNumber"), "title": o.get("title"), "rep": owner,
                         "presentedOn": o["presented_dt"].isoformat() if o["presented_dt"] else None,
                         "wonOn": o["won_dt"].isoformat() if o["won_dt"] else None, "won": o["won"],
                         "options": o["options"], "value": round(o["value"], 2), "bestOption": round(o["top_option"], 2),
                         "_dt": d})
        return rows
    if kind == "invoices":
        rows = []
        for a in q.ar:
            if not _contains(a.get("customer"), f.get("customer")):
                continue
            if f.get("min_days_past_due") is not None and a["days_ago"] < f["min_days_past_due"]:
                continue
            if f.get("min_balance") is not None and a["amount"] < f["min_balance"]:
                continue
            b = ar_bucket(a["days_ago"])
            if f.get("bucket") and b != f["bucket"]:
                continue
            rows.append({"id": a["id"], "invoiceNumber": a.get("invoiceNumber"), "tenant": a.get("loc"),
                         "customer": a.get("customer"), "businessUnit": a.get("businessUnit"),
                         "invoiceDate": a.get("invoiceDate"), "dueDate": a.get("dueDate"),
                         "daysPastDue": a["days_ago"], "bucket": b, "balance": round(a["amount"], 2),
                         "total": a.get("total"), "_dt": a["basis_dt"]})
        return rows
    if kind == "purchase-orders":
        rows = []
        for p in q.purchase_orders:
            if not _contains(p.get("vendor"), f.get("vendor")):
                continue
            if f.get("po_status") and (p.get("status") or "").lower() != f["po_status"].lower():
                continue
            rows.append({"id": p["id"], "number": p.get("number"), "tenant": p.get("loc"), "vendor": p.get("vendor"),
                         "date": p.get("date"), "status": p.get("status"), "total": round(p["amount"], 2),
                         "_dt": p.get("date_dt")})
        return rows
    if kind == "calls":
        rows = []
        for c in q.calls:
            if f.get("direction") == "inbound" and not c["inbound"]:
                continue
            if f.get("direction") == "outbound" and c["inbound"]:
                continue
            if not _contains(c.get("agentName"), f.get("agent")):
                continue
            camp = q.campaign_name(c.get("campaign")) if c.get("campaign") else None
            if not _contains(camp, f.get("campaign")):
                continue
            rows.append({"date": c.get("date"), "tenant": c.get("loc"), "agent": c.get("agentName"), "campaign": camp,
                         "inbound": c["inbound"], "calls": c["n"], "booked": c["booked"], "abandoned": c["abandoned"],
                         "_dt": c["dt"]})
        return rows
    if kind == "memberships":
        rows = []
        for m in q.memberships:
            if f.get("membership_status") and (m.get("status") or "").lower() != f["membership_status"].lower():
                continue
            if not _contains(m.get("type"), f.get("plan")):
                continue
            if not _contains(m.get("customer"), f.get("customer")):
                continue
            days_out = (m["renew_dt"] - q.as_of).total_seconds() / 86400.0 if m["renew_dt"] else None
            if f.get("renews_within") is not None and (days_out is None or not 0 <= days_out <= f["renews_within"]):
                continue
            rows.append({"id": m["id"], "tenant": m.get("loc"), "status": m.get("status"), "plan": m.get("type"),
                         "customer": m.get("customer"), "from": m.get("from"), "to": m.get("to"),
                         "daysToRenewal": round(days_out) if days_out is not None else None,
                         "soldBy": q.person_name(m.get("soldBy")) if m.get("soldBy") else None, "_dt": m["start_dt"]})
        return rows
    if kind == "technicians":
        rows = []
        for t in q.technicians:
            if f.get("dept") and (t.get("dept") or "").lower() != f["dept"].lower():
                continue
            if f.get("active") is not None and bool(t.get("active", True)) != f["active"]:
                continue
            if not _contains(t.get("name"), f.get("name")):
                continue
            rows.append({"id": t["id"], "name": t.get("name"), "tenant": t.get("loc"), "dept": t.get("dept"),
                         "active": t.get("active"), "_dt": None})
        return rows
    if kind == "campaigns":
        rows = []
        for c in q.campaigns:
            if not _contains(c.get("category"), f.get("category")):
                continue
            if not _contains(c.get("name"), f.get("name")):
                continue
            if f.get("active") is not None and bool(c.get("active", True)) != f["active"]:
                continue
            rows.append({"id": c["id"], "name": c.get("name"), "category": c.get("category"), "tenant": c.get("loc"),
                         "active": c.get("active"), "_dt": None})
        return rows
    if kind == "business-units":
        rows = []
        for b in q.data.get("businessUnits") or []:
            if f.get("dept") and (b.get("dept") or "").lower() != f["dept"].lower():
                continue
            if not _contains(b.get("name"), f.get("name")):
                continue
            rows.append({"id": b["id"], "name": b.get("name"), "tenant": b.get("loc"), "dept": b.get("dept") or "EXCLUDED",
                         "active": b.get("active"), "_dt": None})
        return rows
    if kind == "people":
        rows = []
        for pid, name in q.people.items():
            if not _contains(name, f.get("name")):
                continue
            rows.append({"id": pid, "name": name, "tenant": pid.split(":", 1)[0] if ":" in pid else None, "_dt": None})
        return rows
    raise UsageError("Unknown list kind %r" % kind, hint="Choose from: %s" % ", ".join(KINDS))


def list_records(q, kind, filters=None, limit=100, sort=None, fields=None, count_only=False, use_range=False):
    f = filters or {}
    rows = _rows(q, kind, f)
    rows = [r for r in rows if q.loc_match(r.get("tenant"))] if q.tenant else rows
    if use_range:
        rows = [r for r in rows if r.get("_dt") is not None and q.in_range(r["_dt"])]
    total = len(rows)
    if sort:
        desc = sort.startswith("-")
        key = sort.lstrip("-+")
        if rows and key not in rows[0]:
            raise UsageError("Cannot sort %s by %r" % (kind, key), hint="Fields: %s" % ", ".join(k for k in rows[0] if not k.startswith("_")))
        rows.sort(key=lambda r: (r.get(key) is None, r.get(key) if r.get(key) is not None else 0), reverse=desc)
    elif rows and rows[0].get("_dt") is not None:
        rows.sort(key=lambda r: r["_dt"] or q.as_of, reverse=True)
    if count_only:
        rows = []
    elif limit and limit > 0:
        rows = rows[:limit]
    for r in rows:
        r.pop("_dt", None)
    if fields:
        want = [x.strip() for x in fields.split(",") if x.strip()]
        rows = [{k: r.get(k) for k in want} for r in rows]
    out = {"kind": kind, "profile": q.data.get("profile"), "tenant": q.tenant or "all",
           "range": ({"kind": q.range_kind, "start": q.start.isoformat(), "end": q.end.isoformat()} if use_range else None),
           "filters": {k: v for k, v in f.items() if v is not None}, "total": total,
           "returned": (0 if count_only else len(rows)),
           "snapshot": q.snapshot.describe(as_of=q.as_of)}
    if not count_only:
        out["records"] = rows
    return out
