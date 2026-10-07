"""First run: ``servicetitan setup`` (interactive) and ``servicetitan init`` (checklist).

``setup`` is for a person at a terminal. It asks for the four values ServiceTitan
issues, reads the client secret and app key through hidden prompts, tests the
connection before saving anything, and offers the first data pull. Questions go
to stderr and the summary to stdout, like every other command. Without a
terminal it refuses and prints the scripted equivalent, so an agent never hangs
on a prompt.

``init`` is the state-aware checklist: what is done, what is next, and the one
command to run now. It never calls ServiceTitan.
"""
import datetime as dt
import getpass
import os
import sys

from . import __version__
from .dates import UTC, get_tz, iso_z, parse_iso
from .errors import CliError, ConfigError, UsageError
from .paths import config_dir

TIMEZONES = (("Eastern", "America/New_York"), ("Central", "America/Chicago"), ("Mountain", "America/Denver"),
             ("Arizona", "America/Phoenix"), ("Pacific", "America/Los_Angeles"), ("Alaska", "America/Anchorage"),
             ("Hawaii", "Pacific/Honolulu"))

SCRIPTED = (
    'servicetitan profile add <name> --company "<company name>" --timezone <IANA zone> --use',
    'servicetitan tenant add <name> --code MAIN --label "Main Office" --tenant-id <id> --client-id <id> '
    '--client-secret-stdin --app-key-stdin   (client secret on the first line of stdin, app key on the second)',
    "servicetitan auth check",
    "servicetitan fetch",
)

CREDENTIAL_STEPS = (
    "Developer portal (https://developer.servicetitan.io): sign in, register a new app, tick the read scopes you "
    "need, add your tenant ID, save. Copy the App Key from the app page.",
    "ServiceTitan: Settings > Integrations > API Application Access > Connect New App, pick the app, Allow Access.",
    "Same page: generate the Client ID and Client Secret (the secret is shown once). The Tenant ID is there too.",
    "Run `servicetitan setup` and type the four values when asked.",
)

KEYRING_NAMES = (("Windows", "Windows Credential Manager"), ("macOS", "the macOS Keychain"),
                 ("SecretService", "the Secret Service keyring"), ("kwallet", "KWallet"), ("KWallet", "KWallet"))


# ---------------------------------------------------------------- prompting
class Prompter(object):
    """Questions on stderr, answers from stdin; secrets through getpass so they are never echoed."""

    def __init__(self, stdin=None, stderr=None, getpass_fn=None):
        self.stdin = stdin or sys.stdin
        self.err = stderr or sys.stderr
        self._getpass = getpass_fn

    def interactive(self):
        return bool(getattr(self.stdin, "isatty", lambda: False)())

    def say(self, text=""):
        self.err.write(text + "\n")
        self.err.flush()

    def _line(self, prompt):
        self.err.write(prompt)
        self.err.flush()
        line = self.stdin.readline()
        if line == "":
            raise UsageError("Setup stopped: no more input", hint="Run `servicetitan setup` again in a terminal.")
        return line.strip()

    def ask(self, label, default=None, check=None, secret=False):
        while True:
            prompt = "  %s%s: " % (label, (" [%s]" % default) if default else "")
            if secret:
                read = self._getpass or (lambda text: getpass.getpass(text, stream=self.err))
                value = (read(prompt) or "").strip()
            else:
                value = self._line(prompt) or (default or "")
            if not value:
                self.say("    An answer is needed here.")
                continue
            problem = check(value) if check else None
            if problem:
                self.say("    " + problem)
                continue
            return value

    def yes(self, question, default=True):
        while True:
            answer = self._line("  %s [%s]: " % (question, "Y/n" if default else "y/N")).lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self.say("    Please answer y or n.")


def store_words(secrets):
    """Where secrets go, in words a person recognises."""
    d = secrets.describe()
    if d.get("backend") == "keyring":
        detail = str(d.get("detail") or "")
        for needle, words in KEYRING_NAMES:
            if needle.lower() in detail.lower():
                return words
        return "this computer's password store"
    return "%s, readable only by you" % (d.get("detail") or "a private file in the settings folder")


