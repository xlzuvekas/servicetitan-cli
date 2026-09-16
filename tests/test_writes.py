"""The three write gates, request bodies, confirmation and the audit log."""
import io
import json
import os
import stat

import pytest

from conftest import run_cli
from servicetitan_cli import apitree, endpoints, writes
from servicetitan_cli.api import WriteAuthorization
from servicetitan_cli.config import Context
from servicetitan_cli.errors import ApiError, AuthError, ConfigError, UsageError

NOTE = ["12345", "--set", "text=Customer called back"]


# ---------------------------------------------------------------- policy pieces
def test_allowlist_patterns():
    assert writes.parse_patterns("jpm/jobs/create-notes, CRM/customers;  *  ,,jpm/jobs/create-notes") == \
        ["jpm/jobs/create-notes", "crm/customers", "*"]
    assert writes.parse_patterns("") == [] and writes.parse_patterns(None) == []
    assert writes.matches(["jpm/jobs/create-notes"], "jpm", "jobs", "create-notes") == "jpm/jobs/create-notes"
    assert writes.matches(["jpm/jobs/create-notes"], "jpm", "jobs", "create") is None
    assert writes.matches(["crm/customers"], "crm", "customers", "update") == "crm/customers"
    assert writes.matches(["crm"], "crm", "customers", "update") == "crm"
    assert writes.matches(["crm"], "jpm", "jobs", "create") is None
    assert writes.matches(["*"], "jpm", "jobs", "create") == "*"
    assert writes.matches(["*/*/create-notes"], "crm", "customers", "create-notes") == "*/*/create-notes"
    assert writes.matches(["JPM/Jobs"], "jpm", "jobs", "create") == "JPM/Jobs"          # case-insensitive
    assert writes.matches([], "jpm", "jobs", "create") is None and writes.matches(None, "jpm", "jobs", "create") is None
    assert writes.matches(["a/b/c/d", ""], "a", "b", "c") is None                        # malformed patterns never match


def test_build_body_sources_and_validation(tmp_path, monkeypatch, capsys):
    op = {"method": "POST", "path": "/x", "body": {"properties": {"text": "string", "pinToTop": "boolean"}, "required": ["text"]}}
    assert writes.build_body(op, body_arg='{"text": "hi"}') == {"text": "hi"}
    f = tmp_path / "b.json"
    f.write_text('{"text": "from file", "pinToTop": true}')
    assert writes.build_body(op, body_arg="@" + str(f)) == {"text": "from file", "pinToTop": True}
    assert writes.build_body(op, sets=["text=hi there", "pinToTop=true"]) == {"text": "hi there", "pinToTop": True}
    assert writes.build_body(op, body_arg='{"text": "a"}', sets=["pinToTop=false"]) == {"text": "a", "pinToTop": False}
    monkeypatch.setattr("sys.stdin", io.StringIO('{"text": "stdin"}'))
    assert writes.build_body(op, body_stdin=True) == {"text": "stdin"}
    with pytest.raises(UsageError) as ei:
        writes.build_body(op)
    assert "needs a JSON body" in ei.value.message and "text*" in ei.value.hint
    with pytest.raises(UsageError) as ei:
        writes.build_body(op, sets=["pinToTop=true"])
    assert "missing required" in ei.value.message and "text" in ei.value.message
    with pytest.raises(UsageError):
        writes.build_body(op, body_arg="{not json")
    with pytest.raises(UsageError):
        writes.build_body(op, body_arg="@" + str(tmp_path / "missing.json"))
    with pytest.raises(UsageError):
        writes.build_body(op, sets=["novalue"])
    with pytest.raises(UsageError):
        writes.build_body(op, body_arg="[1, 2]", sets=["text=x"])
    capsys.readouterr()
    assert writes.build_body(op, body_arg='{"text": "a", "extra": 1}') == {"text": "a", "extra": 1}
    assert "extra" in capsys.readouterr().err                 # undocumented fields are sent, with a warning
    assert writes.build_body({"method": "DELETE", "path": "/x"}) is None   # nothing documented, nothing required


class Tty(io.StringIO):
    def isatty(self):
        return True


