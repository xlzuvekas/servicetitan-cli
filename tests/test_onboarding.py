"""First run: the interactive `setup` wizard and the `init` checklist."""
import io
import json
import os
import subprocess
import sys

import pytest

from conftest import run_cli
from servicetitan_cli import onboarding
from servicetitan_cli.config import Context
from servicetitan_cli.errors import AuthError, UsageError
from servicetitan_cli.paths import credentials_path, snapshot_path
from servicetitan_cli.store import ProfileStore

UNITS = [{"id": 1, "name": "Service"}, {"id": 2, "name": "Install"}, {"id": 3, "name": "Sales"},
         {"id": 4, "name": "Warehouse"}]
READ_ONLY = {"authenticated": True, "grantedScopes": ["tn.jpm.jobs:r", "tn.stt.businessunits:r"], "businessUnits": UNITS}
WITH_WRITES = {"authenticated": True, "grantedScopes": ["tn.jpm.jobs:r", "tn.jpm.jobs:w", "tn.stt.businessunits:r"],
               "businessUnits": UNITS}


class Tty(io.StringIO):
    def isatty(self):
        return True


class Clients(object):
    """client_factory stand-in: each check() takes the next scripted outcome (a report or an exception)."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.creds = []

    def __call__(self, creds, **kw):
        self.creds.append(creds)
        outcome = self.outcomes.pop(0)

        class Client(object):
            def check(self):
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        return Client()


def prompter(answers, secrets):
    err = io.StringIO()
    hidden = list(secrets)
    return onboarding.Prompter(stdin=Tty("".join(a + "\n" for a in answers)), stderr=err,
                               getpass_fn=lambda prompt: (err.write(prompt), hidden.pop(0))[1]), err


def saved():
    store = ProfileStore()
    with open(credentials_path(), encoding="utf-8") as f:
        return store, json.load(f)


def test_setup_refuses_without_a_terminal(capsys, isolated_home):
    code, out, err = run_cli(capsys, "setup")
    e = json.loads(err)["error"]
    assert code == 2 and e["code"] == "USAGE" and "terminal" in e["message"]
    assert "profile add" in e["hint"] and "tenant add" in e["hint"] and len(e["details"]["scripted"]) == 4
    assert not os.path.exists(credentials_path())


def test_setup_happy_path_tests_before_saving_and_offers_the_first_pull(isolated_home):
    pulls = []
    clients = Clients(WITH_WRITES)
    p, err = prompter(["acme", "Acme Home Services", "3",            # company, Mountain time
                       "", "",                                       # code MAIN, label Main Office (defaults)
                       "12ab", "123456", "cid.test",                 # a bad tenant id is re-asked
                       "",                                           # keep the company read-only (default yes)
                       "y"],                                         # pull now
                      ["s3cret", "ak-1"])
    out = onboarding.run_setup(Context(), prompter=p, client_factory=clients,
                               fetch_fn=lambda name: (pulls.append(name),
                                                      {"written": "/x.json", "generatedAt": "2026-10-02T00:00:00Z",
                                                       "counts": {"jobs": 10, "ar": 3, "technicians": 4}})[1])
    assert out["setup"] == "complete" and out["profile"] == "acme" and out["timezone"] == "America/Denver"
    assert out["tenant"] == {"code": "MAIN", "label": "Main Office", "tenantId": "123456", "clientId": "cid.test"}
    assert out["verified"] is True and sum(out["businessUnits"].values()) == 4 and out["writeScopes"] == ["tn.jpm.jobs:w"]
    assert out["writes"].startswith("off") and out["snapshot"]["counts"]["jobs"] == 10 and out["next"] == "servicetitan brief"
    assert pulls == ["acme"] and clients.creds[0].client_secret == "s3cret" and clients.creds[0].app_key == "ak-1"
    store, creds = saved()
    tenant = store.get_tenant("acme", "MAIN")
    assert store.default_name() == "acme" and tenant["verifiedAt"] and sorted(tenant["secrets"]) == ["appKey", "clientSecret"]
    assert store.effective("acme")["writes"]["allow"] == [] and store.effective("acme")["company"]["name"] == "Acme Home Services"
    assert creds == {"acme/MAIN/appKey": "ak-1", "acme/MAIN/clientSecret": "s3cret"}
    log = err.getvalue()
    for line in ("Step 1 of 4", "Step 2 of 4", "Step 3 of 4", "Step 4 of 4", "Connected. 4 business unit(s)", "Saved.",
                 "The tenant ID is all digits", "All set. Next: servicetitan brief"):
        assert line in log, line
    assert "s3cret" not in log and "ak-1" not in log                # secrets are never echoed


def test_setup_retries_a_failed_connection_and_keeps_the_answers(isolated_home):
    clients = Clients(AuthError("ServiceTitan rejected the client id or secret", hint="Check the client secret."), READ_ONLY)
    p, err = prompter(["acme", "Acme", "America/Chicago", "", "", "123456", "cid.test",
                       "",                                           # enter the values again (default yes)
                       "", "",                                       # tenant id and client id keep their answers
                       "n"],                                         # no pull now
                      ["wrong", "ak-1", "right", "ak-1"])
    out = onboarding.run_setup(Context(), prompter=p, client_factory=clients, fetch_fn=lambda name: pytest.fail("no pull"))
    assert out["verified"] is True and out["timezone"] == "America/Chicago" and out["snapshot"] is None
    assert out["next"] == "servicetitan fetch" and out["writeScopes"] == [] and out["writes"].startswith("auto")
    assert [c.client_secret for c in clients.creds] == ["wrong", "right"] and clients.creds[1].tenant_id == "123456"
    _, creds = saved()
    assert creds["acme/MAIN/clientSecret"] == "right"
    assert "Could not connect" in err.getvalue() and "Check the client secret." in err.getvalue()
    assert "only read" in err.getvalue()


def test_setup_saves_nothing_when_stopped_after_a_failed_test(isolated_home):
    clients = Clients(AuthError("rejected"))
    p, _ = prompter(["acme", "Acme", "1", "", "", "123456", "cid.test", "n", ""], ["s", "k"])
    with pytest.raises(UsageError) as ei:
        onboarding.run_setup(Context(), prompter=p, client_factory=clients)
    assert "before saving anything" in ei.value.message
    assert ProfileStore().profiles == {} and not os.path.exists(credentials_path())


def test_setup_adds_a_second_account_and_reuses_the_app_key(isolated_home):
    first, _ = prompter(["acme", "Acme", "3", "", "", "123456", "cid.one", "n"], ["s1", "shared-key"])
    onboarding.run_setup(Context(), prompter=first, client_factory=Clients(READ_ONLY))
    second, err = prompter(["",                                     # Enter: add to acme
                            "main",                                 # taken: re-asked
                            "north", "North Branch",
                            "123456",                               # same tenant twice: re-asked
                            "222", "cid.two",
                            "",                                     # reuse MAIN's app key (default yes)
                            "n"],
                           ["s2"])
    out = onboarding.run_setup(Context(), prompter=second, client_factory=Clients(READ_ONLY))
    assert out["profile"] == "acme" and out["tenant"]["code"] == "NORTH" and out["tenant"]["tenantId"] == "222"
    store, creds = saved()
    assert [t["code"] for t in store.tenants("acme")] == ["MAIN", "NORTH"]
    assert creds["acme/NORTH/appKey"] == "shared-key" and creds["acme/NORTH/clientSecret"] == "s2"
    log = err.getvalue()
    assert "Companies already set up here: acme" in log and "MAIN is already used in acme" in log
    assert "already connected in acme" in log and "Use the app key already saved for MAIN?" in log


def test_init_checklist_names_the_next_command(capsys, isolated_home, monkeypatch, demo_data):
    code, out, _ = run_cli(capsys, "init")
    assert code == 0 and out["ready"] is False and out["next"] == "servicetitan setup"
    assert [s["step"] for s in out["steps"]] == ["install", "connect", "first pull", "try it"]
    assert out["steps"][0]["done"] is True and out["steps"][1]["run"] == "servicetitan setup"
    assert out["secrets"]["backend"] == "file" and len(out["howToGetCredentials"]) == 4 and "SETUP.md" in out["guide"]
    run_cli(capsys, "profile", "add", "acme", "--company", "Acme", "--timezone", "America/Denver", "--use")
    monkeypatch.setenv("CS", "secret")
    monkeypatch.setenv("AK", "key")
    run_cli(capsys, "tenant", "add", "acme", "--code", "MAIN", "--tenant-id", "123", "--client-id", "cid",
            "--client-secret-env", "CS", "--app-key-env", "AK", "--no-verify")
    code, out, _ = run_cli(capsys, "init")
    steps = {s["step"]: s for s in out["steps"]}
    assert steps["connect"]["done"] and not steps["test"]["done"] and out["next"] == "servicetitan auth check"
    onboarding.record_verified(ProfileStore(), "acme", ["MAIN"])
    path = snapshot_path("acme")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(demo_data, f)
    code, out, _ = run_cli(capsys, "init")
    assert out["ready"] is True and out["next"] == "servicetitan brief" and all(s["done"] for s in out["steps"])
    code, text, _ = run_cli(capsys, "init", "--format", "text")
    assert code == 0 and "servicetitan brief" in text


def test_auth_check_stamps_tenants_that_connect(capsys, isolated_home, monkeypatch):
    from servicetitan_cli.api import ServiceTitanClient
    run_cli(capsys, "profile", "add", "acme", "--company", "Acme", "--use")
    monkeypatch.setenv("CS", "secret")
    monkeypatch.setenv("AK", "key")
    run_cli(capsys, "tenant", "add", "acme", "--code", "MAIN", "--tenant-id", "123", "--client-id", "cid",
            "--client-secret-env", "CS", "--app-key-env", "AK", "--no-verify")
    assert not ProfileStore().get_tenant("acme", "MAIN").get("verifiedAt")
    monkeypatch.setattr(ServiceTitanClient, "check", lambda self: READ_ONLY)
    code, out, err = run_cli(capsys, "auth", "check")
    assert code == 0, err
    assert ProfileStore().get_tenant("acme", "MAIN")["verifiedAt"].startswith("20")


def test_module_entry_point_works_without_the_script_on_path():
    r = subprocess.run([sys.executable, "-m", "servicetitan_cli", "--version"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and r.stdout.startswith("servicetitan-cli ")
