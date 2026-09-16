"""Command-line entry point.

Design rules, all in service of agents driving this tool:

* stdout carries exactly one JSON document; everything else goes to stderr.
* Errors are JSON envelopes on stderr with stable codes and exit statuses.
* Nothing ever prompts unless stdin is a terminal and a secret was omitted.
* Global options are accepted before or after the command.
* ``servicetitan schema`` describes every command, so one call teaches the tool.
"""
import argparse
import getpass
import json
import os
import sys

from . import __version__
from .config import Context
from .dates import RANGE_KINDS, parse_iso
from .errors import CliError, ConfigError, DataError, UsageError, ValidationError
from .output import emit, emit_error, progress, redact
from .paths import snapshot_path

EXAMPLES = """
examples:
  servicetitan profile add acme --company "Acme Home Services" --timezone America/Denver
  servicetitan tenant add acme --code MAIN --label "Main Office" --tenant-id 123456789 \\
      --client-id cid.xxxx --client-secret-stdin --app-key-stdin < secrets.txt
  servicetitan auth check
  servicetitan fetch --max-age 6            # pull only if the snapshot is older than 6h
  servicetitan metrics service --range mtd  # KPIs, targets, technician scorecard
  servicetitan metrics all --explain        # every area with metric definitions
  servicetitan list invoices --min-days-past-due 60 --sort -balance --limit 20
  servicetitan list jobs --range last7 --booked --fields jobNumber,date,customer,revenue
  servicetitan api get jpm/v2/jobs --param jobStatus=Completed --param pageSize=10
  servicetitan api endpoints --search "appointments"
  servicetitan render --out dashboard.html
  servicetitan schema                       # machine-readable description of everything above
"""


# ---------------------------------------------------------------- parser
def _common(parser):
    g = parser.add_argument_group("global options")
    g.add_argument("-p", "--profile", default=argparse.SUPPRESS, metavar="NAME",
                   help="profile (company) to use; default: SERVICETITAN_PROFILE or the store's default")
    g.add_argument("-t", "--tenant", default=argparse.SUPPRESS, metavar="CODE",
                   help="restrict to one tenant of the profile (default: all)")
    g.add_argument("--config", default=argparse.SUPPRESS, metavar="PATH",
                   help="legacy dashboard config.json (implies --env-file .env beside it)")
    g.add_argument("--env-file", default=argparse.SUPPRESS, metavar="PATH", help="legacy .env with SERVICETITAN_* keys")
    g.add_argument("--data", default=argparse.SUPPRESS, metavar="PATH", help="snapshot file to read or write")
    g.add_argument("-f", "--format", default=argparse.SUPPRESS, choices=("json", "text"),
                   help="output format (default json; SERVICETITAN_FORMAT)")
    g.add_argument("--compact", default=argparse.SUPPRESS, action="store_true", help="single-line JSON")
    g.add_argument("--as-of", default=argparse.SUPPRESS, metavar="DATETIME",
                   help="treat this instant as 'now' (ISO date or datetime) for reproducible answers")
    g.add_argument("--secrets", default=argparse.SUPPRESS, choices=("auto", "keyring", "file"),
                   help="secret backend (default auto: OS keyring, else owner-only file)")
    g.add_argument("-q", "--quiet", default=argparse.SUPPRESS, action="store_true", help="no progress on stderr")
    return parser


def _sub(subparsers, name, help_text, description=None, aliases=()):
    p = subparsers.add_parser(name, help=help_text, description=description or help_text, aliases=list(aliases),
                              formatter_class=argparse.RawDescriptionHelpFormatter)
    _common(p)
    return p


def _range_args(p, default="mtd", allow_all=False):
    kinds = list(RANGE_KINDS) + (["all"] if allow_all else [])
    p.add_argument("--range", default=default, choices=kinds, help="date range (default %(default)s)")
    p.add_argument("--start", metavar="YYYY-MM-DD", help="start of a custom range (implies --range custom)")
    p.add_argument("--end", metavar="YYYY-MM-DD", help="end of a custom range (default: as-of)")
    p.add_argument("--max-age", type=float, default=6.0, metavar="HOURS",
                   help="snapshot older than this is reported stale (default %(default)s)")
    p.add_argument("--require-fresh", action="store_true", help="exit 7 instead of answering from a stale snapshot")