def test_confirmation_requires_the_tenant_code():
    preview = {"method": "POST", "url": "https://api.servicetitan.io/x", "params": {"a": 1}, "body": {"text": "hi"}}
    err = io.StringIO()
    assert writes.confirm(preview, "SD", stdin=Tty("sd\n"), stderr=err) is True
    assert "tenant SD" in err.getvalue() and '"text": "hi"' in err.getvalue()
    assert writes.confirm(preview, "SD", stdin=Tty("\n"), stderr=io.StringIO()) is False
    assert writes.confirm(preview, "SD", stdin=Tty("yes\n"), stderr=io.StringIO()) is False
    assert writes.confirm(preview, "SD", stdin=io.StringIO("no\n"), stderr=io.StringIO()) is True   # no terminal: no prompt
    assert writes.confirm(preview, "SD", assume_yes=True, stdin=Tty("no\n"), stderr=io.StringIO()) is True


def test_audit_log_is_private_and_readable(isolated_home):
    p = writes.record({"profile": "acme", "tenant": "MAIN", "operation": "jpm jobs create-notes", "dryRun": False, "status": 200})
    writes.record({"profile": "other", "tenant": "X", "operation": "crm customers update", "dryRun": True})
    assert p == writes.audit_path() and p.startswith(str(isolated_home))
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    rows = writes.read()
    assert [r["profile"] for r in rows] == ["acme", "other"] and all("at" in r for r in rows)
    assert [r["tenant"] for r in writes.read(profile="acme")] == ["MAIN"]
    assert len(writes.read(limit=1)) == 1 and writes.read(limit=1)[0]["profile"] == "other"


# ---------------------------------------------------------------- the gates, end to end
class WriteClient(object):
    """Stands in for ServiceTitanClient: records the write, answers with a scripted result."""

    def __init__(self, creds, progress=None):
        self.creds = creds
        self.calls = []
        self.granted_scopes = None
        self.scopes = ["tn.jpm.jobs:r", "tn.jpm.jobs:w"]
        self.result = {"status": 200, "body": {"id": 777, "text": "ok"}}
        self.tokens = 0

    def resolve_url(self, path):
        return "https://api.servicetitan.io" + path.replace("{tenant}", str(self.creds.tenant_id))

    def token(self, force=False):
        self.tokens += 1
        self.granted_scopes = sorted(self.scopes) if self.scopes is not None else None
        return "tok"

    def write(self, method, path, params=None, body=None, authorization=None):
        assert isinstance(authorization, WriteAuthorization)
        self.calls.append((method, path, dict(params or {}), body, authorization))
        if isinstance(self.result, Exception):
            raise self.result
        return dict(self.result)


@pytest.fixture
def env_creds(isolated_home, monkeypatch):
    for k, v in (("SERVICETITAN_TENANT_ID", "555"), ("SERVICETITAN_CLIENT_ID", "c"), ("SERVICETITAN_CLIENT_SECRET", "s"),
                 ("SERVICETITAN_APP_KEY", "k")):
        monkeypatch.setenv(k, v)
    return isolated_home


def _run(op, argv, holder):
    def factory(creds, **kw):
        c = holder.setdefault("client", WriteClient(creds))
        c.creds = creds
        return c
    ns = apitree.build_parser(op).parse_args(argv)
    return apitree.execute(op, ns, Context(), client_factory=factory)


