import datetime as dt
import json
import os

import pytest

from servicetitan_cli.dates import UTC
from servicetitan_cli.demo import build_demo
from servicetitan_cli.store import new_profile

FIXED_NOW = dt.datetime(2026, 9, 15, 17, 30, tzinfo=UTC)


def demo_config():
    cfg = new_profile("Acme Home Services", timezone="America/Denver")
    cfg["tenants"] = [{"code": "SPR", "label": "Springfield", "tenantId": None, "clientId": None},
                      {"code": "LOC2", "label": "Location 2", "tenantId": None, "clientId": None}]
    cfg["targets"] = {"avgTicket": 280, "membershipAttachPct": 12, "lowTicketPct": 10, "optionsPerCall": 2.5}
    cfg["profile"] = "demo"
    return cfg


@pytest.fixture(scope="session")
def demo_data():
    return build_demo(demo_config(), "2026-01-01T00:00:00Z", seed=7, now=FIXED_NOW, profile="demo")


@pytest.fixture
def demo_path(tmp_path, demo_data):
    p = tmp_path / "demo.json"
    p.write_text(json.dumps(demo_data))
    return str(p)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """A private config dir with the file secret backend and no ambient credentials."""
    home = tmp_path / "home"
    monkeypatch.setenv("SERVICETITAN_HOME", str(home))
    monkeypatch.setenv("SERVICETITAN_SECRETS", "file")
    for k in list(os.environ):
        if k.startswith("SERVICETITAN_") and k not in ("SERVICETITAN_HOME", "SERVICETITAN_SECRETS"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    return home


def run_cli(capsys, *argv):
    """Run the CLI in-process; return (exit code, parsed stdout JSON or text, stderr)."""
    from servicetitan_cli.cli import main
    code = main(list(argv))
    captured = capsys.readouterr()
    out = captured.out
    try:
        parsed = json.loads(out) if out.strip() else None
    except ValueError:
        parsed = out
    return code, parsed, captured.err