def build_parser():
    root = argparse.ArgumentParser(
        prog="servicetitan",
        description="ServiceTitan CLI built for AI agents. JSON in, JSON out. Reads by default; a write runs only "
                    "with --write, a profile allowlist match and the operation's :w scope.",
        epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    root.add_argument("--version", action="version", version="servicetitan-cli %s" % __version__)
    _common(root)
    sub = root.add_subparsers(dest="command", metavar="<command>")

    # ---- profiles
    p = _sub(sub, "profile", "manage company profiles (each holds one or more tenants)")
    ps = p.add_subparsers(dest="profile_cmd", metavar="<action>")
    _sub(ps, "list", "list profiles")
    x = _sub(ps, "show", "show one profile (secrets redacted)")
    x.add_argument("name", nargs="?", help="profile name (default: the default profile)")
    x = _sub(ps, "add", "create a profile")
    x.add_argument("name", help="short name, e.g. acme")
    x.add_argument("--company", required=True, help="company name shown on the dashboard")
    x.add_argument("--subtitle", default="Operations Dashboard")
    x.add_argument("--locations-label", help="header caption, e.g. 'Springfield & Shelbyville'")
    x.add_argument("--timezone", default="UTC", help="IANA zone for month/quarter/year boundaries (default %(default)s)")
    x.add_argument("--environment", default="production", choices=("production", "integration"))
    x.add_argument("--use", action="store_true", help="make it the default profile")
    x = _sub(ps, "remove", "delete a profile and its stored secrets")
    x.add_argument("name")
    x = _sub(ps, "use", "set the default profile")
    x.add_argument("name")
    x = _sub(ps, "set", "change profile settings (JSON values for objects)")
    x.add_argument("name", nargs="?")
    x.add_argument("--company", help="company name")
    x.add_argument("--subtitle")
    x.add_argument("--locations-label")
    x.add_argument("--timezone")
    x.add_argument("--environment", choices=("production", "integration"))
    x.add_argument("--targets", help='JSON, e.g. \'{"avgTicket": 280, "membershipAttachPct": 12}\'')
    x.add_argument("--rules", help='JSON, e.g. \'{"lowTicketThreshold": 150, "boardDays": 45}\'')
    x.add_argument("--department-mapping", help='JSON, e.g. \'{"Install": ["install"], "Service": ["service"], "exclude": ["warehouse"]}\'')
    x.add_argument("--window", help='JSON, e.g. \'{"operationalStart": "2026-01-01", "arMonthsBack": 20}\'')
    x.add_argument("--person-aliases", help='JSON map of "CODE:userId" -> canonical name')
    x.add_argument("--allow-writes", metavar="PATTERNS",
                   help="comma-separated write allowlist: module, module/resource, module/resource/operation or * "
                        "(replaces the list), e.g. \"jpm/jobs/create-notes,crm/customers\"")
    x.add_argument("--no-writes", action="store_true", help="clear the write allowlist (the default state)")
    x = _sub(ps, "import", "create a profile from the dashboard template's config.json + .env "
                            "(paths via the global --config / --env-file; default ./config.json and ./.env)")
    x.add_argument("name")
    x.add_argument("--use", action="store_true", help="make it the default profile")
    x = _sub(ps, "export", "print shareable settings (no ids, no secrets)")
    x.add_argument("name", nargs="?")

    # ---- tenants
    p = _sub(sub, "tenant", "manage the ServiceTitan tenants (accounts) inside a profile")
    ts = p.add_subparsers(dest="tenant_cmd", metavar="<action>")
    x = _sub(ts, "list", "list a profile's tenants")
    x.add_argument("name", nargs="?", help="profile name (default: the default profile)")
    x = _sub(ts, "add", "add a tenant with its credentials",
             description="Add a ServiceTitan tenant. Secrets are stored in the OS keyring (or an owner-only file). "
                         "Pass them on stdin (--client-secret-stdin/--app-key-stdin read one line each, in that order), "
                         "from environment variables (--client-secret-env VAR), or interactively when stdin is a terminal.")
    x.add_argument("name", nargs="?", help="profile name (default: the default profile)")
    x.add_argument("--code", required=True, help="short unique code, e.g. MAIN or SD (prefixes every record id)")
    x.add_argument("--label", help="display name, e.g. 'San Diego' (default: the code)")
    x.add_argument("--tenant-id", required=True, help="numeric tenant id from ServiceTitan")
    x.add_argument("--client-id", required=True, help="OAuth client id from API Application Access")
    x.add_argument("--client-secret", help="client secret (prefer --client-secret-stdin; this lands in shell history)")
    x.add_argument("--client-secret-stdin", action="store_true", help="read the client secret from stdin (first line)")
    x.add_argument("--client-secret-env", metavar="VAR", help="read the client secret from this environment variable")
    x.add_argument("--app-key", help="app key from the developer portal (shared by all tenants of the app)")
    x.add_argument("--app-key-stdin", action="store_true", help="read the app key from stdin (next line)")
    x.add_argument("--app-key-env", metavar="VAR")
    x.add_argument("--environment", choices=("production", "integration"), help="override the profile's environment")
    x.add_argument("--no-verify", action="store_true", help="store without test-authenticating")
    x = _sub(ts, "set-secret", "store or replace a tenant's client secret and/or app key")
    x.add_argument("name", nargs="?")
    x.add_argument("--code", required=True)
    x.add_argument("--client-secret")
    x.add_argument("--client-secret-stdin", action="store_true")
    x.add_argument("--client-secret-env", metavar="VAR")
    x.add_argument("--app-key")
    x.add_argument("--app-key-stdin", action="store_true")
    x.add_argument("--app-key-env", metavar="VAR")
    x = _sub(ts, "update", "change a tenant's label, ids or environment")
    x.add_argument("name", nargs="?")
    x.add_argument("--code", required=True)
    x.add_argument("--label")
    x.add_argument("--tenant-id")
    x.add_argument("--client-id")
    x.add_argument("--environment", choices=("production", "integration"))
    x = _sub(ts, "remove", "remove a tenant and delete its secrets")
    x.add_argument("name", nargs="?")
    x.add_argument("--code", required=True)

    # ---- auth / status
    p = _sub(sub, "auth", "verify credentials")
    aus = p.add_subparsers(dest="auth_cmd", metavar="<action>")
    x = _sub(aus, "check", "authenticate every tenant and preview business-unit classification")
    x = _sub(sub, "status", "profile summary plus snapshot freshness and record counts (cheap; run first)")
    x.add_argument("--max-age", type=float, default=6.0, metavar="HOURS")

    # ---- data
    x = _sub(sub, "fetch", "pull a fresh snapshot from ServiceTitan (read-only)")
    x.add_argument("--max-age", type=float, metavar="HOURS",
                   help="skip the pull when the existing snapshot is younger than this")
    x.add_argument("--max-pages", type=int, default=100, help="page cap per collection (default %(default)s)")
    x.add_argument("--page-size", type=int, default=200, help="records per page, max 500 (default %(default)s)")
    x.add_argument("--workers", type=int, default=4, help="tenants pulled concurrently (rate limits are per tenant; default %(default)s)")
    x.add_argument("--out", metavar="PATH", help="write here instead of the profile's snapshot path")
    x = _sub(sub, "demo", "write a synthetic snapshot to try every command offline")
    x.add_argument("--out", metavar="PATH", help="default: the profile's snapshot path (or ./demo_data.json without a profile)")
    x.add_argument("--seed", type=int, default=20260101)
    x = _sub(sub, "check", "run the snapshot integrity checks (exit 6 on failure)")
    x.add_argument("--max-age", type=float, default=6.0, metavar="HOURS")
    x = _sub(sub, "render", "build the self-contained dashboard.html (validates first)")
    x.add_argument("--out", default="dashboard.html", metavar="PATH")
    x.add_argument("--brand-dir", default="brand", metavar="DIR", help="folder with logo-light.png / logo-dark.png")
    x.add_argument("--template", metavar="PATH", help="custom template instead of the bundled one")
    x.add_argument("--skip-checks", action="store_true", help="render even if integrity checks fail")
    x.add_argument("--full", action="store_true", help="embed every snapshot field (default: only what the template reads)")
    x.add_argument("--max-age", type=float, default=6.0, metavar="HOURS")
    x = _sub(sub, "duplicates", "people who may hold two ServiceTitan user records")

    # ---- queries
    x = _sub(sub, "metrics", "derived KPIs for one area or all",
             description="Derived operations metrics, computed exactly as the dashboard computes them. "
                         "Areas: revenue, service, sales, install, accounting, marketing, office, memberships, all.")
    x.add_argument("area", choices=("revenue", "service", "sales", "install", "accounting", "marketing", "office",
                                    "memberships", "all"))
    _range_args(x)
    x.add_argument("--explain", action="store_true", help="include a definitions block for every metric")

    x = _sub(sub, "list", "records from the snapshot with filters",
             description="List snapshot records. Kinds: jobs, opportunities, invoices, purchase-orders, calls, "
                         "memberships, technicians, campaigns, business-units, people. Dates are only applied when "
                         "--range/--start is given (default: everything in the snapshot).")
    x.add_argument("kind", choices=("jobs", "opportunities", "invoices", "purchase-orders", "calls", "memberships",
                                    "technicians", "campaigns", "business-units", "people"))
    _range_args(x, default="all", allow_all=True)
    x.add_argument("--limit", type=int, default=100, help="max records (0 = no limit; default %(default)s)")
    x.add_argument("--sort", metavar="FIELD", help="sort by this field (ascending; add --desc or use --sort=-FIELD)")
    x.add_argument("--desc", action="store_true", help="sort descending")
    x.add_argument("--fields", metavar="A,B,C", help="only these fields per record")
    x.add_argument("--count", action="store_true", help="counts only")
    g = x.add_argument_group("jobs")
    g.add_argument("--completed", action="store_true", help="completed jobs only")
    g.add_argument("--booked", action="store_true", help="booked (future) jobs only")
    g.add_argument("--dept", help="Service, Install, Sales, Other")
    g.add_argument("--billed", action="store_true", help="revenue > 0 only")
    g.add_argument("--no-charge", action="store_true", help="revenue == 0 only")
    g.add_argument("--min-revenue", type=float)
    g.add_argument("--tech", help="technician name contains")
    g.add_argument("--customer", help="customer name contains (jobs, opportunities, invoices, memberships)")
    g.add_argument("--job-number")
    g.add_argument("--campaign", help="campaign name contains (jobs, calls)")
    g = x.add_argument_group("opportunities")
    g.add_argument("--won", action="store_true")
    g.add_argument("--open", action="store_true")
    g.add_argument("--rep", help="rep name contains")
    g.add_argument("--date-field", choices=("presented", "won"), default="presented", help="which date the range applies to")
    g.add_argument("--min-value", type=float)
    g = x.add_argument_group("invoices")
    g.add_argument("--min-days-past-due", type=int)
    g.add_argument("--bucket", choices=("0-30", "31-60", "61-90", "91-120", "120+"))
    g.add_argument("--min-balance", type=float)
    g = x.add_argument_group("purchase-orders")
    g.add_argument("--vendor", help="vendor name contains")
    g.add_argument("--po-status")
    g = x.add_argument_group("calls")
    g.add_argument("--inbound", action="store_true")
    g.add_argument("--outbound", action="store_true")
    g.add_argument("--agent", help="agent name contains")
    g = x.add_argument_group("memberships")
    g.add_argument("--membership-status", help="Active, Suspended, Expired, Canceled ...")
    g.add_argument("--plan", help="plan name contains")
    g.add_argument("--renews-within", type=int, metavar="DAYS")
    g = x.add_argument_group("technicians / campaigns / business-units / people")
    g.add_argument("--name", help="name contains")
    g.add_argument("--category", help="campaign category contains")
    g.add_argument("--active", action="store_true")
    g.add_argument("--inactive", action="store_true")

    # ---- the API tree
    x = _sub(sub, "api", "the whole ServiceTitan API as a command tree: api <module> <resource> <operation>",
             description="Progressive discovery of ServiceTitan's API, generated from its OpenAPI documents.\n\n"
                         "  servicetitan api                         modules\n"
                         "  servicetitan api jpm                     resources in a module\n"
                         "  servicetitan api jpm jobs                operations on a resource\n"
                         "  servicetitan api jpm jobs list --help    every parameter, typed and described\n"
                         "  servicetitan api jpm jobs list --job-status Completed --completed-on-or-after 2026-09-01T00:00:00Z --all\n"
                         "  servicetitan api jpm jobs get 12345\n"
                         "  servicetitan api jpm jobs list --describe   the contract as JSON (no credentials needed)\n\n"
                         "Built-ins: `api get <path>` for a raw path, `api endpoints --search WORDS`, `api modules`,\n"
                         "`api catalogue status|refresh|reset`, `api audit`.\n"
                         "Reads run live for one tenant (--tenant CODE when the profile has several). Writes run only\n"
                         "with --write, when the profile allows the operation (`profile set NAME --allow-writes ...`)\n"
                         "and the token carries its :w scope; --dry-run previews and audit.jsonl records every attempt.")
    x.add_argument("rest", nargs=argparse.REMAINDER, metavar="<module> <resource> <operation> ...")

    # ---- misc
    x = _sub(sub, "palette", "validate chart colours for accessibility (band, chroma, contrast, colour-blind separation)")
    x.add_argument("colours", help="comma-separated hex colours in series order")
    x.add_argument("--mode", choices=("light", "dark"), default="light")
    x.add_argument("--surface", help="chart surface colour (defaults per mode)")
    x.add_argument("--pairs", choices=("adjacent", "all"), default="adjacent")
    x = _sub(sub, "schema", "machine-readable description of every command, output convention and metric definition")
    x = _sub(sub, "init", "print a step-by-step setup guide for this machine (state-aware)")
    return root


# ---------------------------------------------------------------- helpers
def _opt(args, name, default=None):
    return getattr(args, name, default)


def _read_secret(args, field, flag_base, prompt):
    """Resolve a secret from --X, --X-stdin, --X-env or an interactive prompt."""
    direct = _opt(args, flag_base.replace("-", "_"))
    if direct:
        return direct
    env_name = _opt(args, flag_base.replace("-", "_") + "_env")
    if env_name:
        val = os.environ.get(env_name)
        if not val:
            raise UsageError("Environment variable %s is empty" % env_name)
        return val.strip()
    if _opt(args, flag_base.replace("-", "_") + "_stdin"):
        line = sys.stdin.readline()
        if not line.strip():
            raise UsageError("Expected the %s on stdin" % field)
        return line.strip()
    return None


def _prompt_secret(field):
    if sys.stdin.isatty():
        val = getpass.getpass("%s: " % field)
        if val.strip():
            return val.strip()
    raise UsageError("No %s given" % field,
                     hint="Pass --%s-stdin and pipe it in, --%s-env VAR, or --%s VALUE (least safe)."
                          % (field.replace(" ", "-"), field.replace(" ", "-"), field.replace(" ", "-")))


def _json_arg(value, name):
    if value is None:
        return None
    try:
        return json.loads(value)
    except ValueError as e:
        raise UsageError("--%s must be JSON: %s" % (name, e))


def _settings(ctx):
    """Profile settings, or defaults when an explicit --data file is being queried without a profile."""
    return ctx.config_or_default() if ctx.explicit_data_path else ctx.config


def _query(ctx, args, allow_all=False):
    from .metrics import Query
    from .snapshot import load_snapshot
    settings = _settings(ctx)
    snap = load_snapshot(ctx.data_path, demo_fallback=True)
    kind = _opt(args, "range", "mtd")
    start = end = None
    if _opt(args, "start"):
        kind = "custom"
        start = parse_iso(args.start, tz=ctx.tz)
        if start is None:
            raise UsageError("--start must be YYYY-MM-DD")
        if _opt(args, "end"):
            end = parse_iso(args.end, tz=ctx.tz)
            if end is None:
                raise UsageError("--end must be YYYY-MM-DD")
    elif kind == "custom":
        raise UsageError("--range custom needs --start")
    try:
        q = Query(snap, ctx.as_of, ctx.tz, range_kind=(kind if kind != "all" else "ytd"), custom_start=start,
                  custom_end=end, tenant=_opt(args, "tenant"), targets=settings.get("targets"), rules=settings.get("rules"))
    except ValueError as e:
        raise UsageError(str(e))
    q.use_range = kind != "all"
    if q.tenant and q.tenant not in [c.upper() for c in snap.tenant_codes()]:
        raise UsageError("Tenant %r is not in this snapshot" % q.tenant,
                         hint="Tenants in %s: %s" % (snap.path, ", ".join(snap.tenant_codes()) or "(none)"))
    desc = snap.describe(as_of=ctx.as_of, max_age_hours=_opt(args, "max_age", 6.0))
    if desc["stale"]:
        if _opt(args, "require_fresh"):
            raise DataError("Snapshot is %.1fh old (limit %.0fh)" % (desc["ageHours"] or -1, _opt(args, "max_age", 6.0)),
                            hint="Run `servicetitan fetch` first.")
        progress("warning: snapshot is %s old; run `servicetitan fetch` for current numbers"
                 % ("%.1fh" % desc["ageHours"] if desc["ageHours"] is not None else "of unknown age"), ctx.quiet)
    return snap, q


def _single_tenant_client(ctx, args, page_size=None):
    from .api import ServiceTitanClient
    tenants = ctx.select_tenants(_opt(args, "tenant"))
    if len(tenants) != 1:
        raise UsageError("Profile %r has %d tenants; pass --tenant CODE" % (ctx.profile_name, len(tenants)),
                         hint="Codes: %s" % ", ".join(ctx.tenant_codes()))
    creds = ctx.credentials_for(tenants[0])
    return tenants[0], ServiceTitanClient(creds, page_size=page_size or 200,
                                          progress=(lambda m: progress(m, ctx.quiet)))


# ---------------------------------------------------------------- handlers
def cmd_profile(ctx, args):
    store = ctx.store
    action = args.profile_cmd
    if action == "list":
        return {"default": store.default_name(), "storePath": store.path, "secrets": ctx.secrets.describe(),
                "profiles": [{"name": n, "company": (p.get("company") or {}).get("name"), "timezone": p.get("timezone"),
                              "environment": p.get("environment"), "tenants": [t["code"] for t in p.get("tenants", [])]}
                             for n, p in sorted(store.profiles.items())]}
    if action == "show":
        name = args.name or store.default_name()
        if not name:
            raise ConfigError("No profiles yet", hint="Run `servicetitan profile add <name> --company \"...\"`.")
        out = store.effective(name)
        out["isDefault"] = name == store.default_name()
        out["snapshotPath"] = snapshot_path(name)
        return redact(out)
    if action == "add":
        prof = store.add(args.name, company_name=args.company, subtitle=args.subtitle, timezone=args.timezone,
                         environment=args.environment, locations_label=args.locations_label)
        if args.use:
            store.set_default(args.name)
        store.save()
        return {"created": args.name, "isDefault": store.default_name() == args.name, "profile": prof,
                "next": "servicetitan tenant add %s --code MAIN --label \"Main Office\" --tenant-id <id> --client-id <id> "
                        "--client-secret-stdin --app-key-stdin" % args.name}
    if action == "remove":
        prof = store.get(args.name)
        from .secrets import secret_key
        removed = []
        for t in prof.get("tenants", []):
            for field in t.get("secrets") or []:
                ctx.secrets.delete(secret_key(args.name, t["code"], field))
                removed.append(secret_key(args.name, t["code"], field))
        store.remove(args.name)
        store.save()
        return {"removed": args.name, "secretsDeleted": removed, "default": store.default_name()}
    if action == "use":
        store.set_default(args.name)
        store.save()
        return {"default": args.name}
    if action == "set":
        name = args.name or store.default_name()
        if not name:
            raise ConfigError("No profiles yet")
        settings = {}
        company = {}
        if args.company:
            company["name"] = args.company
        if args.subtitle is not None:
            company["subtitle"] = args.subtitle
        if args.locations_label is not None:
            company["locationsLabel"] = args.locations_label
        if company:
            settings["company"] = company
        for key, argname in (("timezone", "timezone"), ("environment", "environment")):
            if getattr(args, argname):
                settings[key] = getattr(args, argname)
        for key, argname in (("targets", "targets"), ("rules", "rules"), ("departmentMapping", "department_mapping"),
                             ("window", "window"), ("personAliases", "person_aliases")):
            val = _json_arg(getattr(args, argname), argname.replace("_", "-"))
            if val is not None:
                if not isinstance(val, dict):
                    raise UsageError("--%s must be a JSON object" % argname.replace("_", "-"))
                settings[key] = val
        if args.no_writes:
            settings["writes"] = {"allow": []}
        elif args.allow_writes is not None:
            from .writes import parse_patterns
            settings["writes"] = {"allow": parse_patterns(args.allow_writes)}
        if not settings:
            raise UsageError("Nothing to change", hint="See `servicetitan profile set --help`.")
        prof = store.update_settings(name, settings)
        if settings.get("timezone"):
            from .dates import get_tz
            if get_tz(settings["timezone"]) is None:
                raise UsageError("Unknown timezone %r" % settings["timezone"], hint="Use an IANA name like America/Denver.")
        store.save()
        return {"updated": name, "profile": redact(store.effective(name))}
    if action == "import":
        from .config import load_env_file, load_legacy_config
        from .secrets import secret_key, env_lookup
        cfg = load_legacy_config(_opt(args, "config") or "config.json")
        env = load_env_file(_opt(args, "env_file") or ".env")
        env.update({k: v for k, v in os.environ.items() if k.startswith("SERVICETITAN_")})
        prof = store.add(args.name, company_name=cfg["company"].get("name"), subtitle=cfg["company"].get("subtitle"),
                         timezone=cfg.get("timezone"), environment=cfg.get("environment"),
                         locations_label=cfg["company"].get("locationsLabel"))
        for key in ("departmentMapping", "targets", "rules", "window", "personAliases"):
            if cfg.get(key) is not None:
                prof[key] = cfg[key]
        stored = []
        missing = []
        for loc in cfg["tenants"]:
            suffix = loc.get("envSuffix")
            tid = loc.get("tenantId") or env_lookup("tenantId", suffix, environ=env)
            cid = loc.get("clientId") or env_lookup("clientId", suffix, environ=env)
            secret = env_lookup("clientSecret", suffix, environ=env)
            app_key = env_lookup("appKey", suffix, environ=env)
            if not (tid and cid):
                missing.append("%s: tenant id/client id" % loc["code"])
                continue
            store.add_tenant(args.name, loc["code"], loc.get("label"), tid, cid)
            for field, value in (("clientSecret", secret), ("appKey", app_key)):
                if value:
                    ctx.secrets.set(secret_key(args.name, loc["code"], field), value)
                    store.mark_secret(args.name, loc["code"], field)
                    stored.append("%s/%s" % (loc["code"], field))
                else:
                    missing.append("%s: %s" % (loc["code"], field))
        store.data["secretsBackend"] = ctx.secrets.name
        if args.use:
            store.set_default(args.name)
        store.save()
        return {"created": args.name, "tenants": [t["code"] for t in prof["tenants"]], "secretsStored": stored,
                "missing": missing, "secrets": ctx.secrets.describe(),
                "note": "The .env and config.json were read, not deleted. Delete .env once `servicetitan auth check` passes."}
    if action == "export":
        name = args.name or store.default_name()
        if not name:
            raise ConfigError("No profiles yet")
        return store.export(name)
    raise UsageError("profile needs an action", hint="list, show, add, remove, use, set, import, export")


def cmd_tenant(ctx, args):
    from .secrets import secret_key
    store = ctx.store
    action = args.tenant_cmd
    name = _opt(args, "name") or store.default_name()
    if not name:
        raise ConfigError("No profiles yet", hint="Run `servicetitan profile add <name> --company \"...\"` first.")
    if action == "list":
        return {"profile": name, "tenants": redact(store.tenants(name))}
    if action == "add":
        secret = _read_secret(args, "client secret", "client-secret", None)
        app_key = _read_secret(args, "app key", "app-key", None)
        if secret is None:
            secret = _prompt_secret("client secret")
        tenant = store.add_tenant(name, args.code, args.label, args.tenant_id, args.client_id, args.environment)
        if app_key is None:
            # The app key is per developer-portal app, so it is usually already stored on a sibling tenant.
            siblings = [t for t in store.tenants(name) if t is not tenant and "appKey" in (t.get("secrets") or [])]
            if siblings:
                app_key = ctx.secrets.get(secret_key(name, siblings[0]["code"], "appKey"))
            if app_key is None:
                app_key = _prompt_secret("app key")
        ctx.secrets.set(secret_key(name, tenant["code"], "clientSecret"), secret)
        store.mark_secret(name, tenant["code"], "clientSecret")
        ctx.secrets.set(secret_key(name, tenant["code"], "appKey"), app_key)
        store.mark_secret(name, tenant["code"], "appKey")
        store.data["secretsBackend"] = ctx.secrets.name
        store.save()
        result = {"profile": name, "added": redact(tenant), "secrets": ctx.secrets.describe()}
        if not args.no_verify:
            from .api import ServiceTitanClient
            ctx2 = Context(profile=name, quiet=ctx.quiet, store_path=store.path)
            creds = ctx2.credentials_for(ctx2.select_tenants(tenant["code"])[0])
            try:
                rep = ServiceTitanClient(creds).check()
                result["verified"] = True
                result["businessUnits"] = len(rep["businessUnits"])
            except CliError as e:
                result["verified"] = False
                result["verifyError"] = e.to_dict()["error"]
                result["hint"] = "Credentials were stored anyway; fix them with `servicetitan tenant set-secret`."
        return result
    if action == "set-secret":
        tenant = store.get_tenant(name, args.code)
        secret = _read_secret(args, "client secret", "client-secret", None)
        app_key = _read_secret(args, "app key", "app-key", None)
        if secret is None and app_key is None:
            secret = _prompt_secret("client secret")
        changed = []
        for field, value in (("clientSecret", secret), ("appKey", app_key)):
            if value:
                ctx.secrets.set(secret_key(name, tenant["code"], field), value)
                store.mark_secret(name, tenant["code"], field)
                changed.append(field)
        store.data["secretsBackend"] = ctx.secrets.name
        store.save()
        return {"profile": name, "tenant": tenant["code"], "updated": changed, "secrets": ctx.secrets.describe()}
    if action == "update":
        tenant = store.get_tenant(name, args.code)
        if args.label:
            tenant["label"] = args.label
        if args.tenant_id:
            if not str(args.tenant_id).strip().isdigit():
                raise UsageError("--tenant-id must be numeric")
            tenant["tenantId"] = str(args.tenant_id).strip()
        if args.client_id:
            tenant["clientId"] = args.client_id.strip()
        if args.environment:
            tenant["environment"] = args.environment
        store.save()
        return {"profile": name, "tenant": redact(tenant)}
    if action == "remove":
        tenant = store.remove_tenant(name, args.code)
        for field in tenant.get("secrets") or []:
            ctx.secrets.delete(secret_key(name, tenant["code"], field))
        store.save()
        return {"profile": name, "removed": tenant["code"], "secretsDeleted": tenant.get("secrets") or []}
    raise UsageError("tenant needs an action", hint="list, add, set-secret, update, remove")


def cmd_auth(ctx, args):
    from .api import ServiceTitanClient
    from .endpoints import missing_scopes
    from .fetch import FETCH_ENDPOINTS, dept_for_business_unit
    if args.auth_cmd != "check":
        raise UsageError("auth needs an action", hint="check")
    mapping = ctx.config["departmentMapping"]
    results = []
    ok_all = True
    for t in ctx.select_tenants(_opt(args, "tenant")):
        entry = {"tenant": t["code"], "label": t.get("label")}
        try:
            creds = ctx.credentials_for(t)
            entry["tenantId"] = creds.tenant_id
            entry["environment"] = creds.environment
            rep = ServiceTitanClient(creds).check()
            buckets = {}
            for u in rep["businessUnits"]:
                buckets.setdefault(dept_for_business_unit(u.get("name"), mapping) or "EXCLUDED", []).append(u.get("name"))
            entry.update({"ok": True, "businessUnits": len(rep["businessUnits"]), "classification": buckets})
            if rep.get("grantedScopes") is not None:
                missing = missing_scopes(rep["grantedScopes"], FETCH_ENDPOINTS)
                entry["scopes"] = {"granted": len(rep["grantedScopes"]), "missingForFetch": missing}
                if missing:
                    entry["scopeHint"] = ("`servicetitan fetch` will get 403s on the endpoints needing these scopes. "
                                          "Add the read scopes in the developer portal, then re-approve the app in "
                                          "ServiceTitan (Settings > Integrations > API Application Access).")
            if "Other" in buckets:
                entry["note"] = ("'Other' units count toward company totals but appear on no department tab; add a "
                                 "keyword to departmentMapping if that is wrong.")
        except CliError as e:
            ok_all = False
            entry.update({"ok": False, "error": e.to_dict()["error"]})
        results.append(entry)
    out = {"profile": ctx.profile_name, "source": ctx.source, "ok": ok_all, "tenants": results}
    if not ok_all:
        from .errors import AuthError
        err = AuthError("%d of %d tenant(s) failed to authenticate" % (sum(1 for r in results if not r["ok"]), len(results)),
                        details=out)
        raise err
    return out


def cmd_status(ctx, args):
    from .snapshot import load_snapshot
    try:
        out = {"profile": ctx.describe()}
    except ConfigError as e:
        if not ctx.explicit_data_path:
            raise
        ctx.config_or_default()
        out = {"profile": None, "profileHint": e.hint}
    try:
        snap = load_snapshot(ctx.data_path)
        out["snapshot"] = snap.describe(as_of=ctx.as_of, max_age_hours=args.max_age)
        out["snapshot"]["pulls"] = snap.data.get("pulls")
    except DataError as e:
        out["snapshot"] = {"path": ctx.data_path, "exists": False, "hint": e.hint}
    if ctx.warnings:
        out["warnings"] = ctx.warnings
    return out


def cmd_fetch(ctx, args):
    from .fetch import build_snapshot
    from .snapshot import load_snapshot
    out_path = args.out or ctx.data_path
    if args.max_age is not None and os.path.exists(out_path):
        try:
            existing = load_snapshot(out_path)
            age = existing.age_hours(ctx.as_of)
            if age is not None and age <= args.max_age and not existing.is_demo:
                return {"skipped": True, "reason": "snapshot is %.1fh old (max-age %.1fh)" % (age, args.max_age),
                        "snapshot": existing.describe(as_of=ctx.as_of, max_age_hours=args.max_age)}
        except DataError:
            pass
    tenants = ctx.select_tenants(_opt(args, "tenant"))
    snapshot = build_snapshot(ctx, tenants=tenants, max_pages=args.max_pages, page_size=args.page_size, quiet=ctx.quiet,
                              workers=args.workers)
    partial = len(tenants) != len(ctx.tenants)
    if partial and os.path.exists(out_path):
        # Merge a single-tenant refresh into the existing multi-tenant snapshot.
        try:
            existing = load_snapshot(out_path).data
        except DataError:
            existing = None
        if existing:
            codes = {t["code"] for t in tenants}
            for coll in ("technicians", "jobs", "businessUnits", "opportunities", "ar", "purchaseOrders", "campaigns",
                         "calls", "memberships"):
                snapshot[coll] = [r for r in existing.get(coll) or [] if r.get("loc") not in codes] + snapshot[coll]
            people = dict(existing.get("people") or {})
            people.update(snapshot["people"])
            snapshot["people"] = people
            for key in ("poTotals", "pulls"):
                merged = dict(existing.get(key) or {})
                merged.update(snapshot.get(key) or {})
                snapshot[key] = merged
            snapshot["tenants"] = [t for t in existing.get("tenants") or [] if t.get("code") not in codes] + snapshot["tenants"]
            snapshot["rawCallCount"] = sum(int(p.get("calls") or 0) for p in snapshot["pulls"].values())
            snapshot["estimateCount"] = sum(int(p.get("estimates") or 0) for p in snapshot["pulls"].values())
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=1)
    progress("Wrote %s" % out_path, ctx.quiet)
    return {"written": os.path.abspath(out_path), "generatedAt": snapshot["generatedAt"],
            "tenants": [t["code"] for t in snapshot["tenants"]], "partial": partial,
            "counts": {k: len(snapshot.get(k) or []) for k in ("jobs", "opportunities", "ar", "purchaseOrders", "calls",
                                                                "memberships", "technicians", "businessUnits", "campaigns")},
            "pulls": snapshot.get("pulls"), "next": "servicetitan metrics all  |  servicetitan render"}


