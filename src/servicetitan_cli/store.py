"""The profile store: which companies and tenants this machine knows about.

A *profile* is one company as the dashboard sees it: a name, one or more
ServiceTitan *tenants* (the dashboard template called these "locations"),
department mapping, targets and rules. Profiles hold no secrets; those go to
the :mod:`secrets` store keyed by profile and tenant code.

File: ``profiles.json`` in the config directory::

    {
      "version": 1,
      "default": "acme",
      "secretsBackend": "keyring",
      "profiles": {
        "acme": {
          "company": {"name": "Acme Home Services", "subtitle": "Operations Dashboard"},
          "timezone": "America/Denver",
          "environment": "production",
          "tenants": [
            {"code": "MAIN", "label": "Main Office", "tenantId": "123456789",
             "clientId": "cid.xxxx", "secrets": ["clientSecret", "appKey"]}
          ],
          "departmentMapping": {...}, "targets": {...}, "rules": {...},
          "window": {...}, "personAliases": {...}
        }
      }
    }
"""
import copy
import json
import os
import re

from .errors import ConfigError
from .paths import profiles_path, write_private

PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
TENANT_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,15}$")
ENVIRONMENTS = ("production", "integration")

DEFAULT_MAPPING = {
    "Install": ["install", "construction", "solar"],
    "Sales": ["sales"],
    "Service": ["service", "maint"],
    "exclude": ["warehouse"],
}
DEFAULT_TARGETS = {"avgTicket": None, "membershipAttachPct": None, "lowTicketPct": None, "optionsPerCall": None}
DEFAULT_RULES = {"lowTicketThreshold": 150, "boardDays": 45}
DEFAULT_WINDOW = {"operationalStart": None, "arMonthsBack": 20}
SETTING_KEYS = ("company", "timezone", "environment", "departmentMapping", "targets", "rules", "window", "personAliases")


def new_profile(company_name=None, subtitle="Operations Dashboard", timezone=None, environment="production"):
    return {
        "company": {"name": company_name or "Operations Dashboard", "subtitle": subtitle},
        "timezone": timezone or "UTC",
        "environment": environment,
        "tenants": [],
        "departmentMapping": copy.deepcopy(DEFAULT_MAPPING),
        "targets": copy.deepcopy(DEFAULT_TARGETS),
        "rules": copy.deepcopy(DEFAULT_RULES),
        "window": copy.deepcopy(DEFAULT_WINDOW),
        "personAliases": {},
    }


def validate_profile_name(name):
    if not name or not PROFILE_NAME_RE.match(name):
        raise ConfigError("Invalid profile name %r" % (name,),
                          hint="Use 1-40 lowercase letters, digits, '-' or '_', e.g. `acme` or `viking-air`.")
    return name


def validate_tenant_code(code):
    if not code or not TENANT_CODE_RE.match(code):
        raise ConfigError("Invalid tenant code %r" % (code,),
                          hint="Use 1-16 letters, digits, '-' or '_' with no spaces, e.g. `MAIN`, `SD` or `TUCSON`. "
                               "The code prefixes every record id in snapshots, so keep it short.")
    return code.upper()


def validate_environment(env):
    env = (env or "production").lower()
    if env not in ENVIRONMENTS:
        raise ConfigError("Unknown environment %r" % env, hint="Use `production` or `integration`.")
    return env


