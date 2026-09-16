"""Snapshot checks, the endpoint catalogue and the CLI contract end to end."""
import json
import os

from conftest import run_cli

from servicetitan_cli import endpoints
from servicetitan_cli.snapshot import check_snapshot, find_duplicates, problems


def test_check_snapshot_flags_every_failure_mode(demo_data):
    ok = check_snapshot(demo_data, ["SPR", "LOC2"], demo=True)
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
    assert cat["count"] >= 300 and len(cat["endpoints"]) == cat["count"]
    jobs = endpoints.search("jobs", module="jpm-v2")
    assert any(e["path"] == "/jpm/v2/tenant/{tenant}/jobs" for e in jobs)
    assert all(e["export"] for e in endpoints.search(exports_only=True))
    e = endpoints.describe("/jpm/v2/tenant/{tenant}/jobs")
    assert e and "jobStatus" in e["query"] and "total" in e["fields"]
    assert any(m["module"] == "telecom" for m in endpoints.modules())


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

    code, out, _ = run_cli(capsys, "api", "endpoints", "--search", "customers", "--module", "crm-v2", "--limit", "3")
    assert code == 0 and out["count"] == 3

    code, out, _ = run_cli(capsys, "palette", "#2996f4,#ed6d28,#2ca67c", "--compact")
    assert code == 0 and out["ok"] is True


def test_cli_errors_are_structured(capsys, isolated_home, demo_path):
    code, out, err = run_cli(capsys, "metrics", "revenue", "--data", "missing.json")
    assert code == 7 and out is None and json.loads(err)["error"]["code"] == "DATA"

    code, out, err = run_cli(capsys, "list", "jobs", "--data", demo_path, "--range", "custom")
    assert code == 2 and json.loads(err)["error"]["code"] == "USAGE"

    code, out, err = run_cli(capsys, "palette", "#2996f4,#2a97f5")
    assert code == 6 and json.loads(err)["error"]["details"]["ok"] is False

    code, out, err = run_cli(capsys, "fetch")
    assert code == 3 and json.loads(err)["error"]["code"] == "CONFIG"

    code, out, err = run_cli(capsys, "metrics", "revenue", "--data", demo_path, "--format", "text")
    assert code == 0 and isinstance(out, str) and out.startswith("area: revenue")

    code, out, err = run_cli(capsys, "metrics", "revenue", "--data", demo_path, "--as-of", "2026-03-31")
    assert code == 0 and out["range"]["start"].startswith("2026-03-01") and out["asOf"].startswith("2026-03-31T23:59:59")


def test_cli_profile_and_tenant_lifecycle(capsys, isolated_home, monkeypatch):
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
    code, out, _ = run_cli(capsys, "demo", "--compact")
    assert code == 0 and out["written"].endswith("acme.json")
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