def cmd_demo(ctx, args):
    from .demo import build_demo
    cfg = ctx.config_or_default()
    out_path = args.out or (ctx.data_path if ctx.source != "default" else "demo_data.json")
    op_start, _ = ctx.window_starts() if ctx.source != "default" else ("%d-01-01T00:00:00Z" % ctx.as_of.year, None)
    payload = build_demo(cfg, op_start, seed=args.seed, profile=cfg.get("profile") or "demo")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    done = [j for j in payload["jobs"] if not j["future"]]
    return {"written": os.path.abspath(out_path), "isDemo": True, "seed": args.seed,
            "tenants": [t["code"] for t in payload["tenants"]], "completedJobs": len(done),
            "completedRevenue": round(sum(j["revenue"] for j in done), 2), "opportunities": len(payload["opportunities"]),
            "next": "servicetitan metrics all --data %s" % out_path if ctx.source == "default" else "servicetitan metrics all"}


def cmd_check(ctx, args):
    from .snapshot import check_snapshot, load_snapshot, problems
    _settings(ctx)
    snap = load_snapshot(ctx.data_path, demo_fallback=True)
    results = check_snapshot(snap.data, [t["code"] for t in ctx.tenants] if ctx.source != "default" else snap.tenant_codes(),
                             demo=snap.is_demo, max_age_hours=args.max_age, as_of=ctx.as_of)
    bad = problems(results)
    out = {"ok": not bad, "snapshot": snap.describe(as_of=ctx.as_of, max_age_hours=args.max_age), "checks": results}
    if bad:
        raise ValidationError("%d check(s) failed" % len(bad), details=out)
    return out