def local_zone():
    """The machine's IANA zone when it can be read without extra packages, else None (never UTC)."""
    candidates = [os.environ.get("TZ") or ""]
    try:
        real = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in real:
            candidates.append(real.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    for name in candidates:
        name = name.lstrip(":")
        if name and "/" in name and not name.startswith("Etc/") and get_tz(name):
            return name
    return None


def _zone(answer):
    if answer.isdigit() and 1 <= int(answer) <= len(TIMEZONES):
        return TIMEZONES[int(answer) - 1][1]
    return answer


def _check_zone(answer):
    zone = _zone(answer)
    return None if ("/" in zone and get_tz(zone)) else \
        "Pick 1-%d, or type a zone name such as America/Chicago or Australia/Sydney." % len(TIMEZONES)


# ---------------------------------------------------------------- setup
def run_setup(ctx, profile=None, environment=None, offer_fetch=True, prompter=None, client_factory=None, fetch_fn=None):
    """Interactive first-run setup. Returns the summary printed on stdout."""
    from .api import ServiceTitanClient
    from .config import Credentials
    from .endpoints import missing_scopes
    from .fetch import FETCH_ENDPOINTS, dept_for_business_unit
    from .secrets import secret_key
    from .store import new_profile, validate_profile_name, validate_tenant_code
    from .writes import describe_policy

    p = prompter or Prompter()
    if not p.interactive():
        raise UsageError("`servicetitan setup` asks questions, so it needs a person at a terminal",
                         hint="Run it in your own terminal window. Without prompts: " + " ; ".join(SCRIPTED),
                         details={"scripted": list(SCRIPTED)})
    store = ctx.store
    secrets = ctx.secrets

    p.say("Set up servicetitan-cli")
    p.say("")
    p.say("You need four values from ServiceTitan: the app key, tenant ID, client ID and client secret.")
    p.say("No values yet? Follow docs/setup-servicetitan-api-access.md (about ten minutes), then run this again.")
    p.say("The client secret and app key are typed into hidden prompts and saved to %s." % store_words(secrets))
    p.say("Never paste them into a chat or an email.")

    # ---- 1. company
    p.say("")
    p.say("Step 1 of 4: the company")
    existing = sorted(store.profiles)
    if existing and not profile:
        p.say("  Companies already set up here: %s. Press Enter to add an account to %s, or type a new name."
              % (", ".join(existing), store.default_name() or existing[0]))

    def check_name(value):
        try:
            validate_profile_name(value)
        except ConfigError as e:
            return e.hint
        return None
    name = profile or p.ask("Short name for this company, lowercase (e.g. acme)",
                            default=(store.default_name() or existing[0]) if existing else None, check=check_name)
    is_new = name not in store.profiles
    if is_new:
        company = p.ask("Company name, as it should appear on reports")
        p.say("  Time zone, used for month and year boundaries:")
        for i, (label, zone) in enumerate(TIMEZONES, 1):
            p.say("    %d  %-9s %s" % (i, label, zone))
        zone = _zone(p.ask("Pick 1-%d, or type a zone name such as Australia/Sydney" % len(TIMEZONES),
                           default=local_zone(), check=_check_zone))
        settings = new_profile(company, timezone=zone)
        env = environment or "production"
    else:
        settings = store.effective(name)
        company = (settings.get("company") or {}).get("name")
        zone = settings.get("timezone")
        p.say("  Adding a ServiceTitan account to %s (%s, %s). Accounts already here: %s."
              % (name, company, zone, ", ".join(t["code"] for t in settings["tenants"]) or "none"))
        env = environment or settings.get("environment") or "production"
    mapping = settings["departmentMapping"]
    current = [] if is_new else store.profiles[name].get("tenants", [])
    taken_codes = {t["code"].upper() for t in current}
    taken_ids = {str(t.get("tenantId")) for t in current}

    # ---- 2. account
    p.say("")
    p.say("Step 2 of 4: the ServiceTitan account")

    def check_code(value):
        try:
            validate_tenant_code(value)
        except ConfigError as e:
            return e.hint
        return "%s is already used in %s; pick another." % (value.upper(), name) if value.upper() in taken_codes else None
    code = p.ask("Short code for this account, used to label its records (e.g. MAIN or NORTH)",
                 default=None if "MAIN" in taken_codes else "MAIN", check=check_code).upper()
    label = p.ask("Name for this account or location", default="Main Office" if code == "MAIN" else code.title())
    sibling = next((t for t in current if "appKey" in (t.get("secrets") or [])), None)
    sibling_key = None
    if sibling:
        try:
            sibling_key = secrets.get(secret_key(name, sibling["code"], "appKey"))
        except CliError:
            sibling_key = None

    def check_tenant_id(value):
        if not value.isdigit():
            return "The tenant ID is all digits; it is under Settings > Integrations > API Application Access."
        if value in taken_ids:
            return "Tenant %s is already connected in %s; adding it twice would double every total." % (value, name)
        return None

    tenant_id = client_id = None
    report = None
    while True:
        tenant_id = p.ask("Tenant ID", default=tenant_id, check=check_tenant_id)
        client_id = p.ask("Client ID", default=client_id)
        p.say("  The next answers stay hidden as you type. Pasting works.")
        secret = p.ask("Client secret", secret=True)
        if sibling_key and p.yes("Use the app key already saved for %s?" % sibling["code"], True):
            app_key = sibling_key
        else:
            app_key = p.ask("App key", secret=True)

        # ---- 3. test before saving anything
        p.say("")
        p.say("Step 3 of 4: testing the connection")
        creds = Credentials(code, label, tenant_id, client_id, secret, app_key, env)
        try:
            report = (client_factory or ServiceTitanClient)(creds).check()
            break
        except CliError as e:
            p.say("  Could not connect: %s" % e.message)
            if e.hint:
                p.say("  " + e.hint)
            if p.yes("Enter the tenant ID, client ID, client secret and app key again?", True):
                p.say("")
                p.say("Step 2 of 4: the ServiceTitan account, second try")
                continue
            if p.yes("Save these values anyway and fix them later with `servicetitan tenant set-secret`?", False):
                report = None
                break
            raise UsageError("Setup stopped before saving anything",
                             hint="Run `servicetitan setup` again when the values are ready.")

    groups, missing, write_scopes, read_only = {}, [], [], False
    if report is not None:
        for unit in report.get("businessUnits") or []:
            groups.setdefault(dept_for_business_unit(unit.get("name"), mapping) or "EXCLUDED", []).append(unit.get("name"))
        p.say("  Connected. %d business unit(s): %s." % (
            sum(len(v) for v in groups.values()),
            ", ".join("%s %d" % (k, len(v)) for k, v in sorted(groups.items())) or "none"))
        if "Other" in groups:
            p.say("  Units grouped as Other count toward company totals but appear on no department tab. To change that:")
            p.say("    servicetitan profile set %s --department-mapping '{\"Service\": [\"service\"], ...}'" % name)
        granted = report.get("grantedScopes")
        if granted is not None:
            missing = missing_scopes(granted, FETCH_ENDPOINTS)
            write_scopes = sorted(s for s in granted if s.endswith(":w"))
        if missing:
            p.say("  The app is missing read permissions the data pull uses: %s." % ", ".join(missing))
            p.say("  Add them to the app in the developer portal, then approve the new version in ServiceTitan.")
        policy_now = list(((settings.get("writes") or {}).get("allow")) or []) if not is_new else ["auto"]
        if write_scopes and policy_now:
            p.say("  This app can also change data in ServiceTitan (%d write permission(s), such as %s)."
                  % (len(write_scopes), write_scopes[0]))
            read_only = p.yes("Keep %s read-only? Recommended for a first try" % name, True)
        elif not write_scopes:
            p.say("  The app can only read, so nothing here can change your ServiceTitan data.")

    # ---- save
    if is_new:
        store.add(name, company_name=company, timezone=zone, environment=env)
    tenant = store.add_tenant(name, code, label, tenant_id, client_id,
                              env if env != (store.profiles[name].get("environment") or "production") else None)
    secrets.set(secret_key(name, code, "clientSecret"), secret)
    store.mark_secret(name, code, "clientSecret")
    secrets.set(secret_key(name, code, "appKey"), app_key)
    store.mark_secret(name, code, "appKey")
    if report is not None:
        tenant["verifiedAt"] = iso_z(dt.datetime.now(UTC))
    if read_only:
        store.update_settings(name, {"writes": {"allow": []}})
    made_default = False
    if store.default_name() != name and (len(store.profiles) == 1 or
                                         p.yes("Make %s the company every command uses by default?" % name, True)):
        store.set_default(name)
        made_default = True
    store.data["secretsBackend"] = secrets.name
    store.save()
    p.say("  Saved. The client secret and app key are in %s." % store_words(secrets))

    # ---- 4. first pull
    p.say("")
    p.say("Step 4 of 4: the first data pull")
    snapshot = None
    if not offer_fetch or fetch_fn is None:
        p.say("  Skipped. Run `servicetitan fetch` when you are ready.")
    elif report is None:
        p.say("  Skipped until the connection works. Fix the values, then run `servicetitan auth check` and "
              "`servicetitan fetch`.")
    elif p.yes("Pull your data now? A year of jobs, invoices and calls takes a few minutes", True):
        try:
            result = fetch_fn(name)
            counts = result.get("counts") or {}
            snapshot = {"written": result.get("written"), "generatedAt": result.get("generatedAt"), "counts": counts}
            p.say("  Done: %d jobs, %d open invoices, %d technicians."
                  % (counts.get("jobs", 0), counts.get("ar", 0), counts.get("technicians", 0)))
        except CliError as e:
            snapshot = {"error": e.to_dict()["error"]}
            p.say("  The pull failed: %s" % e.message)
            if e.hint:
                p.say("  " + e.hint)
            p.say("  Your company is saved; run `servicetitan fetch` to try again.")
    else:
        p.say("  Skipped. Run `servicetitan fetch` when you are ready.")

    ready = bool(snapshot and not snapshot.get("error"))
    pre = "" if (made_default or store.default_name() == name) else "--profile %s " % name
    nxt = ("servicetitan %sbrief" % pre) if ready else ("servicetitan %sfetch" % pre if report is not None
                                                         else "servicetitan %sauth check" % pre)
    p.say("")
    p.say("All set. Next: %s" % nxt if report is not None else "Saved, but not connected yet. Next: %s" % nxt)
    return {"setup": "complete" if report is not None else "saved-unverified", "profile": name, "company": company,
            "timezone": zone, "environment": env, "isDefault": store.default_name() == name,
            "tenant": {"code": code, "label": label, "tenantId": tenant_id, "clientId": client_id},
            "verified": report is not None,
            "businessUnits": {k: len(v) for k, v in sorted(groups.items())},
            "missingScopes": missing, "writeScopes": write_scopes,
            "writes": describe_policy([] if read_only else (((store.effective(name).get("writes") or {}).get("allow")) or [])),
            "secrets": secrets.describe(), "snapshot": snapshot, "next": nxt}


def record_verified(store, profile, codes):
    """Stamp tenants that just authenticated, so `init` can show the connection works."""
    if not codes:
        return
    now = iso_z(dt.datetime.now(UTC))
    for code in codes:
        try:
            store.get_tenant(profile, code)["verifiedAt"] = now
        except CliError:
            continue
    store.save()


# ---------------------------------------------------------------- init
def _step(steps, key, done, detail, run=None):
    steps.append({"n": len(steps) + 1, "step": key, "done": bool(done), "detail": detail, "run": run})


def checklist(ctx):
    """What is set up on this machine and the next command to run. Offline and cheap."""
    steps = []
    _step(steps, "install", True, "servicetitan-cli %s on Python %d.%d" % ((__version__,) + tuple(sys.version_info[:2])))
    profile, tenants, source = None, [], None
    try:
        cfg = ctx.config
        profile, tenants, source = cfg.get("profile"), list(ctx.tenants), ctx.source
    except ConfigError:
        pass
    if not profile:
        _step(steps, "connect", False, "No company connected yet; have the four ServiceTitan values ready",
              "servicetitan setup")
    elif not tenants:
        _step(steps, "connect", False, "%s has no ServiceTitan account yet" % profile,
              "servicetitan setup --profile %s" % profile)
    else:
        incomplete = []
        for t in tenants:
            try:
                ctx.credentials_for(t)
            except CliError:
                incomplete.append(t["code"])
        if incomplete:
            _step(steps, "connect", False, "%s: %s is missing a secret" % (profile, ", ".join(incomplete)),
                  "servicetitan tenant set-secret %s --code %s" % (profile, incomplete[0]))
        else:
            _step(steps, "connect", True, "%s with %s" % (profile, ", ".join(t["code"] for t in tenants)))
            if source == "store":
                stamps = [t.get("verifiedAt") for t in tenants]
                if all(stamps):
                    _step(steps, "test", True, "connected on %s" % min(stamps)[:10])
                else:
                    _step(steps, "test", False, "connection not tested yet", "servicetitan auth check")
    have_snapshot = False
    if profile:
        try:
            path = ctx.data_path
            have_snapshot = os.path.exists(path)
        except CliError:
            path = None
        if have_snapshot:
            when = None
            try:
                import json
                with open(path, encoding="utf-8") as f:
                    when = parse_iso((json.load(f) or {}).get("generatedAt"))
            except (OSError, ValueError, TypeError):
                pass
            age = ("%.0f hours old" % ((dt.datetime.now(UTC) - when).total_seconds() / 3600.0)) if when else "present"
            _step(steps, "first pull", True, "snapshot %s" % age)
        else:
            _step(steps, "first pull", False, "no data pulled yet (a few minutes)", "servicetitan fetch")
    else:
        _step(steps, "first pull", False, "after connecting", "servicetitan fetch")
    _step(steps, "try it", have_snapshot, "the day's picture in one call", "servicetitan brief")
    nxt = next((s["run"] for s in steps if not s["done"] and s["run"]), "servicetitan brief")
    secrets = ctx.secrets
    return {"ready": all(s["done"] for s in steps), "next": nxt, "steps": steps,
            "secrets": dict(secrets.describe(), where=store_words(secrets)), "configDir": config_dir(),
            "howToGetCredentials": list(CREDENTIAL_STEPS),
            "guide": "SETUP.md in the project folder walks through everything, including installing Python.",
            "scripted": list(SCRIPTED)}
