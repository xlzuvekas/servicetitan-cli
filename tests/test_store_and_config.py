import json
import os
import stat
import sys

import pytest

from servicetitan_cli.config import Context, load_env_file, load_legacy_config
from servicetitan_cli.errors import ConfigError, UsageError
from servicetitan_cli.secrets import SecretStore, secret_key
from servicetitan_cli.store import ProfileStore


def test_profile_store_roundtrip(isolated_home):
    store = ProfileStore()
    store.add("acme", company_name="Acme", timezone="America/Denver")
    store.add_tenant("acme", "main", "Main", "123", "cid")
    store.mark_secret("acme", "MAIN", "clientSecret")
    store.save()
    again = ProfileStore()
    assert again.default_name() == "acme"
    t = again.get_tenant("acme", "MAIN")
    assert t["code"] == "MAIN" and t["tenantId"] == "123" and t["secrets"] == ["clientSecret"]
    with pytest.raises(ConfigError):
        again.add_tenant("acme", "OTHER", "Other", "123", "cid2")  # duplicate tenant id
    with pytest.raises(ConfigError):
        again.add_tenant("acme", "MAIN", "Dup", "999", "cid3")  # duplicate code
    with pytest.raises(ConfigError):
        again.add_tenant("acme", "X", "X", "not-a-number", "cid")
    with pytest.raises(ConfigError):
        again.add("Bad Name!", company_name="x")
    exported = again.export("acme")
    assert exported["tenants"] == [{"code": "MAIN", "label": "Main"}] and "clientId" not in json.dumps(exported)


def test_file_secret_backend_is_private(isolated_home):
    s = SecretStore("file")
    s.set("acme/MAIN/clientSecret", "shh")
    assert s.get("acme/MAIN/clientSecret") == "shh"
    path = s.detail
    if not sys.platform.startswith("win"):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    s.delete("acme/MAIN/clientSecret")
    assert s.get("acme/MAIN/clientSecret") is None
    with pytest.raises(ConfigError):
        s.set("k", "")
    assert secret_key("acme", "MAIN", "appKey") == "acme/MAIN/appKey"


def test_context_resolves_store_profile_and_env_overrides(isolated_home, monkeypatch):
    store = ProfileStore()
    store.add("acme", company_name="Acme")
    store.add_tenant("acme", "SD", "San Diego", "111", "cid-sd")
    store.add_tenant("acme", "TUC", "Tucson", "222", "cid-tuc")
    store.save()
    secrets = SecretStore("file")
    secrets.set(secret_key("acme", "SD", "clientSecret"), "sd-secret")
    secrets.set(secret_key("acme", "SD", "appKey"), "shared-app-key")
    secrets.set(secret_key("acme", "TUC", "clientSecret"), "tuc-secret")
    store.mark_secret("acme", "SD", "clientSecret")
    store.mark_secret("acme", "SD", "appKey")
    store.mark_secret("acme", "TUC", "clientSecret")
    store.save()

    ctx = Context()
    assert ctx.profile_name == "acme" and ctx.source == "store"
    creds = ctx.validate_credentials()
    assert creds["SD"].client_secret == "sd-secret"
    assert creds["TUC"].app_key == "shared-app-key"  # borrowed from the sibling tenant
    assert creds["TUC"].client_secret == "tuc-secret"
    assert ctx.data_path.endswith(os.path.join("snapshots", "acme.json"))

    monkeypatch.setenv("SERVICETITAN_CLIENT_SECRET_TUC", "from-env")
    ctx2 = Context()
    assert ctx2.credentials_for(ctx2.select_tenants("tuc")[0]).client_secret == "from-env"
    with pytest.raises(UsageError):
        ctx2.select_tenants("NOPE")


