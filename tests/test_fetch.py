"""pull_tenant against a fake client: the derived-number rules from the docstring."""
from servicetitan_cli.fetch import build_snapshot, dept_for_business_unit, pull_tenant
from servicetitan_cli.store import new_profile


class FakeClient(object):
    def __init__(self, data):
        self.data = data
        self.calls = []
        self.requests_made = 0

    def get_all(self, path, params=None, max_pages=None, label=None):
        self.calls.append((path, dict(params or {})))
        for key, rows in self.data.items():
            if path.endswith(key):
                if isinstance(rows, dict):
                    return rows.get(params.get("jobStatus"), [])
                return rows
        return []

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path.endswith("estimates/501"):
            return {"id": 501, "status": {"name": "Sold"}, "items": [{"total": "2500.5"}, {"total": 100}]}
        if path.endswith("technicians/77"):
            return None
        if path.endswith("employees/77"):
            return {"id": 77, "name": "Old Employee"}
        return None


def fake_data():
    return {
        "business-units": [{"id": 1, "name": "HVAC Service", "active": True}, {"id": 2, "name": "Install Dept"},
                           {"id": 3, "name": "Warehouse"}],
        "technicians": [{"id": 10, "name": "Tech Ten", "businessUnitId": 1, "active": True}],
        "employees": [{"id": 20, "name": "Office Person"}, {"id": 10, "name": "Clashing Employee"}],
        "job-types": [{"id": 5, "name": "No Cool"}],
        "jobs": {
            "Completed": [
                {"id": 100, "jobNumber": "J100", "businessUnitId": 1, "completedOn": "2026-09-05T15:00:00Z", "total": "120.00",
                 "customerId": 900, "estimateIds": [501, 502], "campaignId": 700, "jobTypeId": 5, "noCharge": False},
                {"id": 101, "businessUnitId": 3, "completedOn": "2026-09-05T15:00:00Z", "total": 999},  # warehouse -> excluded
            ],
            "Scheduled": [
                {"id": 200, "businessUnitId": 2, "total": 0, "estimateIds": [501], "customerId": 901},   # priced by sold estimate
                {"id": 201, "businessUnitId": 2, "total": 5000, "estimateIds": []},                      # no future appt -> stale
            ],
            "Dispatched": [{"id": 202, "businessUnitId": 1, "total": 275, "estimateIds": [], "customerId": 900}],
            "InProgress": [], "Hold": [],
        },
        "appointments": [{"jobId": 200, "start": "2026-10-02T14:00:00Z"}, {"jobId": 200, "start": "2026-09-25T14:00:00Z"},
                         {"jobId": 202, "start": "2026-09-16T20:00:00Z"}],
        "appointment-assignments": [{"jobId": 100, "technicianId": 10, "technicianName": "Tech Ten"},
                                    {"jobId": 200, "technicianId": 77, "technicianName": "Old Employee"}],
        "customers": [{"id": 900, "name": "Cust Nine", "address": {"city": "Denver"}}],
        "memberships": [{"id": 300, "customerId": 900, "createdOn": "2026-09-05T18:00:00Z", "membershipTypeId": 8,
                         "status": "Active", "from": "2026-09-05", "to": "2027-09-05", "soldById": 10}],
        "membership-types": [{"id": 8, "name": "Comfort Plan"}],
        "estimates": [
            {"id": 501, "jobId": 100, "customerId": 900, "businessUnitId": 1, "name": "Best", "createdOn": "2026-09-01T10:00:00Z",
             "status": {"name": "Sold"}, "soldOn": "2026-09-03T10:00:00Z", "soldBy": 10, "items": [{"total": 2500.5}, {"total": 100}], "jobNumber": "J100"},
            {"id": 502, "jobId": 100, "customerId": 900, "businessUnitId": 1, "name": "Good", "createdOn": "2026-09-01T09:00:00Z",
             "status": {"name": "Open"}, "items": [{"total": 900}]},
            {"id": 503, "jobId": 200, "businessUnitId": 2, "createdOn": "2026-09-02T09:00:00Z", "status": {"name": "Open"},
             "items": [{"total": 4000}]},
            {"id": 504, "jobId": None, "businessUnitId": 1, "createdOn": "2026-09-02T09:00:00Z", "status": {"name": "Dismissed"},
             "items": [{"total": 50}]},
        ],
        "invoices": [
            {"id": 400, "balance": "250.00", "invoiceDate": "2026-08-01T00:00:00Z", "dueDate": "2026-08-31T00:00:00Z",
             "customer": {"name": "Cust Nine"}, "businessUnit": {"name": "HVAC Service"}, "total": 250, "referenceNumber": "INV-400"},
            {"id": 401, "balance": 300, "invoiceDate": None},   # pre-created, unbilled -> excluded
            {"id": 402, "balance": 0, "invoiceDate": "2026-08-01T00:00:00Z"},
        ],
        "purchase-orders": [{"id": 600, "vendorId": 61, "status": "Pending", "total": "80.5", "date": "2026-09-01", "number": "PO-600"},
                            {"id": 601, "vendorId": 61, "status": "Received", "total": 999}],
        "vendors": [{"id": 61, "name": "Parts Co"}],
        "campaigns": [{"id": 700, "name": " Referral ", "category": {"name": "Referral"}, "active": True}],
        "calls": [
            {"id": 1, "leadCall": {"receivedOn": "2026-09-04T10:00:00Z", "direction": "Inbound", "callType": "Booked",
                                   "agent": {"name": "Agent A"}, "campaign": {"id": 700}}},
            {"id": 2, "leadCall": {"receivedOn": "2026-09-04T11:00:00Z", "direction": "Inbound", "callType": "Unbooked",
                                   "agent": {"name": "Agent A"}, "campaign": {"id": 700}}},
            {"id": 3, "leadCall": {"createdOn": "2026-09-04T12:00:00Z", "direction": "Inbound", "callType": "Abandoned"}},
            {"id": 4, "leadCall": {}},
        ],
    }


