"""The three write gates, the write policy, request bodies, confirmation and the audit log."""
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
def test_policy_words_and_patterns():
    assert writes.parse_patterns("jpm/jobs/create-notes, CRM/customers;  *  ,,jpm/jobs/create-notes") == \
        ["jpm/jobs/create-notes", "crm/customers", "*"]
    assert writes.parse_patterns("") == [] and writes.parse_patterns(None) == [] and writes.parse_patterns("none") == []
    assert writes.parse_patterns("auto, jpm/jobs") == ["auto", "jpm/jobs"] and writes.parse_patterns("off") == []
    assert writes.follows_credential(["auto"]) and writes.follows_credential(["jpm", "auto"])
    assert not writes.follows_credential(["jpm"]) and not writes.follows_credential([])
    assert writes.matches(["auto"], "jpm", "jobs", "create-notes") is None          # auto is decided by the token
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
    assert writes.describe_policy([]).startswith("off")
    assert writes.describe_policy(["auto"]).startswith("auto:")
    assert "plus: crm/customers" in writes.describe_policy(["auto", "crm/customers"])
    assert writes.describe_policy(["jpm/jobs"]).startswith("allowlist: jpm/jobs")


def test_writable_operations_from_granted_scopes():
    allowed = endpoints.writable_operations(["tn.jpm.jobs:w", "tn.jpm.jobs:r", "tn.stt.businessunits:r"])
    assert set(allowed) == {"jpm/jobs"} and "create-notes" in allowed["jpm/jobs"] and "create" in allowed["jpm/jobs"]
    assert endpoints.writable_operations(["tn.jpm.jobs:r"]) == {} and endpoints.writable_operations(None) is None


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


def test_default_policy_follows_the_credential(env_creds, monkeypatch):
    op = endpoints.resolve_operation("jpm", "jobs", "create-notes")
    holder = {"client": WriteClient(None)}
    assert Context().write_patterns() == ["auto"] and Context().writes_follow_credential()
    assert Context().describe()["writes"]["policy"].startswith("auto")
    # the credential was granted tn.jpm.jobs:w, so the note can be written with no allowlist to maintain
    env = _run(op, NOTE + ["--write"], holder)
    assert env["gates"] == {"flag": "--write", "allowlist": "auto", "scope": "granted"} and len(holder["client"].calls) == 1
    assert env["status"] == 200 and env["data"]["id"] == 777 and writes.read()[-1]["gates"]["allowlist"] == "auto"
    # not granted: the scope error, whose hint says the policy follows the credential
    holder["client"].scopes = ["tn.jpm.jobs:r", "tn.crm.customers:w"]
    with pytest.raises(AuthError) as ei:
        _run(op, NOTE + ["--write"], holder)
    assert ei.value.code == "SCOPE" and ei.value.exit_code == 4 and "'auto'" in ei.value.hint
    assert len(holder["client"].calls) == 1
    # a token that does not say what it was granted cannot decide under auto: fail closed
    holder["client"].scopes = None
    with pytest.raises(ConfigError) as ei:
        _run(op, NOTE + ["--write"], holder)
    assert ei.value.code == "WRITES_DISABLED" and "did not report" in ei.value.message and len(holder["client"].calls) == 1
    # ...but an explicit pattern is a person's decision and runs, recorded as not verifiable
    monkeypatch.setenv("SERVICETITAN_ALLOW_WRITES", "jpm/jobs/create-notes")
    env = _run(op, NOTE + ["--write"], holder)
    assert env["gates"] == {"flag": "--write", "allowlist": "jpm/jobs/create-notes", "scope": "not verifiable from token"}
    # auto plus explicit patterns is a union
    monkeypatch.setenv("SERVICETITAN_ALLOW_WRITES", "auto, crm/customers")
    holder["client"].scopes = ["tn.jpm.jobs:w"]
    assert _run(op, NOTE + ["--write"], holder)["gates"]["allowlist"] == "auto"