def cmd_render(ctx, args):
    from .render import render_dashboard
    from .snapshot import load_snapshot
    cfg = _settings(ctx) if ctx.explicit_data_path else ctx.config_or_default()
    snap = load_snapshot(ctx.data_path if (ctx.source != "default" or ctx.explicit_data_path) else "demo_data.json", demo_fallback=True)
    settings = ctx.dashboard_settings() if ctx.source != "default" else {
        "company": cfg["company"], "locations": [{"code": t["code"], "label": t.get("label")} for t in cfg["tenants"]],
        "targets": cfg["targets"], "rules": cfg["rules"]}
    if snap.is_demo:
        settings["locations"] = [{"code": t.get("code"), "label": t.get("label")} for t in snap.data.get("tenants") or []] or settings["locations"]
    codes = [l["code"] for l in settings["locations"]]
    report = render_dashboard(snap, settings, args.out, brand_dir=args.brand_dir, template_path=args.template,
                              skip_checks=args.skip_checks, max_age_hours=args.max_age, as_of=ctx.as_of, expected_codes=codes,
                              full=args.full)
    return report


def cmd_duplicates(ctx, args):
    from .snapshot import find_duplicates, load_snapshot
    _settings(ctx)
    snap = load_snapshot(ctx.data_path, demo_fallback=True)
    groups = find_duplicates(snap.data)
    return {"snapshot": snap.describe(as_of=ctx.as_of), "possibleDuplicates": len(groups), "groups": groups,
            "note": "Confirm each is really one person before adding to personAliases; merging two different people "
                    "is worse than leaving them split. Best fixed inside ServiceTitan by merging the user records."}


