"""Snapshot checks, the endpoint catalogue and the CLI contract end to end."""
import json
import os

import pytest
from conftest import run_cli

from servicetitan_cli import endpoints
from servicetitan_cli.snapshot import check_snapshot, find_duplicates, problems


def test_check_snapshot_flags_every_failure_mode(demo_data):
    ok = check_snapshot(demo_data, ["SPR", "LOC2"], synthetic=True)
    assert not problems(ok)
    broken = json.loads(json.dumps(demo_data))
    broken["isDemo"] = False
    broken["generatedAt"] = "2020-01-01T00:00:00Z"
    broken["jobs"] = [j for j in broken["jobs"] if j["loc"] == "SPR"][:5]
    broken["jobs"][0]["tech"] = "LOC2:999"
    broken["opportunities"][0]["owner"] = "SPR:unknown"
    failed = {p["check"]: p["detail"] for p in problems(check_snapshot(broken, ["SPR", "LOC2"], max_age_hours=6))}
    assert "floor:jobs" in failed and "tenant-present:LOC2" in failed
    assert "no-cross-tenant-ids" in failed and "owners-resolved" in failed and "fresh" in failed


def test_find_duplicates():
    data = {"people": {"A:1": "Robin Vance", "A:2": "Robin Vance (T)", "A:3": "Sam Other", "A:4": "Robin Vance"},
            "opportunities": [{"owner": "A:2", "won": True, "value": 10}], "jobs": [{"tech": "A:1"}]}
    groups = find_duplicates(data)
    assert len(groups) == 1 and groups[0]["names"] == ["Robin Vance", "Robin Vance (T)"]
    assert groups[0]["suggestedAlias"] == {"A:2": "Robin Vance"}
    assert {r["id"]: r["jobs"] for r in groups[0]["records"]}["A:1"] == 1


def test_endpoint_catalogue():
    cat = endpoints.catalogue()
    assert cat["count"] >= 590 and cat["readCount"] >= 320 and len(cat["operations"]) == cat["count"]
    jobs = endpoints.search("jobs", module="jpm")
    assert any(e["path"] == "/jpm/v2/tenant/{tenant}/jobs" for e in jobs) and all(e["method"] == "GET" for e in jobs)
    assert all(e["export"] for e in endpoints.search(exports_only=True))
    assert any(e["method"] != "GET" for e in endpoints.search("jobs", module="jpm", methods=None))
    e = endpoints.by_path("/jpm/v2/tenant/{tenant}/jobs")
    assert e and "jobStatus" in [p["name"] for p in e["params"]] and "total" in e["fields"] and e["scope"] == "tn.jpm.jobs:r"
    assert any(m["module"] == "telecom" for m in endpoints.modules())
    from servicetitan_cli.fetch import FETCH_ENDPOINTS
    needed = endpoints.scopes_for(FETCH_ENDPOINTS)
    assert all(needed.values()), "every fetch endpoint must be in the catalogue with a scope: %s" % needed
    assert all(s.endswith(":r") for s in needed.values())
    granted = [s for s in needed.values() if s != "tn.tlc.calls:r"]
    assert endpoints.missing_scopes(granted, FETCH_ENDPOINTS) == ["tn.tlc.calls:r"]
    assert endpoints.missing_scopes(None, FETCH_ENDPOINTS) == []