def test_write_gates_in_order(env_creds, monkeypatch):
    op = endpoints.resolve_operation("jpm", "jobs", "create-notes")
    holder = {"client": WriteClient(None)}
    # gate 1: the flag, checked before the body so the first error names it
    with pytest.raises(UsageError) as ei:
        _run(op, ["12345"], holder)
    assert "--write" in ei.value.message and "--allow-writes" in ei.value.hint and "list" in ei.value.hint
    # gate 2: the policy. Off means exit 3 before any token is fetched
    monkeypatch.setenv("SERVICETITAN_ALLOW_WRITES", "none")
    with pytest.raises(ConfigError) as ei:
        _run(op, NOTE + ["--write"], holder)
    assert ei.value.code == "WRITES_DISABLED" and ei.value.exit_code == 3 and "turned off" in ei.value.message
    assert "--allow-writes auto" in ei.value.hint and holder["client"].tokens == 0
    # an explicit allowlist that names other operations
    monkeypatch.setenv("SERVICETITAN_ALLOW_WRITES", "jpm/jobs/create, crm")
    with pytest.raises(ConfigError) as ei:
        _run(op, NOTE + ["--write"], holder)
    assert ei.value.code == "WRITES_DISABLED" and "jpm/jobs/create-notes" in ei.value.hint and holder["client"].tokens == 0
    # gate 3: the granted scope, even when an explicit pattern names the operation
    monkeypatch.setenv("SERVICETITAN_ALLOW_WRITES", "jpm/jobs")
    holder["client"].scopes = ["tn.jpm.jobs:r"]
    with pytest.raises(AuthError) as ei:
        _run(op, NOTE + ["--write"], holder)
    assert ei.value.code == "SCOPE" and ei.value.exit_code == 4 and "tn.jpm.jobs:w" in ei.value.message
    assert not holder["client"].calls and "'auto'" not in ei.value.hint
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
    assert env["dryRun"] is True and env["wouldRun"] is False and env["allowedBy"] == "auto" and env["write"] is True
    assert env["policy"].startswith("auto") and "tn.jpm.jobs:w" in env["checkedAtRunTime"]
    assert env["body"] == {"text": "Customer called back"} and env["url"].endswith("/tenant/555/jobs/12345/notes")
    assert not holder["client"].calls and holder["client"].tokens == 0        # offline
    env = _run(op, NOTE + ["--dry-run", "--write"], holder)
    assert env["wouldRun"] is True and not holder["client"].calls
    monkeypatch.setenv("SERVICETITAN_ALLOW_WRITES", "none")
    env = _run(op, NOTE + ["--dry-run", "--write"], holder)
    assert env["wouldRun"] is False and env["allowedBy"] is None and env["policy"].startswith("off")
    monkeypatch.setenv("SERVICETITAN_ALLOW_WRITES", "jpm")
    assert _run(op, NOTE + ["--dry-run", "--write"], holder)["allowedBy"] == "jpm"
    rows = writes.read()
    assert len(rows) == 4 and all(r["dryRun"] for r in rows)
    with pytest.raises(UsageError):                       # a dry run still validates the body
        _run(op, ["12345", "--dry-run"], holder)


def test_failed_write_is_logged_and_never_retried(env_creds, monkeypatch):
    op = endpoints.resolve_operation("jpm", "jobs", "create-notes")
    holder = {"client": WriteClient(None)}
    holder["client"].result = ApiError("HTTP 500 for x", status=500)
    with pytest.raises(ApiError):
        _run(op, NOTE + ["--write"], holder)
    assert len(holder["client"].calls) == 1
    rows = writes.read()
    assert rows[-1]["status"] == 500 and "500" in rows[-1]["error"] and rows[-1]["dryRun"] is False