def cmd_metrics(ctx, args):
    from .metrics import compute
    snap, q = _query(ctx, args)
    return compute(q, args.area, explain=args.explain)


def cmd_list(ctx, args):
    from .listing import list_records
    snap, q = _query(ctx, args, allow_all=True)
    status = "completed" if args.completed else ("booked" if args.booked else None)
    billed = True if args.billed else (False if args.no_charge else None)
    won = True if args.won else (False if args.open else None)
    direction = "inbound" if args.inbound else ("outbound" if args.outbound else None)
    active = True if args.active else (False if args.inactive else None)
    filters = {"status": status, "dept": args.dept, "billed": billed, "min_revenue": args.min_revenue, "tech": args.tech,
               "customer": args.customer, "job_number": args.job_number, "campaign": args.campaign, "won": won,
               "rep": args.rep, "date_field": args.date_field, "min_value": args.min_value,
               "min_days_past_due": args.min_days_past_due, "bucket": args.bucket, "min_balance": args.min_balance,
               "vendor": args.vendor, "po_status": args.po_status, "direction": direction, "agent": args.agent,
               "membership_status": args.membership_status, "plan": args.plan, "renews_within": args.renews_within,
               "name": args.name, "category": args.category, "active": active}
    sort = args.sort
    if sort and args.desc and not sort.startswith("-"):
        sort = "-" + sort
    if args.kind != "opportunities":
        filters.pop("date_field", None)
    result = list_records(q, args.kind, filters, limit=args.limit, sort=sort, fields=args.fields,
                          count_only=args.count, use_range=q.use_range)
    if args.fields and result.get("records"):
        unknown = [f for f in args.fields.split(",") if f.strip() and all(r.get(f.strip()) is None for r in result["records"])]
        if unknown:
            progress("warning: field(s) %s are empty for every record; check the names with `servicetitan list %s --limit 1`"
                     % (", ".join(unknown), args.kind), ctx.quiet)
    return result