def config():
    cfg = new_profile("Acme")
    cfg["tenants"] = [{"code": "SPR", "label": "Springfield", "tenantId": "1", "clientId": "c"}]
    return cfg


def test_dept_mapping_order_and_exclusion():
    mapping = {"Install": ["install"], "Service": ["service", "maint"], "exclude": ["warehouse"]}
    assert dept_for_business_unit("HVAC Service", mapping) == "Service"
    assert dept_for_business_unit("Install Dept", mapping) == "Install"
    assert dept_for_business_unit("Main Warehouse", mapping) is None
    assert dept_for_business_unit("Admin", mapping) == "Other"


def test_pull_tenant_applies_the_rules():
    client = FakeClient(fake_data())
    cfg = config()
    out = pull_tenant(client, cfg, cfg["tenants"][0], "2026-01-01T00:00:00Z", "2025-01-01T00:00:00Z", log=lambda m: None)

    jobs = {j["id"]: j for j in out["jobs"]}
    assert "SPR:101" not in jobs  # warehouse business unit dropped
    assert set(jobs) == {"SPR:100", "SPR:200", "SPR:202"}  # 201 had no future appointment -> stale, skipped
    assert jobs["SPR:202"]["future"] is True and jobs["SPR:202"]["revenue"] == 275  # Dispatched jobs are open work
    assert out["people"]["SPR:10"] == "Tech Ten"  # technician name wins over an employee with the same raw id
    assert out["stats"]["staleOpenJobsSkipped"] == 1
    completed = jobs["SPR:100"]
    assert completed["revenue"] == 120.0 and completed["tech"] == "SPR:10" and completed["lowTicket"] is True
    assert completed["membershipSold"] is True and completed["options"] == 2 and completed["campaign"] == "SPR:700"
    assert completed["customer"] == "Cust Nine" and completed["jobType"] == "No Cool" and completed["jobNumber"] == "J100"
    booked = jobs["SPR:200"]
    assert booked["future"] is True and booked["date"] == "2026-09-25T14:00:00Z"  # earliest future appointment
    assert booked["revenue"] == 2600.5  # sold-estimate fallback for an unpriced booked job

    opps = {o["id"]: o for o in out["opportunities"]}
    assert set(opps) == {"SPR:100", "SPR:200", "SPR:est-504"}
    won = opps["SPR:100"]
    assert won["won"] is True and won["options"] == 2 and won["value"] == 2600.5 and won["topOption"] == 2600.5
    assert won["owner"] == "SPR:10" and won["title"] == "Best" and won["customer"] == "Cust Nine" and won["city"] == "Denver"
    assert won["presentedOn"] == "2026-09-01T09:00:00Z" and won["wonOn"] == "2026-09-03T10:00:00Z"
    unsold = opps["SPR:200"]
    assert unsold["won"] is False and unsold["owner"] == "SPR:77" and unsold["value"] == 0 and unsold["topOption"] == 4000
    assert out["people"]["SPR:77"] == "Old Employee"  # resolved via the individual lookup
    assert out["estimateCount"] == 4

    assert [a["id"] for a in out["ar"]] == ["SPR:400"] and out["ar"][0]["balance"] == 250.0
    assert out["ar"][0]["invoiceNumber"] == "INV-400" and out["stats"]["unbilledInvoicesExcluded"] == 1
    inv_params = [p for path, p in client.calls if path.endswith("invoices")][0]
    assert inv_params == {"invoicedOnOrAfter": "2025-01-01T00:00:00Z"}
    for roster in ("technicians", "employees", "business-units", "customers", "campaigns", "vendors"):
        assert [p for path, p in client.calls if path.endswith(roster)][0].get("active") == "Any", roster

    assert [p["id"] for p in out["purchaseOrders"]] == ["SPR:600"] and out["purchaseOrders"][0]["vendor"] == "Parts Co"
    assert out["poTotals"] == {"SPR": 2}
    assert out["campaigns"][0]["name"] == "Referral" and out["campaigns"][0]["category"] == "Referral"

    calls = {(c["date"], c["agentName"], c["campaign"]): c for c in out["calls"]}
    assert calls[("2026-09-04", "Agent A", "SPR:700")]["n"] == 2 and calls[("2026-09-04", "Agent A", "SPR:700")]["booked"] == 1
    assert calls[("2026-09-04", None, None)]["abandoned"] == 1 and out["rawCallCount"] == 4
    assert out["memberships"][0]["type"] == "Comfort Plan" and out["memberships"][0]["soldBy"] == "SPR:10"
    assert out["technicians"][0]["dept"] == "Service" and out["businessUnits"][2]["dept"] is None


def test_build_snapshot_merges_tenants(isolated_home, monkeypatch):
    from servicetitan_cli.config import Context
    monkeypatch.setenv("SERVICETITAN_TENANT_ID", "1")
    monkeypatch.setenv("SERVICETITAN_CLIENT_ID", "c")
    monkeypatch.setenv("SERVICETITAN_CLIENT_SECRET", "s")
    monkeypatch.setenv("SERVICETITAN_APP_KEY", "k")
    ctx = Context()
    snap = build_snapshot(ctx, quiet=True, client_factory=lambda creds, **kw: FakeClient(fake_data()))
    assert snap["isDemo"] is False and snap["profile"] == "env" and snap["tenants"][0]["code"] == "MAIN"
    assert len(snap["jobs"]) == 3 and snap["pulls"]["MAIN"]["completedJobs"] == 1 and snap["cli"]["version"]
    assert all(j["id"].startswith("MAIN:") for j in snap["jobs"])