def test_terminal_confirmation(env_creds, monkeypatch, capsys):
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
def test_cli_profile_policy_describe_and_audit(capsys, isolated_home, monkeypatch):
    code, out, err = run_cli(capsys, "profile", "add", "acme", "--company", "Acme", "--timezone", "America/Denver", "--use")
    assert code == 0, err
    code, out, _ = run_cli(capsys, "profile", "show", "acme")
    assert out["writes"]["allow"] == ["auto"]                                  # the default: the credential decides
    code, out, err = run_cli(capsys, "profile", "set", "acme", "--allow-writes", "JPM/jobs/create-notes, crm/customers")
    assert code == 0, err
    code, out, _ = run_cli(capsys, "profile", "show", "acme")
    assert out["writes"]["allow"] == ["jpm/jobs/create-notes", "crm/customers"]
    code, out, err = run_cli(capsys, "profile", "set", "acme", "--no-writes")
    assert code == 0, err
    code, out, _ = run_cli(capsys, "profile", "show", "acme")
    assert out["writes"]["allow"] == []
    code, out, err = run_cli(capsys, "profile", "set", "acme", "--allow-writes", "auto")
    assert code == 0 and run_cli(capsys, "profile", "show", "acme")[1]["writes"]["allow"] == ["auto"]
    # the contract is visible without credentials or path parameters
    code, out, _ = run_cli(capsys, "api", "jpm", "jobs", "create-notes", "--describe")
    assert code == 0 and out["write"] is True and out["requestBody"]["required"] == ["text"] and len(out["requires"]) == 3
    assert "--write --dry-run" in out["example"] and out["scope"] == "tn.jpm.jobs:w" and "auto" in out["requires"][1]
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
    assert out["dryRun"] is True and out["wouldRun"] is False and out["allowedBy"] == "auto" and out["body"] == {"text": "hi"}
    assert out["method"] == "POST" and out["url"] == "https://api.servicetitan.io/jpm/v2/tenant/555/jobs/12345/notes"
    code, out, err = run_cli(capsys, "api", "jpm", "jobs", "create-notes", "12345", "--set", "text=hi")
    assert code == 2 and json.loads(err)["error"]["code"] == "USAGE" and "--write" in json.loads(err)["error"]["message"]
    monkeypatch.setenv("SERVICETITAN_ALLOW_WRITES", "none")            # turned off: refused before any network call
    code, out, err = run_cli(capsys, "api", "jpm", "jobs", "create-notes", "12345", "--set", "text=hi", "--write")
    assert code == 3 and json.loads(err)["error"]["code"] == "WRITES_DISABLED"
    monkeypatch.setenv("SERVICETITAN_ALLOW_WRITES", "crm/customers")   # allowlist naming something else
    code, out, err = run_cli(capsys, "api", "jpm", "jobs", "create-notes", "12345", "--set", "text=hi", "--write")
    assert code == 3 and json.loads(err)["error"]["code"] == "WRITES_DISABLED"
    code, out, err = run_cli(capsys, "api", "audit")
    assert code == 0 and out["count"] == 1 and out["entries"][0]["dryRun"] is True and out["entries"][0]["body"] == {"text": "hi"}


def test_cli_auth_check_reports_what_the_credential_can_write(capsys, isolated_home, monkeypatch):
    from servicetitan_cli.api import ServiceTitanClient
    for k, v in (("SERVICETITAN_TENANT_ID", "555"), ("SERVICETITAN_CLIENT_ID", "c"), ("SERVICETITAN_CLIENT_SECRET", "s"),
                 ("SERVICETITAN_APP_KEY", "k")):
        monkeypatch.setenv(k, v)
    report = {"authenticated": True, "grantedScopes": ["tn.jpm.jobs:r", "tn.jpm.jobs:w", "tn.stt.businessunits:r"],
              "businessUnits": [{"id": 1, "name": "Service", "active": True}]}
    monkeypatch.setattr(ServiceTitanClient, "check", lambda self: report)
    code, out, err = run_cli(capsys, "auth", "check")
    assert code == 0, err
    t = out["tenants"][0]
    assert t["scopes"]["write"] == ["tn.jpm.jobs:w"] and t["writes"]["policy"].startswith("auto")
    assert "create-notes" in t["writes"]["credentialAllows"]["jpm/jobs"] and list(t["writes"]["credentialAllows"]) == ["jpm/jobs"]
    report["grantedScopes"] = ["tn.jpm.jobs:r"]
    code, out, _ = run_cli(capsys, "auth", "check")
    assert out["tenants"][0]["writes"]["credentialAllows"] == {} and "no write scope" in out["tenants"][0]["writes"]["note"]