class ProfileStore(object):
    def __init__(self, path=None):
        self.path = path or profiles_path()
        self.data = {"version": 1, "default": None, "secretsBackend": None, "profiles": {}}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                try:
                    loaded = json.load(f)
                except json.JSONDecodeError as e:
                    raise ConfigError("%s is not valid JSON: %s" % (self.path, e),
                                      hint="Fix the file by hand or move it aside and re-add your profiles.")
            if not isinstance(loaded, dict) or not isinstance(loaded.get("profiles"), dict):
                raise ConfigError("%s does not look like a profile store" % self.path)
            self.data.update(loaded)

    # ------------------------------------------------------------ persistence
    def save(self):
        write_private(self.path, json.dumps(self.data, indent=2, sort_keys=True))
        return self.path

    # ------------------------------------------------------------ profiles
    @property
    def profiles(self):
        return self.data["profiles"]

    def names(self):
        return sorted(self.profiles)

    def exists(self, name):
        return name in self.profiles

    def get(self, name):
        if name not in self.profiles:
            raise ConfigError("No profile named %r" % name,
                              hint="Run `servicetitan profile list` to see profiles, or `servicetitan profile add %s`." % name)
        return self.profiles[name]

    def default_name(self):
        d = self.data.get("default")
        if d and d in self.profiles:
            return d
        if len(self.profiles) == 1:
            return next(iter(self.profiles))
        return None

    def add(self, name, **kw):
        validate_profile_name(name)
        if name in self.profiles:
            raise ConfigError("Profile %r already exists" % name,
                              hint="Use `servicetitan profile show %s`, or remove it first." % name)
        prof = new_profile(kw.get("company_name"), kw.get("subtitle") or "Operations Dashboard",
                           kw.get("timezone"), validate_environment(kw.get("environment")))
        if kw.get("locations_label"):
            prof["company"]["locationsLabel"] = kw["locations_label"]
        self.profiles[name] = prof
        if not self.data.get("default"):
            self.data["default"] = name
        return prof

    def remove(self, name):
        prof = self.get(name)
        del self.profiles[name]
        if self.data.get("default") == name:
            self.data["default"] = next(iter(self.profiles), None)
        return prof

    def set_default(self, name):
        self.get(name)
        self.data["default"] = name

    def update_settings(self, name, settings):
        """Merge dashboard settings (company, targets, rules, ...) into a profile."""
        prof = self.get(name)
        for key, value in settings.items():
            if key not in SETTING_KEYS:
                raise ConfigError("Unknown profile setting %r" % key, hint="Settings: %s" % ", ".join(SETTING_KEYS))
            if key == "environment":
                value = validate_environment(value)
            if isinstance(value, dict) and isinstance(prof.get(key), dict):
                prof[key].update(value)
            else:
                prof[key] = value
        return prof

    # ------------------------------------------------------------ tenants
    def tenants(self, name):
        return self.get(name)["tenants"]

    def get_tenant(self, name, code):
        code_u = (code or "").upper()
        for t in self.tenants(name):
            if t["code"].upper() == code_u:
                return t
        raise ConfigError("Profile %r has no tenant with code %r" % (name, code),
                          hint="Codes in this profile: %s" % (", ".join(t["code"] for t in self.tenants(name)) or "(none)"))

    def add_tenant(self, name, code, label, tenant_id, client_id, environment=None):
        prof = self.get(name)
        code = validate_tenant_code(code)
        if any(t["code"].upper() == code for t in prof["tenants"]):
            raise ConfigError("Tenant code %r is already used in profile %r" % (code, name))
        tenant_id = str(tenant_id or "").strip()
        if not tenant_id.isdigit():
            raise ConfigError("Tenant id must be the numeric id from ServiceTitan (got %r)" % tenant_id,
                              hint="Find it in ServiceTitan under Settings > Integrations > API Application Access, "
                                   "or in the developer portal app's tenant list.")
        if any(t["tenantId"] == tenant_id for t in prof["tenants"]):
            raise ConfigError("Tenant id %s is already registered in profile %r" % (tenant_id, name),
                              hint="Two tenants with one id would pull the same data twice and double every total.")
        tenant = {"code": code, "label": label or code, "tenantId": tenant_id,
                  "clientId": (client_id or "").strip(), "secrets": []}
        if environment:
            tenant["environment"] = validate_environment(environment)
        prof["tenants"].append(tenant)
        return tenant

    def remove_tenant(self, name, code):
        prof = self.get(name)
        tenant = self.get_tenant(name, code)
        prof["tenants"] = [t for t in prof["tenants"] if t is not tenant]
        return tenant

    def mark_secret(self, name, code, field, stored=True):
        t = self.get_tenant(name, code)
        have = set(t.get("secrets") or [])
        if stored:
            have.add(field)
        else:
            have.discard(field)
        t["secrets"] = sorted(have)

    # ------------------------------------------------------------ views
    def effective(self, name):
        """A self-contained settings dict for one profile (no secrets)."""
        prof = self.get(name)
        cfg = copy.deepcopy(new_profile())
        for key in SETTING_KEYS:
            if key in prof:
                if isinstance(cfg.get(key), dict) and isinstance(prof[key], dict):
                    cfg[key].update(prof[key])
                else:
                    cfg[key] = prof[key]
        cfg["profile"] = name
        cfg["tenants"] = copy.deepcopy(prof.get("tenants") or [])
        cfg["rules"].setdefault("lowTicketThreshold", 150)
        cfg["rules"].setdefault("boardDays", 45)
        return cfg

    def export(self, name):
        """Shareable settings: everything except tenant ids, client ids and secrets."""
        cfg = self.effective(name)
        cfg["tenants"] = [{"code": t["code"], "label": t.get("label")} for t in cfg["tenants"]]
        return cfg
