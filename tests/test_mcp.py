"""`servicetitan mcp`: the st-operator MCP server over stdio."""
import io
import json
import os
import subprocess
import sys

import pytest

from conftest import run_cli
from servicetitan_cli import cli, mcp_server
from servicetitan_cli.api import ServiceTitanClient
from servicetitan_cli.output import progress

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
TOOLS = ["status", "brief", "metrics", "list_records", "search", "saved_results", "refresh", "api_discover", "api_read",
         "api_write"]


def session(messages, raw=b"", **kw):
    data = raw + b"".join((json.dumps(m) + "\n").encode("utf-8") for m in messages)
    out = io.BytesIO()
    mcp_server.serve(stdin=io.BytesIO(data), stdout=out, **kw)
    return [json.loads(line) for line in out.getvalue().decode("utf-8").splitlines()]


def call(mid, name, args=None, meta=None):
    params = {"name": name, "arguments": args or {}}
    if meta:
        params["_meta"] = meta
    return {"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": params}


def one(name, args=None, **kw):
    """Initialize, call one tool, return (isError, parsed first content block)."""
    replies = session([INIT, INITIALIZED, call(2, name, args)], **kw)
    result = replies[-1]["result"]
    return result["isError"], json.loads(result["content"][0]["text"]), result


@pytest.fixture
def snapshot_env(isolated_home, monkeypatch, demo_path):
    monkeypatch.setenv("SERVICETITAN_DATA", demo_path)
    return demo_path


@pytest.fixture
def env_creds(isolated_home, monkeypatch):
    for k, v in (("SERVICETITAN_TENANT_ID", "555"), ("SERVICETITAN_CLIENT_ID", "c"), ("SERVICETITAN_CLIENT_SECRET", "s"),
                 ("SERVICETITAN_APP_KEY", "k")):
        monkeypatch.setenv(k, v)