def test_write_gates_in_order(env_creds, monkeypatch):
    op = endpoints.resolve_operation("jpm", "jobs", "create-notes")
    holder = {"client": WriteClient(None)}
    # gate 1: the flag, checked before the body so the first error names it
    with pytest.raises(UsageError) as ei:
        _run(op, ["12345"], holder)
    assert "--write" in ei.value.message and "--allow-writes" in ei.value.hint and "list" in ei.value.hint
    # gate 2: the allowlist, empty by default
    with pytest.raises(ConfigError) as ei:
        _run(op, NOTE + ["--write"], holder)
    assert ei.value.code == "WRITES_DISABLED" and ei.value.exit_code == 3 and "jpm/jobs/create-notes" in ei.value.hint
    monkeypatch.setenv("SERVICETITAN_ALLOW_WRITES", "jpm/jobs/create")   # a different operation
    with pytest.raises(ConfigError):
        _run(op, NOTE + ["--write"], holder)
    assert not holder["client"].calls and holder["client"].tokens == 0
    # gate 3: the granted scope
    monkeypatch.setenv("SERVICETITAN_ALLOW_WRITES", "jpm/jobs")
    holder["client"].scopes = ["tn.jpm.jobs:r"]
    with pytest.raises(AuthError) as ei:
        _run(op, NOTE + ["--write"], holder)
    assert ei.value.code == "SCOPE" and ei.value.exit_code == 4 and "tn.jpm.jobs:w" in ei.value.message
    assert not holder["client"].calls
    # all three pass
    holder["client"].scopes = ["tn.jpm.jobs:r", "tn.jpm.jobs:w"]
    env = _run(op, NOTE + ["--write"], holder)
    method, path, params, body, auth = holder["client"].calls[0]
    assert method == "POST" and path == "/jpm/v2/tenant/{tenant}/jobs/12345/notes" and params == {}
    assert body == {"text": "Customer called back"} and auth.tenant == env["tenant"] and auth.operation == "jpm jobs create-notes"
    assert env["source"] == "live" and env["write"] is True and env["status"] == 200 and env["data"]["id"] == 777
    assert env["method"] == "POST" and env["url"] == "https://api.servicetitan.io/jpm/v2/tenant/555/jobs/12345/notes"
    assert env["gates"] == {"flag": "--write", "allowlist": "jpm/jobs", "scope": "granted"} and env["scope"] == "tn.jpm.jobs:w"
    rows = writes.read()
    assert len(rows) == 1 and rows[0]["resultId"] == 777 and rows[0]["dryRun"] is False and rows[0]["status"] == 200
    assert rows[0]["gates"]["allowlist"] == "jpm/jobs" and rows[0]["body"] == {"text": "Customer called back"}
    assert env["audit"] == writes.audit_path() and rows[0]["environment"] == "production"


def test_dry_run_sends_nothing_and_is_logged(env_creds, monkeypatch):
    op = endpoints.resolve_operation("jpm", "jobs", "create-notes")
    holder = {}
    env = _run(op, NOTE + ["--dry-run"], holder)
    assert env["dryRun"] is True and env["wouldRun"] is False and env["allowedBy"] is None and env["write"] is True
    assert env["body"] == {"text": "Customer called back"} and env["url"].endswith("/tenant/555/jobs/12345/notes")
    assert not holder["client"].calls and holder["client"].tokens == 0
    monkeypatch.setenv("SERVICETITAN_ALLOW_WRITES", "*")
    env = _run(op, NOTE + ["--dry-run", "--write"], holder)
    assert env["wouldRun"] is True and env["allowedBy"] == "*" and not holder["client"].calls
    rows = writes.read()
    assert len(rows) == 2 and all(r["dryRun"] for r in rows)
    with pytest.raises(UsageError):                       # a dry run still validates the body
        _run(op, ["12345", "--dry-run"], holder)


def test_failed_write_is_logged_and_never_retried(env_creds, monkeypatch):
    monkeypatch.setenv("SERVICETITAN_ALLOW_WRITES", "jpm")
    op = endpoints.resolve_operation("jpm", "jobs", "create-notes")
    holder = {"client": WriteClient(None)}
    holder["client"].result = ApiError("HTTP 500 for x", status=500)
    with pytest.raises(ApiError):
        _run(op, NOTE + ["--write"], holder)
    assert len(holder["client"].calls) == 1
    rows = writes.read()
    assert rows[-1]["status"] == 500 and "500" in rows[-1]["error"] and rows[-1]["dryRun"] is False


def test_unverifiable_scope_is_recorded_not_assumed(env_creds, monkeypatch):
    monkeypatch.setenv("SERVICETITAN_ALLOW_WRITES", "jpm/jobs")
    op = endpoints.resolve_operation("jpm", "jobs", "create-notes")
    holder = {"client": WriteClient(None)}
    holder["client"].scopes = None                    # token response without a scope list
    env = _run(op, NOTE + ["--write"], holder)
    assert env["gates"]["scope"] == "not verifiable from token" and holder["client"].calls