def _api_builtin_parser(name):
    p = argparse.ArgumentParser(prog="servicetitan api %s" % name, formatter_class=argparse.RawDescriptionHelpFormatter)
    _common(p)
    if name == "get":
        p.description = ("GET any ServiceTitan path for one tenant. Paths may be the documented form "
                         "(/jpm/v2/tenant/{tenant}/jobs), shorthand (jpm/v2/jobs) or a full URL on this environment's API "
                         "host; requests never go anywhere else. Prefer the command tree (`servicetitan api jpm jobs list`) "
                         "when the operation is catalogued.")
        p.add_argument("path")
        p.add_argument("--param", action="append", default=[], metavar="KEY=VALUE", help="query parameter (repeatable)")
        p.add_argument("--all", action="store_true", help="follow pages until hasMore is false")
        p.add_argument("--max-pages", type=int, default=20, help="page cap with --all (default %(default)s)")
        p.add_argument("--page-size", type=int, help="pageSize (default 200 with --all)")
        p.add_argument("--export", action="store_true", help="treat as an export endpoint: follow continueFrom")
        p.add_argument("--from", dest="from_token", metavar="TOKEN", help="export continuation token or start date")
        p.add_argument("--include-recent", action="store_true", help="export: includeRecentChanges=true")
        p.add_argument("--max-batches", type=int, default=10, help="export batch cap (default %(default)s)")
        p.add_argument("--raw", action="store_true", help="print the response body as is, without the envelope")
    elif name == "endpoints":
        p.description = "Search the catalogue of ServiceTitan operations (GET only unless --all-methods)."
        p.add_argument("--search", metavar="TEXT", help="words that must all appear in path/operation/summary/params")
        p.add_argument("--module", help="e.g. jpm, crm, accounting, settings, telecom (aliases: sales, jobs, calls ...)")
        p.add_argument("--exports", action="store_true", help="only bulk export feeds")
        p.add_argument("--all-methods", action="store_true", help="include write operations")
        p.add_argument("--fields", action="store_true", help="include response record fields")
        p.add_argument("--limit", type=int, default=50)
    elif name == "modules":
        p.description = "List API modules with operation counts and API versions."
    elif name == "audit":
        p.description = "Recent write attempts (dry runs, successes and failures) from the audit log."
        p.add_argument("--limit", type=int, default=50)
    elif name == "catalogue":
        p.description = ("The operation catalogue is built from ServiceTitan's OpenAPI documents. The package ships one; "
                         "`refresh` downloads the current documents from the developer portal into the config directory "
                         "(which then takes precedence) and reports what changed; `reset` returns to the packaged copy.")
        p.add_argument("action", choices=("status", "refresh", "reset"))
        p.add_argument("--dry-run", action="store_true", help="refresh: report the differences without saving")
    return p


