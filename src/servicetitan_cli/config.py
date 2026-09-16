"""Which company a command runs against, and how it authenticates.

Three sources, checked in this order:

1. A named profile from the profile store (``--profile NAME`` or
   ``SERVICETITAN_PROFILE``), or the store's default profile.
2. The dashboard template's legacy layout: ``config.json`` + ``.env`` in the
   current directory, or the paths given with ``--config`` / ``--env-file``.
3. Plain environment variables (``SERVICETITAN_CLIENT_ID`` etc.), which make a
   single-tenant profile called ``env`` with tenant code ``MAIN``. This is the
   zero-setup path for CI jobs and sandboxed agents.

Secrets for stored profiles come from the secret store, but an environment
variable named for the tenant (``SERVICETITAN_CLIENT_SECRET_<CODE>``) always
wins, so an orchestrator can inject credentials without touching the store.
"""
import copy
import datetime as dt
import json
import os
import re

from .dates import get_tz, parse_iso, UTC
from .errors import ConfigError, UsageError
from .paths import snapshot_path
from .secrets import SecretStore, env_lookup, secret_key
from .store import ProfileStore, new_profile, SETTING_KEYS, validate_environment

ENV_SUFFIX_RE = re.compile(r"^[A-Za-z0-9_]+$")


class Credentials(object):
    __slots__ = ("code", "label", "tenant_id", "client_id", "client_secret", "app_key", "environment")

    def __init__(self, code, label, tenant_id, client_id, client_secret, app_key, environment="production"):
        self.code = code
        self.label = label
        self.tenant_id = str(tenant_id)
        self.client_id = client_id
        self.client_secret = client_secret
        self.app_key = app_key
        self.environment = environment

    def describe(self):
        return {"code": self.code, "label": self.label, "tenantId": self.tenant_id, "clientId": self.client_id,
                "clientSecret": "***" if self.client_secret else None, "appKey": "***" if self.app_key else None,
                "environment": self.environment}


# ---------------------------------------------------------------- legacy files
def load_env_file(path):
    """Parse a .env file. Values are taken literally after the first '='."""
    env = {}
    if not path or not os.path.exists(path):
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            env[k.strip()] = v
    return env


def load_legacy_config(path):
    """config.json from the dashboard template, normalised to profile settings."""
    if not os.path.exists(path):
        raise ConfigError("No %s found" % path,
                          hint="Copy config.example.json to config.json and edit it, or set up a profile with "
                               "`servicetitan profile add`.")
    with open(path, encoding="utf-8") as f:
        try:
            raw = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError("%s is not valid JSON: %s" % (path, e),
                              hint="A trailing comma after the last item in a list or object is the usual cause.")
    raw = {k: v for k, v in raw.items() if not str(k).startswith("_")}
    locs = raw.get("locations") or raw.get("tenants") or []
    if not locs:
        raise ConfigError("%s has no locations/tenants" % path,
                          hint='Add at least one, e.g. "locations": [{"code": "MAIN", "label": "Main Office"}]')
    tenants, seen_codes, seen_env = [], set(), {}
    for loc in locs:
        for field in ("code", "label"):
            if not loc.get(field):
                raise ConfigError('Every location needs a "%s". Offending entry: %s' % (field, json.dumps(loc)))
        code = str(loc["code"]).upper()
        if code in seen_codes:
            raise ConfigError('Duplicate location code "%s"' % code,
                              hint="Codes namespace every record id; duplicates would merge two tenants' data.")
        seen_codes.add(code)
        suffix = (loc.get("env") or "").strip()
        if suffix and not ENV_SUFFIX_RE.match(suffix):
            raise ConfigError('Location "%s" has env "%s", which cannot be part of an environment-variable name'
                              % (loc["label"], suffix), hint="Letters, digits and underscores only.")
        if len(locs) > 1:
            if not suffix:
                raise ConfigError('Location "%s" needs an "env" suffix' % loc["label"],
                                  hint="With more than one location each needs its own suffix so each reads its own "
                                       "credentials, e.g. \"env\": \"SPRINGFIELD\" reads SERVICETITAN_TENANT_ID_SPRINGFIELD.")
            if suffix.upper() in seen_env:
                raise ConfigError('Locations "%s" and "%s" both use env "%s"' % (seen_env[suffix.upper()], loc["label"], suffix))
            seen_env[suffix.upper()] = loc["label"]
        tenants.append({"code": code, "label": loc["label"], "envSuffix": suffix or None,
                        "tenantId": str(loc.get("tenantId") or "") or None, "clientId": loc.get("clientId")})
    cfg = new_profile()
    for key in SETTING_KEYS:
        if key in raw and raw[key] is not None:
            if isinstance(cfg.get(key), dict) and isinstance(raw[key], dict):
                cfg[key].update(raw[key])
            else:
                cfg[key] = raw[key]
    cfg["environment"] = validate_environment(cfg.get("environment"))
    cfg["tenants"] = tenants
    cfg["rules"].setdefault("lowTicketThreshold", 150)
    cfg["rules"].setdefault("boardDays", 45)
    return cfg