# ---------------------------------------------------------------- protocol
def test_handshake_tool_list_and_protocol_edges(isolated_home):
    replies = session([INIT, INITIALIZED, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                       {"jsonrpc": "2.0", "id": 3, "method": "ping"},
                       {"jsonrpc": "2.0", "id": 4, "method": "no/such/method"},
                       {"jsonrpc": "2.0", "id": 5, "method": "resources/list"},
                       {"jsonrpc": "2.0", "id": 6, "method": "initialize", "params": {"protocolVersion": "2099-01-01"}},
                       {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "nope"}}])
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5, 6, 7]          # the notification got no reply
    init = replies[0]["result"]
    assert init["protocolVersion"] == "2025-06-18" and init["capabilities"] == {"tools": {"listChanged": False}}
    assert init["serverInfo"]["name"] == "st-operator" and "third-party" in init["serverInfo"]["title"]
    assert "not affiliated with ServiceTitan" in init["instructions"] and "servicetitan setup" in init["instructions"]
    tools = {t["name"]: t for t in replies[1]["result"]["tools"]}
    assert list(tools) == TOOLS
    assert all(t["inputSchema"]["type"] == "object" and t["description"] for t in tools.values())
    assert tools["status"]["annotations"]["readOnlyHint"] is True and tools["api_read"]["annotations"]["openWorldHint"]
    assert tools["api_write"]["annotations"]["destructiveHint"] is True
    assert tools["api_write"]["annotations"]["readOnlyHint"] is False
    assert tools["metrics"]["inputSchema"]["properties"]["area"]["enum"][0] == "revenue"     # straight from argparse
    assert "min_days_past_due" in tools["list_records"]["inputSchema"]["properties"]
    assert tools["refresh"]["inputSchema"]["properties"]["max_age"]["type"] == "number"
    assert replies[2]["result"] == {} and replies[3]["error"]["code"] == -32601
    assert replies[4]["result"] == {"resources": []}
    assert replies[5]["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSIONS[0]    # unknown version: our newest
    assert replies[6]["error"]["code"] == -32602 and "nope" in replies[6]["error"]["message"]


def test_parse_errors_and_batches(isolated_home):
    replies = session([[{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": 2, "method": "ping"}]],
                      raw=b"this is not json\n\n")
    assert replies[0]["error"]["code"] == -32700 and replies[0]["id"] is None
    assert [r["id"] for r in replies[1]] == [1, 2]


def test_read_only_mode_has_no_write_tool(isolated_home):
    replies = session([INIT, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                       call(3, "api_write", {"module": "jpm", "resource": "jobs", "operation": "create"})], read_only=True)
    assert "api_write" not in [t["name"] for t in replies[1]["result"]["tools"]]
    assert replies[2]["error"]["code"] == -32602


# ---------------------------------------------------------------- tools over the snapshot
def test_status_without_setup_points_to_setup(isolated_home):
    is_error, out, _ = one("status")
    assert is_error is False and out["checklist"]["next"] == "servicetitan setup"
    assert out["status"]["error"]["code"] == "CONFIG"


def test_snapshot_tools_answer_like_the_cli(snapshot_env):
    is_error, brief, _ = one("brief")
    assert is_error is False and brief["source"] == "snapshot" and "monthToDate" in brief
    is_error, metrics, _ = one("metrics", {"area": "service", "range": "mtd", "explain": True})
    assert is_error is False and metrics["area"] == "service" and "avgTicket" in metrics["kpis"] and metrics["definitions"]
    is_error, rows, _ = one("list_records", {"kind": "invoices", "min_days_past_due": 30, "sort": "-balance", "limit": 3,
                                             "fields": ["invoiceNumber", "balance"]})
    balances = [r["balance"] for r in rows["records"]]
    assert is_error is False and 0 < len(balances) <= 3 and balances == sorted(balances, reverse=True)
    assert all(set(r) == {"invoiceNumber", "balance"} for r in rows["records"])
    is_error, counted, _ = one("list_records", {"kind": "jobs", "count": True})
    assert is_error is False and counted["total"] > 100 and "records" not in counted


def test_bad_arguments_come_back_as_tool_errors(snapshot_env):
    is_error, out, _ = one("list_records", {"kind": "widgets"})
    assert is_error is True and out["error"]["code"] == "USAGE" and "kind must be one of" in out["error"]["message"]
    is_error, out, _ = one("list_records", {"kind": "jobs", "colour": "blue"})
    assert is_error is True and "unknown argument(s): colour" in out["error"]["message"]
    is_error, out, _ = one("list_records", {"kind": "jobs", "limit": "ten"})
    assert is_error is True and "limit must be an integer" in out["error"]["message"]
    is_error, out, _ = one("metrics", {"area": "service", "start": "not-a-date"})
    assert is_error is True and out["error"]["code"] == "USAGE"           # rejected by the CLI's own validation
    is_error, out, _ = one("metrics", {})
    assert is_error is True and "area is required" in out["error"]["message"]


def test_save_then_find_the_saved_result(snapshot_env):
    is_error, rows, _ = one("list_records", {"kind": "invoices", "limit": 2, "save": True, "save_label": "overdue check"})
    assert is_error is False and os.path.exists(rows["saved"]["path"])
    is_error, saved, _ = one("saved_results")
    assert saved["count"] == 1 and saved["results"][0]["label"] == "overdue check"
    is_error, shown, _ = one("saved_results", {"show": saved["results"][0]["file"]})
    assert is_error is False and shown["result"]["kind"] == "invoices"


def test_oversized_answers_are_kept_locally_and_outlined(snapshot_env, monkeypatch):
    monkeypatch.setattr(mcp_server, "MAX_TEXT", 2000)
    is_error, out, _ = one("list_records", {"kind": "jobs", "limit": 0})
    assert is_error is False and out["truncated"] is True and out["characters"] > 2000
    assert out["outline"]["records"].startswith("list of ") and os.path.exists(out["saved"]["path"])
    assert "saved_results show=" in out["next"]


def test_stdout_carries_only_protocol_messages(snapshot_env, monkeypatch):
    def noisy(ctx, args):
        print("stray print from a command")
        sys.stdout.write("more stray output\n")
        return {"ok": 1}
    monkeypatch.setitem(cli.HANDLERS, "brief", noisy)
    replies = session([INIT, call(2, "brief")])                         # session() fails if any line is not JSON
    assert json.loads(replies[-1]["result"]["content"][0]["text"]) == {"ok": 1}


def test_long_pulls_report_progress(isolated_home, monkeypatch):
    seen = {}

    def fake_fetch(ctx, args):
        seen["max_age"] = args.max_age
        progress("[MAIN] jobs...")
        progress("[MAIN] invoices...")
        return {"written": "snapshot.json"}
    monkeypatch.setitem(cli.HANDLERS, "fetch", fake_fetch)
    replies = session([INIT, call(2, "refresh", meta={"progressToken": "t1"})])
    notes = [r for r in replies if r.get("method") == "notifications/progress"]
    assert [n["params"]["progress"] for n in notes] == [1, 2] and notes[0]["params"]["progressToken"] == "t1"
    assert notes[1]["params"]["message"] == "[MAIN] invoices..." and seen["max_age"] == 6.0
    assert json.loads(replies[-1]["result"]["content"][0]["text"]) == {"written": "snapshot.json"}


# ---------------------------------------------------------------- the API tree
def test_api_discover_walks_the_tree(isolated_home):
    is_error, top, _ = one("api_discover")
    assert is_error is False and any(m["module"] == "jpm" for m in top["modules"])
    is_error, module, _ = one("api_discover", {"module": "jobs"})                       # alias
    assert module["module"] == "jpm" and any(r["resource"] == "jobs" for r in module["resources"])
    is_error, ops, _ = one("api_discover", {"module": "jpm", "resource": "jobs"})
    assert any(o["operation"] == "list" for o in ops["operations"])
    is_error, contract, _ = one("api_discover", {"module": "jpm", "resource": "jobs", "operation": "create-notes"})
    assert contract["method"] == "POST" and contract["requestBody"]["required"] == ["text"]
    is_error, found, _ = one("api_discover", {"search": "technician"})
    assert is_error is False and found["count"] > 0
    is_error, out, _ = one("api_discover", {"operation": "list"})
    assert is_error is True and "needs module" in out["error"]["message"]


def test_api_read_maps_parameters_onto_the_operation(env_creds, monkeypatch):
    calls = []

    def fake_get(self, path, params=None):
        calls.append((path, dict(params or {})))
        if path.endswith("/jobs/12345"):
            return {"id": 12345, "jobNumber": "J-1"}
        return {"data": [{"id": 1, "jobNumber": "A", "total": 5}], "page": 1, "pageSize": 10, "hasMore": False}
    monkeypatch.setattr(ServiceTitanClient, "get", fake_get)
    is_error, out, _ = one("api_read", {"module": "jpm", "resource": "jobs", "operation": "list",
                                        "query": {"jobStatus": "completed", "page_size": 10}, "fields": "jobNumber"})
    assert is_error is False and out["source"] == "live" and out["data"] == [{"jobNumber": "A"}]
    assert calls[-1] == ("/jpm/v2/tenant/{tenant}/jobs", {"jobStatus": "Completed", "pageSize": 10})
    is_error, out, _ = one("api_read", {"module": "jpm", "resource": "jobs", "operation": "get", "path_params": {"id": 12345}})
    assert is_error is False and out["data"]["jobNumber"] == "J-1" and calls[-1][0].endswith("/jobs/12345")
    is_error, out, _ = one("api_read", {"module": "jpm", "resource": "jobs", "operation": "get"})
    assert is_error is True and "path_params needs id" in out["error"]["message"]
    is_error, out, _ = one("api_read", {"module": "jpm", "resource": "jobs", "operation": "list", "query": {"colour": 1}})
    assert is_error is True and "not a query parameter" in out["error"]["message"] and "jobStatus" in out["error"]["message"]
    is_error, out, _ = one("api_read", {"module": "jpm", "resource": "jobs", "operation": "create"})
    assert is_error is True and "use api_write" in out["error"]["message"]
    is_error, out, _ = one("api_read", {"module": "jpm", "resource": "jobz", "operation": "list"})
    assert is_error is True and "jobs" in (out["error"].get("hint") or "")             # the catalogue's suggestion


def test_api_write_previews_by_default_and_keeps_the_gates(env_creds, monkeypatch):
    monkeypatch.setattr(ServiceTitanClient, "write", lambda *a, **k: pytest.fail("a preview must not write"))
    args = {"module": "jpm", "resource": "jobs", "operation": "create-notes", "path_params": {"id": 12345},
            "body": {"text": "Customer called back"}}
    is_error, out, _ = one("api_write", args)
    assert is_error is False and out["dryRun"] is True and out["wouldRun"] is True and out["method"] == "POST"
    assert out["body"] == {"text": "Customer called back"} and out["url"].endswith("/tenant/555/jobs/12345/notes")
    monkeypatch.setenv("SERVICETITAN_ALLOW_WRITES", "none")
    is_error, out, _ = one("api_write", dict(args, dry_run=False))
    assert is_error is True and out["error"]["code"] == "WRITES_DISABLED"
    is_error, out, _ = one("api_write", {"module": "jpm", "resource": "jobs", "operation": "list"})
    assert is_error is True and "use api_read" in out["error"]["message"]


# ---------------------------------------------------------------- the command
def test_client_config_names_the_server_and_an_absolute_command(capsys, isolated_home):
    code, out, _ = run_cli(capsys, "mcp", "--client-config")
    entry = out["claudeDesktop"]["add"]["mcpServers"]["st-operator"]
    assert code == 0 and out["server"] == "st-operator" and "not affiliated" in out["thirdParty"]
    assert os.path.isabs(entry["command"]) and entry["args"][-1] == "mcp"
    assert entry["env"]["SERVICETITAN_HOME"] == str(isolated_home)
    assert out["claudeCode"].startswith("claude mcp add st-operator -- ")
    assert out["claudeDesktop"]["configFile"].endswith("claude_desktop_config.json")
    code, out, _ = run_cli(capsys, "mcp", "--client-config", "--read-only")
    assert out["anyClient"]["args"][-2:] == ["mcp", "--read-only"]


def test_a_real_stdio_session(tmp_path):
    env = dict(os.environ, SERVICETITAN_HOME=str(tmp_path / "home"), SERVICETITAN_SECRETS="file")
    for k in [k for k in env if k.startswith("SERVICETITAN_") and k not in ("SERVICETITAN_HOME", "SERVICETITAN_SECRETS")]:
        env.pop(k)
    data = "".join(json.dumps(m) + "\n" for m in [INIT, INITIALIZED, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                                                    call(3, "status")])
    r = subprocess.run([sys.executable, "-m", "servicetitan_cli", "mcp"], input=data.encode("utf-8"), env=env,
                       capture_output=True, timeout=120)
    replies = [json.loads(line) for line in r.stdout.decode("utf-8").splitlines()]
    assert r.returncode == 0 and [x["id"] for x in replies] == [1, 2, 3]
    assert len(replies[1]["result"]["tools"]) == len(TOOLS)
    assert json.loads(replies[2]["result"]["content"][0]["text"])["checklist"]["next"] == "servicetitan setup"