def cmd_api(ctx, args):
    from . import apitree, endpoints as ep
    rest = list(args.rest or [])
    fmt = _opt(args, "format") or os.environ.get("SERVICETITAN_FORMAT") or "json"
    if rest and rest[0] == "--":
        rest = rest[1:]
    if not rest or rest[0] in ("-h", "--help"):
        data = apitree.overview()
        if rest:
            sys.stdout.write(apitree.overview_text(data))
            return None
        return data
    head = rest[0]
    if head in ("get", "endpoints", "modules", "catalogue", "audit"):
        sub = _api_builtin_parser(head).parse_args(rest[1:])
        for k, v in vars(sub).items():
            if k not in vars(args) or v not in (None, False):
                setattr(args, k, v)
        if head == "modules":
            return {"source": "catalogue", "catalogue": {k: v for k, v in ep.info().items() if k != "modules"},
                    "modules": ep.modules()}
        if head == "audit":
            from . import writes
            prof = None
            try:
                prof = ctx.profile_name
            except ConfigError:
                pass
            rows = writes.read(limit=sub.limit, profile=prof)
            return {"source": "audit", "path": writes.audit_path(), "profile": prof, "count": len(rows), "entries": rows}
        if head == "catalogue":
            from . import catalogue as cat
            if sub.action == "status":
                packaged = ep.packaged()
                active = ep.info()
                out = {"source": "catalogue", "active": active,
                       "packaged": {"generatedAt": packaged.get("generatedAt"), "operations": packaged.get("count"),
                                    "reads": packaged.get("readCount")},
                       "localPath": cat.local_path(), "localPresent": os.path.exists(cat.local_path())}
                if active["origin"] == "local":
                    out["diffFromPackaged"] = cat.diff(packaged, ep.catalogue())["summary"]
                out["hint"] = "servicetitan api catalogue refresh [--dry-run] downloads the current OpenAPI documents; reset returns to the packaged copy"
                return out
            if sub.action == "reset":
                removed = cat.remove_local()
                ep.reload()
                return {"source": "catalogue", "removed": removed, "active": ep.info()}
            progress("downloading the current OpenAPI documents from %s ..." % cat.PORTAL, ctx.quiet)
            fresh = cat.build_from_portal(progress=lambda m: progress("   " + m, ctx.quiet))
            delta = cat.diff(ep.catalogue(), fresh)
            out = {"source": "catalogue", "dryRun": bool(sub.dry_run), "generatedAt": fresh["generatedAt"],
                   "operations": fresh["count"], "reads": fresh["readCount"], "diff": delta}
            if not sub.dry_run:
                out["saved"] = cat.save_local(fresh)
                ep.reload()
                out["active"] = {k: v for k, v in ep.info().items() if k != "modules"}
            return out
        if head == "endpoints":
            rows = ep.search(sub.search, sub.module, exports_only=sub.exports, include_fields=sub.fields, limit=sub.limit,
                             methods=None if sub.all_methods else ("GET",))
            return {"source": "catalogue", "count": len(rows), "endpoints": rows,
                    "usage": "run one with its `command`, add --describe or --help for its parameters"}
        params = {}
        for kv in sub.param:
            if "=" not in kv:
                raise UsageError("--param expects KEY=VALUE, got %r" % kv)
            k, v = kv.split("=", 1)
            params[k.strip()] = v
        tenant, client = _single_tenant_client(ctx, args, page_size=sub.page_size)
        url = client.resolve_url(sub.path)
        env = {"source": "live", "tenant": tenant["code"], "method": "GET", "url": url, "params": params}
        if sub.export:
            batches, items, token, has_more = 0, [], sub.from_token, False
            for batch, token, has_more in client.export(sub.path, from_token=sub.from_token,
                                                        include_recent_changes=sub.include_recent, max_batches=sub.max_batches):
                batches += 1
                items.extend(batch)
            env.update({"batches": batches, "count": len(items), "continueFrom": token, "hasMore": has_more, "data": items})
            return env
        if sub.all:
            items, pages = [], 0
            for page, batch, _ in client.get_pages(sub.path, params, max_pages=sub.max_pages, page_size=sub.page_size):
                pages = page
                items.extend(batch)
            env.update({"pages": pages, "count": len(items), "truncated": client.last_truncated, "data": items})
            return env
        body = client.get(sub.path, params)
        if sub.raw:
            return body
        if isinstance(body, dict) and "data" in body:
            env.update({"page": body.get("page"), "pageSize": body.get("pageSize"), "hasMore": body.get("hasMore"),
                        "totalCount": body.get("totalCount"), "count": len(body.get("data") or []), "data": body.get("data")})
        else:
            env["body"] = body
        return env

    # ---- the generated tree: module > resource > operation
    module = ep.resolve_module(head)
    if len(rest) == 1 or rest[1] in ("-h", "--help"):
        data = apitree.module_overview(module)
        if len(rest) > 1:
            sys.stdout.write(apitree.overview_text(data))
            return None
        return data
    resource = ep.resolve_resource(module, rest[1])
    if len(rest) == 2 or rest[2] in ("-h", "--help"):
        data = apitree.resource_overview(module, resource)
        if len(rest) > 2:
            sys.stdout.write(apitree.overview_text(data))
            return None
        return data
    op = ep.resolve_operation(module, resource, rest[2])
    if "--describe" in rest[3:]:      # the contract needs no path parameters, credentials or tenant
        return ep.describe(op)
    parser = apitree.build_parser(op, add_common=_common)
    ns = parser.parse_args(rest[3:])
    for k, v in vars(ns).items():   # global options given after the operation (e.g. --tenant, --format)
        if k in ("profile", "tenant", "format", "compact", "quiet", "as_of", "data", "config", "env_file", "secrets") and v not in (None, False):
            setattr(args, k, v)
    if ns.describe:
        return ep.describe(op)
    return apitree.execute(op, ns, ctx, client_factory=None, tenant_code=_opt(args, "tenant"))