def test_context_env_only_mode(isolated_home, monkeypatch):
    monkeypatch.setenv("SERVICETITAN_TENANT_ID", "42")
    monkeypatch.setenv("SERVICETITAN_CLIENT_ID", "cid")
    monkeypatch.setenv("SERVICETITAN_CLIENT_SECRET", "sec")
    monkeypatch.setenv("SERVICETITAN_APP_KEY", "key")
    monkeypatch.setenv("SERVICETITAN_TIMEZONE", "America/Denver")
    ctx = Context()
    assert ctx.source == "env" and ctx.profile_name == "env"
    c = ctx.credentials_for(ctx.tenants[0])
    assert (c.code, c.tenant_id, c.client_secret, c.app_key) == ("MAIN", "42", "sec", "key")
    assert str(ctx.tz) == "America/Denver"


def test_context_without_anything_gives_actionable_error(isolated_home):
    with pytest.raises(ConfigError) as ei:
        Context().config
    assert "profile add" in ei.value.hint


def test_legacy_config_and_env(isolated_home, tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({
        "_comment": "ignored",
        "company": {"name": "Legacy Co"},
        "locations": [{"code": "spr", "label": "Springfield", "env": "SPRINGFIELD"},
                      {"code": "loc2", "label": "Two", "env": "LOCATION_2"}],
        "targets": {"avgTicket": 300},
    }))
    (tmp_path / ".env").write_text("\n".join([
        "# comment", "SERVICETITAN_APP_KEY=shared-key",
        "SERVICETITAN_CLIENT_ID_SPRINGFIELD=cid1", "SERVICETITAN_CLIENT_SECRET_SPRINGFIELD=s1=with=equals",
        "SERVICETITAN_TENANT_ID_SPRINGFIELD=1001",
        "SERVICETITAN_CLIENT_ID_LOCATION_2=cid2", "SERVICETITAN_CLIENT_SECRET_LOCATION_2='quoted'",
        "SERVICETITAN_TENANT_ID_LOCATION_2=1002", ""]))
    env = load_env_file(str(tmp_path / ".env"))
    assert env["SERVICETITAN_CLIENT_SECRET_SPRINGFIELD"] == "s1=with=equals"
    assert env["SERVICETITAN_CLIENT_SECRET_LOCATION_2"] == "quoted"
    ctx = Context()
    assert ctx.source == "legacy" and ctx.profile_name == "local"
    assert ctx.config["company"]["name"] == "Legacy Co" and ctx.config["targets"]["avgTicket"] == 300
    creds = ctx.validate_credentials()
    assert creds["SPR"].tenant_id == "1001" and creds["SPR"].app_key == "shared-key"
    assert creds["LOC2"].client_secret == "quoted"
    assert ctx.data_path == "live_data.json"


def test_legacy_config_validation(tmp_path):
    bad = tmp_path / "config.json"
    bad.write_text(json.dumps({"locations": [{"code": "A", "label": "A"}, {"code": "B", "label": "B"}]}))
    with pytest.raises(ConfigError) as ei:
        load_legacy_config(str(bad))
    assert "env" in ei.value.message
    bad.write_text(json.dumps({"locations": [{"code": "A", "label": "A", "env": "X"}, {"code": "A", "label": "B", "env": "Y"}]}))
    with pytest.raises(ConfigError):
        load_legacy_config(str(bad))
    bad.write_text("{not json")
    with pytest.raises(ConfigError):
        load_legacy_config(str(bad))


def test_same_tenant_twice_is_refused(isolated_home, tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({
        "locations": [{"code": "A", "label": "A", "env": "A"}, {"code": "B", "label": "B", "env": "B"}]}))
    (tmp_path / ".env").write_text("SERVICETITAN_APP_KEY=k\nSERVICETITAN_TENANT_ID=1\nSERVICETITAN_CLIENT_ID=c\n"
                                   "SERVICETITAN_CLIENT_SECRET=s\n")
    with pytest.raises(ConfigError) as ei:
        Context().validate_credentials()
    assert "same ServiceTitan tenant id" in ei.value.message