# ---------------------------------------------------------------- context
class Context(object):
    """Everything a command needs: settings, tenants, credentials, clock, paths.

    Resolution is lazy so commands that need no profile (``demo``, ``schema``,
    ``palette``, ``profile add``) never fail for lack of one.
    """

    def __init__(self, profile=None, config_path=None, env_file=None, data_path=None, as_of=None,
                 secrets_backend=None, quiet=False, environ=None, store_path=None):
        self.requested_profile = profile or os.environ.get("SERVICETITAN_PROFILE") or None
        self.config_path = config_path
        self.env_file = env_file
        self.explicit_data_path = data_path or os.environ.get("SERVICETITAN_DATA") or None
        self.as_of_arg = as_of
        self.quiet = quiet
        self.environ = os.environ if environ is None else environ
        self._store_path = store_path
        self._store = None
        self._config = None
        self._source = None
        self._legacy_env = None
        self._secrets = SecretStore(secrets_backend or (self.environ.get("SERVICETITAN_SECRETS") or "auto"))
        self._creds = {}
        self.warnings = []

    # ---- stores
    @property
    def store(self):
        if self._store is None:
            self._store = ProfileStore(self._store_path)
            backend = self._store.data.get("secretsBackend")
            if backend and self._secrets.preference == "auto":
                self._secrets.preference = backend
        return self._store

    @property
    def secrets(self):
        self.store  # make sure the recorded backend preference is applied
        return self._secrets

    def secrets_selected(self):
        """True once a secret backend has actually been chosen (no side effects)."""
        return self._secrets._backend is not None

    # ---- resolution
    def _resolve(self):
        if self._config is not None:
            return
        env = self.environ
        legacy_requested = bool(self.config_path or self.env_file)
        if self.requested_profile and not legacy_requested:
            self._config = self.store.effective(self.requested_profile)
            self._source = "store"
        elif legacy_requested or os.path.exists("config.json"):
            self._config = load_legacy_config(self.config_path or "config.json")
            self._legacy_env = dict(load_env_file(self.env_file or ".env"))
            self._legacy_env.update({k: v for k, v in env.items() if k.startswith("SERVICETITAN_")})
            self._config["profile"] = self.requested_profile or "local"
            self._source = "legacy"
        else:
            name = self.store.default_name()
            if name:
                self._config = self.store.effective(name)
                self._source = "store"
            elif env.get("SERVICETITAN_TENANT_ID") and env.get("SERVICETITAN_CLIENT_ID"):
                cfg = new_profile(env.get("SERVICETITAN_COMPANY_NAME") or "Operations Dashboard",
                                  timezone=env.get("SERVICETITAN_TIMEZONE"),
                                  environment=validate_environment(env.get("SERVICETITAN_ENVIRONMENT")))
                cfg["tenants"] = [{"code": "MAIN", "label": env.get("SERVICETITAN_TENANT_LABEL") or "Main",
                                   "tenantId": env["SERVICETITAN_TENANT_ID"].strip(),
                                   "clientId": env["SERVICETITAN_CLIENT_ID"].strip(), "envSuffix": None}]
                cfg["profile"] = "env"
                self._config = cfg
                self._legacy_env = {k: v for k, v in env.items() if k.startswith("SERVICETITAN_")}
                self._source = "env"
            else:
                raise ConfigError(
                    "No ServiceTitan profile is configured on this machine.",
                    hint="Run `servicetitan profile add <name> --company \"Acme\"` then "
                         "`servicetitan tenant add <name> --code MAIN --tenant-id ... --client-id ... "
                         "--client-secret-stdin --app-key-stdin`; or set SERVICETITAN_TENANT_ID, "
                         "SERVICETITAN_CLIENT_ID, SERVICETITAN_CLIENT_SECRET and SERVICETITAN_APP_KEY; or run in a "
                         "directory with the template's config.json and .env.")
        tzname = self._config.get("timezone")
        tz = get_tz(tzname)
        if tz is None:
            self.warnings.append("Unknown timezone %r; using UTC" % tzname)
            tz = UTC
        self.tz = tz

    @property
    def config(self):
        self._resolve()
        return self._config

    @property
    def source(self):
        self._resolve()
        return self._source

    @property
    def profile_name(self):
        return self.config.get("profile")

    @property
    def environment(self):
        return self.config.get("environment") or "production"

    def config_or_default(self):
        """Settings for commands that can run without a profile (demo, render of demo data)."""
        try:
            return self.config
        except ConfigError:
            cfg = new_profile("Acme Home Services", timezone="UTC")
            cfg["tenants"] = [{"code": "SPR", "label": "Springfield", "tenantId": None, "clientId": None},
                              {"code": "LOC2", "label": "Location 2", "tenantId": None, "clientId": None}]
            cfg["targets"] = {"avgTicket": 280, "membershipAttachPct": 12, "lowTicketPct": 10, "optionsPerCall": 2.5}
            cfg["profile"] = "demo"
            self._config = cfg
            self._source = "default"
            self.tz = UTC
            return cfg

    # ---- clock
    @property
    def as_of(self):
        self._resolve()
        if getattr(self, "_as_of", None) is None:
            if self.as_of_arg:
                d = parse_iso(self.as_of_arg, tz=self.tz)
                if d is None:
                    raise UsageError("--as-of must be an ISO date or datetime, e.g. 2026-03-31 or 2026-03-31T17:00:00Z")
                if len(str(self.as_of_arg).strip()) == 10:
                    d = d.replace(hour=23, minute=59, second=59)
                self._as_of = d.astimezone(self.tz)
            else:
                self._as_of = dt.datetime.now(UTC).astimezone(self.tz)
        return self._as_of

    # ---- tenants
    @property
    def tenants(self):
        return self.config.get("tenants") or []

    def tenant_codes(self):
        return [t["code"] for t in self.tenants]

    def select_tenants(self, code=None):
        if not code or str(code).lower() == "all":
            return list(self.tenants)
        want = str(code).upper()
        for t in self.tenants:
            if t["code"].upper() == want:
                return [t]
        raise UsageError("Unknown tenant code %r for profile %r" % (code, self.profile_name),
                         hint="Tenant codes: %s" % ", ".join(self.tenant_codes()))

    # ---- credentials
    def credentials_for(self, tenant, validate=True):
        code = tenant["code"]
        if code in self._creds:
            return self._creds[code]
        env = self._legacy_env if self._legacy_env is not None else self.environ
        suffix = tenant.get("envSuffix") if self._source in ("legacy", "env") else code
        single = len(self.tenants) == 1
        environment = tenant.get("environment") or self.environment

        def from_env(field, allow_unsuffixed):
            return env_lookup(field, suffix, environ=env) if (suffix or allow_unsuffixed) else None

        if self._source in ("legacy", "env"):
            tenant_id = tenant.get("tenantId") or from_env("tenantId", True)
            client_id = tenant.get("clientId") or from_env("clientId", True)
            client_secret = from_env("clientSecret", True)
            app_key = from_env("appKey", True)
        else:
            name = self.profile_name
            tenant_id = tenant.get("tenantId")
            client_id = env_lookup("clientId", code, environ=env) if env.get("SERVICETITAN_CLIENT_ID_" + code) else tenant.get("clientId")
            client_secret = env.get("SERVICETITAN_CLIENT_SECRET_" + code) or (single and env.get("SERVICETITAN_CLIENT_SECRET")) or None
            if not client_secret and "clientSecret" in (tenant.get("secrets") or []):
                client_secret = self.secrets.get(secret_key(name, code, "clientSecret"))
            app_key = env.get("SERVICETITAN_APP_KEY_" + code) or env.get("SERVICETITAN_APP_KEY") or None
            if not app_key and "appKey" in (tenant.get("secrets") or []):
                app_key = self.secrets.get(secret_key(name, code, "appKey"))
            if not app_key:
                # The app key belongs to the developer-portal app, so tenants of one profile normally share it.
                for other in self.tenants:
                    if other is not tenant and "appKey" in (other.get("secrets") or []):
                        app_key = self.secrets.get(secret_key(name, other["code"], "appKey"))
                        if app_key:
                            break
        creds = Credentials(code, tenant.get("label") or code, tenant_id or "", client_id or "",
                            client_secret or "", app_key or "", environment)
        if validate:
            missing = [n for n, v in (("tenant id", creds.tenant_id), ("client id", creds.client_id),
                                      ("client secret", creds.client_secret), ("app key", creds.app_key)) if not v]
            if missing:
                if self._source == "store":
                    hint = ("Set them with `servicetitan tenant set-secret %s --tenant %s --client-secret-stdin` / "
                            "`--app-key-stdin`, or export SERVICETITAN_CLIENT_SECRET_%s / SERVICETITAN_APP_KEY."
                            % (self.profile_name, code, code))
                else:
                    base = "_" + suffix if suffix else ""
                    hint = ("Add SERVICETITAN_CLIENT_ID%s, SERVICETITAN_CLIENT_SECRET%s, SERVICETITAN_TENANT_ID%s and "
                            "SERVICETITAN_APP_KEY (shared) to .env or the environment." % (base, base, base))
                raise ConfigError("Tenant %s (%s) is missing credentials: %s" % (code, creds.label, ", ".join(missing)),
                                  hint=hint)
        self._creds[code] = creds
        return creds

    def validate_credentials(self):
        """Resolve every tenant up front and refuse duplicates. Returns {code: Credentials}."""
        by_tenant = {}
        out = {}
        for t in self.tenants:
            c = self.credentials_for(t)
            if c.tenant_id in by_tenant:
                raise ConfigError("Tenants %s and %s resolve to the same ServiceTitan tenant id %s"
                                  % (by_tenant[c.tenant_id], c.code, c.tenant_id),
                                  hint="Both would pull identical data and double every total. Give each its own "
                                       "tenant id (an unsuffixed SERVICETITAN_TENANT_ID is inherited by every "
                                       "location that lacks its own).")
            by_tenant[c.tenant_id] = c.code
            out[c.code] = c
        return out

    # ---- snapshot location
    @property
    def data_path(self):
        if self.explicit_data_path:
            return self.explicit_data_path
        if self.source in ("legacy",):
            return "live_data.json"
        return snapshot_path(self.profile_name)

    # ---- dashboard-facing settings
    def dashboard_settings(self):
        cfg = self.config
        return {
            "company": cfg.get("company") or {},
            "locations": [{"code": t["code"], "label": t.get("label") or t["code"]} for t in self.tenants],
            "targets": cfg.get("targets") or {},
            "rules": cfg.get("rules") or {},
        }

    def window_starts(self):
        """(operational_start, ar_start) as ISO UTC strings, like the template."""
        w = self.config.get("window") or {}
        start = w.get("operationalStart")
        if start:
            op = "%sT00:00:00Z" % start if len(str(start)) == 10 else str(start)
        else:
            op = "%d-01-01T00:00:00Z" % self.as_of.year
        months = int(w.get("arMonthsBack") or 20)
        ar_dt = dt.datetime.now(UTC) - dt.timedelta(days=months * 31)
        return op, ar_dt.strftime("%Y-%m-%dT00:00:00Z")

    def describe(self):
        cfg = self.config
        return {
            "profile": self.profile_name,
            "source": self.source,
            "environment": self.environment,
            "timezone": cfg.get("timezone") or "UTC",
            "company": cfg.get("company"),
            "tenants": [{"code": t["code"], "label": t.get("label"), "tenantId": t.get("tenantId"),
                         "clientId": t.get("clientId"), "secretsStored": t.get("secrets", [])} for t in self.tenants],
            "dataPath": self.data_path,
            "secrets": self.secrets.describe() if self.source == "store" else {"backend": "env/.env"},
        }