def test_api_tree_resolution():
    from servicetitan_cli.errors import UsageError
    assert endpoints.resolve_module("jpm") == "jpm" and endpoints.resolve_module("sales") == "salestech"
    assert endpoints.resolve_module("tenant-crm-v2") == "crm" and endpoints.resolve_module("JOBS") == "jpm"
    with pytest.raises(UsageError) as ei:
        endpoints.resolve_module("accounts")
    assert "accounting" in ei.value.hint
    assert endpoints.resolve_resource("jpm", "job_types") == "job-types"
    with pytest.raises(UsageError) as ei:
        endpoints.resolve_resource("jpm", "job")
    assert "jobs" in ei.value.hint
    op = endpoints.resolve_operation("jpm", "jobs", "list")
    assert op["method"] == "GET" and op["kind"] == "paged"
    get = endpoints.resolve_operation("jpm", "jobs", "get")
    assert [p["name"] for p in get["params"] if p["in"] == "path"] == ["id"]
    names = {o["operation"] for o in endpoints.operations("telecom", "calls")}
    assert {"list", "list-v2", "export", "get", "recording"} <= names
    est = {o["operation"]: o for o in endpoints.operations("salestech", "estimates")}
    assert est["export-legacy"]["path"].endswith("/estimates/export") and est["export-legacy"]["write"] is False
    assert est["create"]["executable"] is True and est["create"]["write"] is True
    d = endpoints.describe(op)
    assert d["example"].startswith("servicetitan api jpm jobs list") and any(q["name"] == "jobStatus" and q.get("enum") for q in d["queryParams"])
    assert "total" in d["responseFields"]
    r = {x["resource"]: x for x in endpoints.resources("jpm")}
    assert "list" in r["jobs"]["reads"] and "create" in r["jobs"]["writes"]


class FakeTreeClient(object):
    def __init__(self, creds, progress=None):
        self.creds = creds
        self.calls = []
        self.last_truncated = False

    def resolve_url(self, path):
        return "https://api.servicetitan.io" + path.replace("{tenant}", "555")

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if params and params.get("includeTotal") == "true":
            return {"data": [{"id": 1}], "hasMore": True, "totalCount": 321}
        if path.endswith("/jobs/12345"):
            return {"id": 12345, "jobNumber": "J-1", "total": 99.5}
        return {"data": [{"id": 1, "jobNumber": "A", "total": 1}, {"id": 2, "jobNumber": "B", "total": 2}],
                "page": params.get("page", 1), "pageSize": 50, "hasMore": False}

    def get_pages(self, path, params=None, max_pages=None, page_size=None):
        yield 1, [{"id": 1}], {}
        yield 2, [{"id": 2}], {}

    def export(self, path, from_token=None, include_recent_changes=False, max_batches=None):
        self.calls.append((path, {"from": from_token, "recent": include_recent_changes}))
        yield [{"id": 9}], "tok-1", False