def test_terminal_confirmation(env_creds, monkeypatch, capsys):
    monkeypatch.setenv("SERVICETITAN_ALLOW_WRITES", "jpm/jobs")
    op = endpoints.resolve_operation("jpm", "jobs", "create-notes")
    holder = {"client": WriteClient(None)}
    monkeypatch.setattr("sys.stdin", Tty("no\n"))
    with pytest.raises(UsageError) as ei:
        _run(op, NOTE + ["--write"], holder)
    assert "aborted" in ei.value.message and not holder["client"].calls
    assert "Type the tenant code" in capsys.readouterr().err
    monkeypatch.setattr("sys.stdin", Tty("no\n"))
    _run(op, NOTE + ["--write", "--yes"], holder)
    assert len(holder["client"].calls) == 1
    code = Context().select_tenants(None)[0]["code"]
    monkeypatch.setattr("sys.stdin", Tty(code.lower() + "\n"))
    _run(op, NOTE + ["--write"], holder)
    assert len(holder["client"].calls) == 2


# ---------------------------------------------------------------- through the CLI
def test_cli_profile_allowlist_describe_and_audit(capsys, isolated_home, monkeypatch):
    code, out, err = run_cli(capsys, "profile", "add", "acme", "--company", "Acme", "--timezone", "America/Denver", "--use")
    assert code == 0, err
    code, out, err = run_cli(capsys, "profile", "set", "acme", "--allow-writes", "JPM/jobs/create-notes, crm/customers")
    assert code == 0, err
    code, out, _ = run_cli(capsys, "profile", "show", "acme")
    assert out["writes"]["allow"] == ["jpm/jobs/create-notes", "crm/customers"]
    code, out, err = run_cli(capsys, "profile", "set", "acme", "--no-writes")
    assert code == 0, err
    code, out, _ = run_cli(capsys, "profile", "show", "acme")
    assert out["writes"]["allow"] == []
    # the contract is visible without credentials
    code, out, _ = run_cli(capsys, "api", "jpm", "jobs", "create-notes", "--describe")
    assert code == 0 and out["write"] is True and out["requestBody"]["required"] == ["text"] and len(out["requires"]) == 3
    assert "--write --dry-run" in out["example"] and out["scope"] == "tn.jpm.jobs:w"
    code, out, _ = run_cli(capsys, "api", "jpm", "jobs")
    assert code == 0 and any(o["operation"] == "create-notes" and o["write"] for o in out["operations"])
    code, out, _ = run_cli(capsys, "api", "audit")
    assert code == 0 and out["source"] == "audit" and out["count"] == 0 and out["profile"] == "acme"


def test_cli_dry_run_and_refusal(capsys, isolated_home, monkeypatch):
    for k, v in (("SERVICETITAN_TENANT_ID", "555"), ("SERVICETITAN_CLIENT_ID", "c"), ("SERVICETITAN_CLIENT_SECRET", "s"),
                 ("SERVICETITAN_APP_KEY", "k")):
        monkeypatch.setenv(k, v)
    code, out, err = run_cli(capsys, "api", "jpm", "jobs", "create-notes", "12345", "--set", "text=hi", "--dry-run")
    assert code == 0, err
    assert out["dryRun"] is True and out["wouldRun"] is False and out["method"] == "POST" and out["body"] == {"text": "hi"}
    assert out["url"] == "https://api.servicetitan.io/jpm/v2/tenant/555/jobs/12345/notes"
    code, out, err = run_cli(capsys, "api", "jpm", "jobs", "create-notes", "12345", "--set", "text=hi")
    assert code == 2 and json.loads(err)["error"]["code"] == "USAGE" and "--write" in json.loads(err)["error"]["message"]
    code, out, err = run_cli(capsys, "api", "jpm", "jobs", "create-notes", "12345", "--set", "text=hi", "--write")
    assert code == 3 and json.loads(err)["error"]["code"] == "WRITES_DISABLED"
    code, out, err = run_cli(capsys, "api", "audit")
    assert code == 0 and out["count"] == 1 and out["entries"][0]["dryRun"] is True and out["entries"][0]["body"] == {"text": "hi"}