def cmd_palette(ctx, args):
    from .palette import validate_palette
    try:
        report = validate_palette(args.colours.split(","), mode=args.mode, surface=args.surface, pairs=args.pairs)
    except ValueError as e:
        raise UsageError(str(e))
    if not report["ok"]:
        raise ValidationError("Palette failed accessibility checks", details=report)
    return report


def cmd_schema(ctx, args, parser=None):
    from .schema import build_schema
    return build_schema(parser or build_parser())


def cmd_init(ctx, args):
    steps = []
    try:
        cfg = ctx.config
        steps.append({"done": True, "step": "profile", "detail": "using profile %r from %s" % (cfg.get("profile"), ctx.source)})
        if not ctx.tenants:
            steps.append({"done": False, "step": "tenant", "detail": "add a tenant: servicetitan tenant add %s --code MAIN --tenant-id ... --client-id ... --client-secret-stdin --app-key-stdin" % cfg.get("profile")})
        else:
            for t in ctx.tenants:
                try:
                    ctx.credentials_for(t)
                    steps.append({"done": True, "step": "credentials:%s" % t["code"], "detail": "all four values present"})
                except CliError as e:
                    steps.append({"done": False, "step": "credentials:%s" % t["code"], "detail": e.message, "hint": e.hint})
    except ConfigError as e:
        steps.append({"done": False, "step": "profile", "detail": e.message, "hint": e.hint})
    if os.path.exists(ctx.data_path if steps and steps[0]["done"] else "live_data.json"):
        steps.append({"done": True, "step": "snapshot", "detail": "snapshot present; run `servicetitan status`"})
    else:
        steps.append({"done": False, "step": "snapshot", "detail": "run `servicetitan auth check`, then `servicetitan fetch`"})
    return {
        "steps": steps,
        "howToGetCredentials": [
            "1. Sign in at https://developer.servicetitan.io with your ServiceTitan login (choose the production environment).",
            "2. Register an app: organization name/website/email, app name (e.g. 'Claude'), description, then under API scopes tick every READ scope you need (Tenant APIs).",
            "3. Add your tenant id(s) to the app. Saving generates the APP KEY (shown on the app page).",
            "4. In ServiceTitan: Settings > Integrations > API Application Access > Connect New App, pick the app, complete the required dropdowns, Allow Access.",
            "5. Still there, generate the CLIENT ID and CLIENT SECRET for the app (the secret is shown once). The TENANT ID is on the same page.",
            "6. servicetitan profile add <name> --company ... ; servicetitan tenant add <name> --code MAIN --tenant-id <id> --client-id <id> (secrets via stdin, env or prompt).",
            "7. servicetitan auth check ; servicetitan fetch ; servicetitan metrics all",
        ],
    }


HANDLERS = {"profile": cmd_profile, "tenant": cmd_tenant, "auth": cmd_auth, "status": cmd_status, "fetch": cmd_fetch,
            "demo": cmd_demo, "check": cmd_check, "render": cmd_render, "duplicates": cmd_duplicates,
            "metrics": cmd_metrics, "list": cmd_list, "api": cmd_api, "palette": cmd_palette, "schema": cmd_schema,
            "init": cmd_init}


# ---------------------------------------------------------------- main
def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    fmt = _opt(args, "format") or os.environ.get("SERVICETITAN_FORMAT") or "json"
    compact = bool(_opt(args, "compact"))
    quiet = bool(_opt(args, "quiet"))
    if not args.command:
        parser.print_help()
        return 2
    try:
        ctx = Context(profile=_opt(args, "profile"), config_path=_opt(args, "config"), env_file=_opt(args, "env_file"),
                      data_path=_opt(args, "data"), as_of=_opt(args, "as_of"), secrets_backend=_opt(args, "secrets"),
                      quiet=quiet)
        handler = HANDLERS[args.command]
        result = handler(ctx, args, parser) if args.command == "schema" else handler(ctx, args)
        for w in ctx.warnings:
            progress("warning: %s" % w, quiet)
        if result is None:
            return 0
        fmt = _opt(args, "format") or os.environ.get("SERVICETITAN_FORMAT") or "json"
        compact = bool(_opt(args, "compact"))
        if args.command in ("tenant", "profile") and ctx.secrets_selected() and ctx.secrets.fallback_reason:
            progress("note: secrets are kept in %s because the OS keyring is unavailable (%s)"
                     % (ctx.secrets.detail, ctx.secrets.fallback_reason), quiet)
        emit(result, fmt=fmt, pretty=not compact)
        return 0
    except CliError as e:
        emit_error(e.to_dict(), fmt=fmt)
        return e.exit_code
    except KeyboardInterrupt:
        emit_error({"error": {"code": "INTERRUPTED", "exit": 130, "message": "interrupted"}}, fmt=fmt)
        return 130
    except Exception as e:  # pragma: no cover - last resort
        if os.environ.get("SERVICETITAN_DEBUG"):
            raise
        emit_error({"error": {"code": "INTERNAL", "exit": 1, "message": "%s: %s" % (type(e).__name__, e),
                              "hint": "Set SERVICETITAN_DEBUG=1 for a traceback."}}, fmt=fmt)
        return 1


if __name__ == "__main__":
    sys.exit(main())