def test_api_tree_parser_and_execution(isolated_home, monkeypatch):
    from servicetitan_cli import apitree
    from servicetitan_cli.config import Context
    from servicetitan_cli.errors import UsageError
    for k, v in (("SERVICETITAN_TENANT_ID", "555"), ("SERVICETITAN_CLIENT_ID", "c"), ("SERVICETITAN_CLIENT_SECRET", "s"),
                 ("SERVICETITAN_APP_KEY", "k")):
        monkeypatch.setenv(k, v)
    ctx = Context()
    op = endpoints.resolve_operation("jpm", "jobs", "list")
    parser = apitree.build_parser(op)
    ns = parser.parse_args(["--job-status", "completed", "--page-size", "10", "--completed-on-or-after", "2026-09-01T00:00:00Z",
                            "--fields", "jobNumber,total"])
    assert ns.q_job_status == "Completed"  # case-insensitive enum mapped to the spec's spelling
    with pytest.raises(SystemExit):
        parser.parse_args(["--job-status", "Finished"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--page-size", "ten"])
    holder = {}

    def factory(creds, **kw):
        holder["client"] = FakeTreeClient(creds)
        return holder["client"]
    env = apitree.execute(op, ns, ctx, client_factory=factory)
    path, params = holder["client"].calls[0]
    assert path == "/jpm/v2/tenant/{tenant}/jobs" and params == {"jobStatus": "Completed", "pageSize": 10,
                                                                  "completedOnOrAfter": "2026-09-01T00:00:00Z"}
    assert env["source"] == "live" and env["count"] == 2 and env["data"] == [{"jobNumber": "A", "total": 1}, {"jobNumber": "B", "total": 2}]
    assert env["operation"] == "jpm jobs list" and env["url"].endswith("/tenant/555/jobs")

    get_op = endpoints.resolve_operation("jpm", "jobs", "get")
    ns = apitree.build_parser(get_op).parse_args(["12345"])
    env = apitree.execute(get_op, ns, ctx, client_factory=factory)
    assert env["data"]["jobNumber"] == "J-1" and holder["client"].calls[-1][0] == "/jpm/v2/tenant/{tenant}/jobs/12345"

    ns = apitree.build_parser(op).parse_args(["--count"])
    assert apitree.execute(op, ns, ctx, client_factory=factory)["totalCount"] == 321
    ns = apitree.build_parser(op).parse_args(["--all", "--max-pages", "5"])
    assert apitree.execute(op, ns, ctx, client_factory=factory)["count"] == 2

    exp = endpoints.resolve_operation("jpm", "jobs", "export")
    ns = apitree.build_parser(exp).parse_args(["--from", "2026-01-01", "--include-recent-changes", "true"])
    env = apitree.execute(exp, ns, ctx, client_factory=factory)
    assert env["continueFrom"] == "tok-1" and holder["client"].calls[-1][1] == {"from": "2026-01-01", "recent": True}

    write = endpoints.resolve_operation("jpm", "jobs", "create")
    ns = apitree.build_parser(write).parse_args([])
    with pytest.raises(UsageError) as ei:
        apitree.execute(write, ns, ctx, client_factory=factory)
    assert "--write" in ei.value.message and "list" in ei.value.hint


def test_cli_api_tree_discovery(capsys, isolated_home):
    code, out, _ = run_cli(capsys, "api")
    assert code == 0 and out["source"] == "catalogue" and any(m["module"] == "jpm" for m in out["modules"])
    code, out, _ = run_cli(capsys, "api", "jpm")
    assert code == 0 and any(r["resource"] == "jobs" and "list" in r["reads"] for r in out["resources"])
    code, out, _ = run_cli(capsys, "api", "sales", "estimates")
    assert code == 0 and out["module"] == "salestech" and any(o["operation"] == "list" for o in out["operations"])
    code, out, _ = run_cli(capsys, "api", "jpm", "jobs", "list", "--describe")
    assert code == 0 and out["method"] == "GET" and any(q["name"] == "jobStatus" for q in out["queryParams"])
    code, out, err = run_cli(capsys, "api", "jpm", "jobs", "list", "--help")
    assert code == 0 and "--job-status" in out and "--completed-on-or-after" in out
    code, out, err = run_cli(capsys, "api", "jpm", "jobs", "--help")
    assert code == 0 and "servicetitan api jpm jobs <operation>" in out
    code, out, err = run_cli(capsys, "api", "accounts")
    assert code == 2 and "accounting" in json.loads(err)["error"]["hint"]
    code, out, err = run_cli(capsys, "api", "jpm", "jobs", "list")
    assert code == 3 and json.loads(err)["error"]["code"] == "CONFIG"  # executing needs credentials
    code, out, _ = run_cli(capsys, "api", "endpoints", "--search", "customers", "--module", "crm", "--limit", "3")
    assert code == 0 and out["count"] == 3 and out["endpoints"][0]["command"].startswith("servicetitan api crm")
    code, out, _ = run_cli(capsys, "api", "modules")
    assert code == 0 and len(out["modules"]) == 25


def test_cli_offline_flow(capsys, isolated_home, demo_path):
    code, out, err = run_cli(capsys, "status", "--data", demo_path)
    assert code == 0 and out["snapshot"]["isDemo"] is True and out["profile"] is None

    code, out, _ = run_cli(capsys, "metrics", "service", "--data", demo_path, "--range", "ytd", "--explain", "--compact")
    assert code == 0 and out["area"] == "service" and out["definitions"]["avgTicket"]
    assert out["kpis"]["callsCompleted"] > 0

    code, out, _ = run_cli(capsys, "list", "invoices", "--data", demo_path, "--sort", "balance", "--desc", "--limit", "2")
    assert code == 0 and out["returned"] == 2 and out["records"][0]["balance"] >= out["records"][1]["balance"]

    code, out, _ = run_cli(capsys, "list", "jobs", "--data", demo_path, "--range", "next30", "--booked", "--count")
    assert code == 0 and out["total"] > 0 and "records" not in out and out["range"]["kind"] == "next30"

    code, out, _ = run_cli(capsys, "list", "opportunities", "--data", demo_path, "--won", "--fields", "customer,value", "--limit", "1")
    assert code == 0 and set(out["records"][0]) == {"customer", "value"}

    code, out, _ = run_cli(capsys, "check", "--data", demo_path)
    assert code == 0 and out["ok"] is True

    code, out, _ = run_cli(capsys, "duplicates", "--data", demo_path)
    assert code == 0 and "groups" in out

    html = os.path.join(os.path.dirname(demo_path), "dash.html")
    code, out, _ = run_cli(capsys, "render", "--data", demo_path, "--out", html)
    assert code == 0 and os.path.getsize(html) > 100000 and out["isDemo"] is True
    text = open(html, encoding="utf-8").read()
    assert "__LIVE_DATA_JSON__" not in text and '"isDemo":true' in text
    assert '"jobType"' not in text  # agent-only fields stay out of the shared page
    code, out2, _ = run_cli(capsys, "render", "--data", demo_path, "--out", html, "--full")
    assert code == 0 and out2["bytes"] > out["bytes"] and '"jobType"' in open(html, encoding="utf-8").read()

    code, out, _ = run_cli(capsys, "schema")
    assert code == 0 and out["commands"]["command"] == "servicetitan"
    names = {c["command"] for c in out["commands"]["subcommands"]}
    assert {"servicetitan metrics", "servicetitan list", "servicetitan api", "servicetitan fetch"} <= names
    assert out["conventions"]["exitCodes"]["7"].startswith("snapshot")
    assert out["api"]["reads"] >= 320 and any(m["module"] == "jpm" and "jobs" in m["resources"] for m in out["api"]["modules"])


    code, out, _ = run_cli(capsys, "palette", "#2996f4,#ed6d28,#2ca67c", "--compact")
    assert code == 0 and out["ok"] is True


def test_cli_errors_are_structured(capsys, isolated_home, demo_path):
    code, out, err = run_cli(capsys, "metrics", "revenue", "--data", "missing.json")
    assert code == 7 and out is None and json.loads(err)["error"]["code"] == "DATA"

    code, out, err = run_cli(capsys, "list", "jobs", "--data", demo_path, "--range", "custom")
    assert code == 2 and json.loads(err)["error"]["code"] == "USAGE"

    code, out, err = run_cli(capsys, "metrics", "revenue", "--data", demo_path, "--start", "2026-06-30", "--end", "2026-06-01")
    assert code == 2 and "before its start" in json.loads(err)["error"]["message"]

    code, out, err = run_cli(capsys, "metrics", "service", "--data", demo_path, "--tenant", "NOPE")
    assert code == 2 and "SPR" in json.loads(err)["error"]["hint"]

    code, out, err = run_cli(capsys, "palette", "#2996f4,#2a97f5")
    assert code == 6 and json.loads(err)["error"]["details"]["ok"] is False

    code, out, err = run_cli(capsys, "fetch")
    assert code == 3 and json.loads(err)["error"]["code"] == "CONFIG"

    code, out, err = run_cli(capsys, "metrics", "revenue", "--data", demo_path, "--format", "text")
    assert code == 0 and isinstance(out, str) and out.startswith("area: revenue")

    code, out, err = run_cli(capsys, "metrics", "revenue", "--data", demo_path, "--as-of", "2026-03-31")
    assert code == 0 and out["range"]["start"].startswith("2026-03-01") and out["asOf"].startswith("2026-03-31T23:59:59")


def test_cli_profile_and_tenant_lifecycle(capsys, isolated_home, monkeypatch, demo_data):
    code, out, _ = run_cli(capsys, "profile", "add", "acme", "--company", "Acme", "--timezone", "America/Denver")
    assert code == 0 and out["isDefault"] is True
    monkeypatch.setenv("APP_KEY_VAR", "app-key-value")
    monkeypatch.setenv("CS_VAR", "client-secret-value")
    code, out, _ = run_cli(capsys, "tenant", "add", "acme", "--code", "main", "--label", "Main", "--tenant-id", "123",
                           "--client-id", "cid", "--client-secret-env", "CS_VAR", "--app-key-env", "APP_KEY_VAR", "--no-verify")
    assert code == 0 and out["added"]["code"] == "MAIN" and out["added"]["secrets"] == ["appKey", "clientSecret"]
    assert out["secrets"]["backend"] == "file"
    code, out, _ = run_cli(capsys, "profile", "show")
    assert code == 0 and "client-secret-value" not in json.dumps(out) and out["tenants"][0]["clientId"] == "cid"
    code, out, _ = run_cli(capsys, "profile", "set", "acme", "--targets", '{"avgTicket": 300}', "--rules", '{"boardDays": 30}')
    assert code == 0 and out["profile"]["targets"]["avgTicket"] == 300 and out["profile"]["rules"]["boardDays"] == 30
    code, out, _ = run_cli(capsys, "profile", "export")
    assert code == 0 and out["tenants"] == [{"code": "MAIN", "label": "Main"}] and "tenantId" not in json.dumps(out)
    code, out, _ = run_cli(capsys, "status")
    assert code == 0 and out["profile"]["tenants"][0]["secretsStored"] == ["appKey", "clientSecret"] and out["snapshot"]["exists"] is False
    code, out, _ = run_cli(capsys, "init")
    assert code == 0 and all(s["done"] for s in out["steps"] if s["step"] != "snapshot")
    from servicetitan_cli.paths import snapshot_path
    path = snapshot_path("acme")                      # the synthetic fixture stands in for a fetched snapshot
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(demo_data, f)
    assert path.endswith("acme.json")
    code, out, _ = run_cli(capsys, "metrics", "revenue", "--profile", "acme", "--range", "ytd")
    assert code == 0 and out["timezone"] == "America/Denver" and out["snapshot"]["isDemo"] is True
    code, out, _ = run_cli(capsys, "tenant", "remove", "acme", "--code", "MAIN")
    assert code == 0 and out["secretsDeleted"] == ["appKey", "clientSecret"]
    cred_file = os.path.join(str(isolated_home), "credentials.json")
    assert json.load(open(cred_file)) == {}
    code, out, _ = run_cli(capsys, "profile", "remove", "acme")
    assert code == 0 and out["default"] is None


def test_cli_import_legacy_layout(capsys, isolated_home, tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({
        "company": {"name": "Legacy Co"}, "locations": [{"code": "MAIN", "label": "Main"}], "targets": {"avgTicket": 250}}))
    (tmp_path / ".env").write_text("SERVICETITAN_APP_KEY=k\nSERVICETITAN_TENANT_ID=77\nSERVICETITAN_CLIENT_ID=c\n"
                                   "SERVICETITAN_CLIENT_SECRET=s\n")
    code, out, _ = run_cli(capsys, "profile", "import", "legacy", "--use")
    assert code == 0 and out["tenants"] == ["MAIN"] and set(out["secretsStored"]) == {"MAIN/clientSecret", "MAIN/appKey"}
    code, out, _ = run_cli(capsys, "profile", "show", "legacy")
    assert code == 0 and out["targets"]["avgTicket"] == 250 and out["tenants"][0]["tenantId"] == "77"
    os.remove(str(tmp_path / "config.json"))  # store-based profile keeps working without the legacy files
    code, out, _ = run_cli(capsys, "status")
    assert code == 0 and out["profile"]["source"] == "store" and out["profile"]["profile"] == "legacy"
