"""Dynamic commands for every ServiceTitan operation in the catalogue.

``servicetitan api <module> <resource> <operation> [ARGS] [--options]`` builds
an argparse parser for that one operation from its OpenAPI parameters: path
parameters become positional arguments, query parameters become ``--kebab``
options with the spec's descriptions, types and allowed values. Paged
operations gain ``--all``/``--max-pages``/``--count``; export feeds gain
``--max-batches``. ``--describe`` prints the operation's contract instead of
calling it, and ``--help`` renders the same for people.
"""
import argparse
import json
import re

from . import endpoints, writes
from .errors import AuthError, ConfigError, UsageError
from .output import progress

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def kebab(name):
    return _CAMEL_RE.sub("-", name.replace(".", "-").replace("_", "-")).lower()


def _enum_type(values):
    canon = {v.lower(): v for v in values}

    def convert(text):
        if text in values:
            return text
        if text.lower() in canon:
            return canon[text.lower()]
        raise argparse.ArgumentTypeError("choose from %s" % ", ".join(values))
    convert.__name__ = "enum"
    return convert


def _param_help(p):
    bits = [p.get("description") or ""]
    meta = p.get("type") or "string"
    if p.get("format"):
        meta += "/" + p["format"]
    if p.get("enum") and not (p.get("description") or "").lower().startswith("values"):
        meta = "one of: " + ", ".join(p["enum"])
    bits.append("(%s)" % meta)
    return " ".join(b for b in bits if b).replace("%", "%%")


def _metavar(p):
    if p.get("enum"):
        return "VALUE"
    if p.get("type") == "integer":
        return "N"
    if p.get("type") == "number":
        return "NUMBER"
    if p.get("type") == "boolean":
        return "true|false"
    if p.get("format") == "date-time":
        return "ISO-8601"
    if p.get("format") == "date":
        return "YYYY-MM-DD"
    return "TEXT"


def build_parser(op, add_common=None, prog=None):
    desc = ["%s %s" % (op["method"], op["path"])]
    if op.get("summary"):
        desc.append(op["summary"])
    if op.get("description"):
        desc.append(op["description"])
    extra = []
    if op.get("scope"):
        extra.append("scope: %s" % op["scope"])
    if op.get("kind") and op["kind"] != "unknown":
        extra.append("response: %s" % op["kind"])
    if op.get("fields"):
        extra.append("record fields: %s" % ", ".join(op["fields"][:40]) + (" ..." if len(op["fields"]) > 40 else ""))
    if extra:
        desc.append("\n".join(extra))
    if op.get("deprecated"):
        desc.append("DEPRECATED by ServiceTitan; prefer the newer version of this operation (see --describe replacedBy).")
    if op["method"] != "GET":
        desc.append("WRITE OPERATION. Runs only with --write, when the profile's write allowlist permits it "
                    "(`servicetitan profile set <profile> --allow-writes ...`) and the token carries scope %s. "
                    "--dry-run shows the exact request; every executed write is recorded in the audit log."
                    % (op.get("scope") or "(unknown)"))
        if op.get("body"):
            req = op["body"].get("required") or []
            desc.append("request body fields: " + ", ".join(
                "%s%s (%s)" % (k, "*" if k in req else "", t) for k, t in op["body"].get("properties", {}).items())
                        + ("   (* required)" if req else ""))
    parser = argparse.ArgumentParser(prog=prog or "servicetitan api %s %s %s" % (op["module"], op["resource"], op["operation"]),
                                     description="\n\n".join(desc), formatter_class=argparse.RawDescriptionHelpFormatter)
    query_names = {}
    for p in op.get("params", []):
        if p["in"] == "path":
            parser.add_argument(p["name"], metavar=p["name"].upper(), help=_param_help(p) or "path parameter")
    g = parser.add_argument_group("query parameters (from the ServiceTitan OpenAPI document)")
    for p in op.get("params", []):
        if p["in"] != "query":
            continue
        flag = "--" + kebab(p["name"])
        dest = "q_" + re.sub(r"[^a-z0-9]", "_", kebab(p["name"]))
        query_names[dest] = p["name"]
        kw = {"dest": dest, "help": _param_help(p), "metavar": _metavar(p)}
        if p.get("enum"):
            kw["type"] = _enum_type(p["enum"])
        elif p.get("type") == "integer":
            kw["type"] = int
        elif p.get("type") == "boolean":
            kw["type"] = _enum_type(["true", "false"])
        if p.get("required"):
            kw["required"] = True
        g.add_argument(flag, **kw)
    c = parser.add_argument_group("output")
    if op.get("kind") == "paged":
        c.add_argument("--all", action="store_true", help="follow pages until hasMore is false")
        c.add_argument("--max-pages", type=int, default=20, help="page cap with --all (default %(default)s)")
        c.add_argument("--count", action="store_true", help="return only totalCount (sends includeTotal=true, pageSize=1)")
    if op.get("export"):
        c.add_argument("--max-batches", type=int, default=10, help="export batch cap (default %(default)s)")
    c.add_argument("--fields", metavar="A,B,C", help="keep only these fields on each record")
    c.add_argument("--raw", action="store_true", help="print the response body as is, without the envelope")
    c.add_argument("--describe", action="store_true", help="print the operation's parameters and fields instead of calling it")
    if op["method"] != "GET":
        w = parser.add_argument_group("write controls")
        w.add_argument("--write", action="store_true", help="required: confirm that this invocation is meant to change ServiceTitan")
        w.add_argument("--yes", action="store_true", help="skip the interactive confirmation (only asked when stdin is a terminal)")
        w.add_argument("--dry-run", action="store_true", help="show the request that would be sent, send nothing")
        w.add_argument("--body", metavar="JSON|@FILE", help="request body as JSON text or @path to a JSON file")
        w.add_argument("--body-stdin", action="store_true", help="read the JSON body from stdin")
        w.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                       help="set one body field (JSON value when it parses, else text); repeatable")
    if add_common:
        add_common(parser)   # last, so the operation's own parameters lead the help text
    parser.set_defaults(_query_names=query_names)
    return parser


def _trim(records, fields):
    if not fields:
        return records
    want = [f.strip() for f in fields.split(",") if f.strip()]
    return [{k: r.get(k) for k in want} if isinstance(r, dict) else r for r in records]


def _fill_path(op, ns):
    path = op["path"]
    for p in op.get("params", []):
        if p["in"] == "path":
            path = path.replace("{%s}" % p["name"], str(getattr(ns, p["name"])))
    return path


def _query_params(ns):
    params = {}
    for dest, original in (getattr(ns, "_query_names", None) or {}).items():
        val = getattr(ns, dest, None)
        if val is not None:
            params[original] = val
    return params


def require_write_flag(op, ns, ctx):
    """Gate 1: this invocation carries --write. Checked before anything is parsed or sent."""
    if getattr(ns, "write", False):
        return
    op_key = "%s %s %s" % (op["module"], op["resource"], op["operation"])
    raise UsageError("%s is a write operation (%s %s); it runs only with --write." % (op_key, op["method"], op["path"]),
                     hint="Add --write (and --dry-run first to see the request). The profile must also allow it: "
                          "servicetitan profile set %s --allow-writes \"%s/%s\" ; reads for this resource: %s"
                          % (ctx.profile_name, op["module"], op["resource"],
                             ", ".join(o["operation"] for o in endpoints.operations(op["module"], op["resource"]) if o["method"] == "GET")))


def authorize_write(op, ns, ctx, tenant, client):
    """Run the three gates; return a WriteAuthorization or raise."""
    from .api import WriteAuthorization
    op_key = "%s %s %s" % (op["module"], op["resource"], op["operation"])
    require_write_flag(op, ns, ctx)
    pattern = ctx.write_allowed(op["module"], op["resource"], op["operation"])
    if not pattern:
        raise ConfigError("Writes are not allowed for %s on profile %r" % (op_key, ctx.profile_name), code="WRITES_DISABLED",
                          hint="Allow it with: servicetitan profile set %s --allow-writes \"%s/%s/%s\" (patterns: module, "
                               "module/resource, module/resource/operation, or *; comma-separated). CI: "
                               "SERVICETITAN_ALLOW_WRITES. Current allowlist: %s"
                               % (ctx.profile_name, op["module"], op["resource"], op["operation"],
                                  ", ".join(ctx.write_patterns()) or "(empty)"))
    client.token()
    granted = client.granted_scopes
    scope = op.get("scope")
    if granted is not None and scope and scope not in granted:
        raise AuthError("The token for tenant %s was not granted scope %s, which %s needs" % (tenant["code"], scope, op_key),
                        code="SCOPE",
                        hint="Add the write scope to the app in the developer portal and have the tenant admin approve the "
                             "new app version (Settings > Integrations > API Application Access > Upgrade to New Version). "
                             "Granted: %d scope(s); read scopes never imply write." % len(granted))
    return WriteAuthorization(ctx.profile_name, tenant["code"], op_key, scope,
                              {"flag": "--write", "allowlist": pattern,
                               "scope": "granted" if (granted is not None and scope) else "not verifiable from token"})


def execute_write(op, ns, ctx, client_factory, tenant_code=None):
    from .api import ServiceTitanClient
    if not getattr(ns, "dry_run", False):
        require_write_flag(op, ns, ctx)   # first, so a missing flag is the first thing an agent hears about
    tenants = ctx.select_tenants(tenant_code)
    if len(tenants) != 1:
        raise UsageError("Profile %r has %d tenants; pass --tenant CODE" % (ctx.profile_name, len(tenants)),
                         hint="Codes: %s" % ", ".join(ctx.tenant_codes()))
    tenant = tenants[0]
    body = writes.build_body(op, body_arg=getattr(ns, "body", None), body_stdin=getattr(ns, "body_stdin", False),
                             sets=getattr(ns, "set", None))
    path = _fill_path(op, ns)
    params = _query_params(ns)
    creds = ctx.credentials_for(tenant)
    client = (client_factory or ServiceTitanClient)(creds, progress=(lambda m: progress(m, ctx.quiet)))
    url = client.resolve_url(path)
    op_key = "%s %s %s" % (op["module"], op["resource"], op["operation"])
    preview = {"source": "live", "write": True, "tenant": tenant["code"], "operation": op_key, "method": op["method"],
               "url": url, "params": params, "body": body, "scope": op.get("scope")}
    if getattr(ns, "dry_run", False):
        preview["dryRun"] = True
        preview["allowedBy"] = ctx.write_allowed(op["module"], op["resource"], op["operation"])
        preview["wouldRun"] = bool(getattr(ns, "write", False) and preview["allowedBy"])
        writes.record({"profile": ctx.profile_name, "tenant": tenant["code"], "operation": op_key, "method": op["method"],
                       "url": url, "params": params, "body": body, "dryRun": True})
        return preview
    authorization = authorize_write(op, ns, ctx, tenant, client)
    if not writes.confirm(preview, tenant["code"], assume_yes=getattr(ns, "yes", False)):
        raise UsageError("Write aborted at the confirmation prompt", hint="Pass --yes to skip the prompt.")
    entry = {"profile": ctx.profile_name, "tenant": tenant["code"], "environment": creds.environment, "operation": op_key,
             "method": op["method"], "url": url, "params": params, "body": body, "dryRun": False,
             "gates": authorization.gates}
    try:
        result = client.write(op["method"], path, params=params, body=body, authorization=authorization)
    except Exception as e:  # record the failure, then re-raise unchanged
        entry["error"] = getattr(e, "message", str(e))
        entry["status"] = getattr(e, "status", None)
        writes.record(entry)
        raise
    entry["status"] = result["status"]
    resp = result.get("body")
    if isinstance(resp, dict) and resp.get("id") is not None:
        entry["resultId"] = resp.get("id")
    entry["audit"] = writes.record(entry)
    preview.update({"status": result["status"], "data": resp, "gates": authorization.gates, "audit": entry["audit"]})
    return preview


def execute(op, ns, ctx, client_factory, tenant_code=None):
    """Run one operation for one tenant. Returns the result envelope."""
    if op["method"] != "GET":
        return execute_write(op, ns, ctx, client_factory, tenant_code)
    from .api import ServiceTitanClient
    tenants = ctx.select_tenants(tenant_code)
    if len(tenants) != 1:
        raise UsageError("Profile %r has %d tenants; pass --tenant CODE" % (ctx.profile_name, len(tenants)),
                         hint="Codes: %s" % ", ".join(ctx.tenant_codes()))
    creds = ctx.credentials_for(tenants[0])
    client = (client_factory or ServiceTitanClient)(creds, progress=(lambda m: progress(m, ctx.quiet)))
    path = _fill_path(op, ns)
    params = _query_params(ns)
    url = client.resolve_url(path)
    env = {"source": "live", "tenant": tenants[0]["code"], "operation": "%s %s %s" % (op["module"], op["resource"], op["operation"]),
           "method": "GET", "url": url, "params": dict(params)}
    if op.get("export"):
        batches, items, token, has_more = 0, [], params.pop("from", None), False
        include_recent = str(params.pop("includeRecentChanges", "false")).lower() == "true"
        for batch, token, has_more in client.export(path, from_token=token, include_recent_changes=include_recent,
                                                    max_batches=getattr(ns, "max_batches", 10)):
            batches += 1
            items.extend(batch)
        env.update({"batches": batches, "count": len(items), "continueFrom": token, "hasMore": has_more,
                    "data": _trim(items, getattr(ns, "fields", None))})
        return env
    if op.get("kind") == "paged":
        if getattr(ns, "count", False):
            body = client.get(path, dict(params, includeTotal="true", pageSize=1, page=1)) or {}
            env.update({"totalCount": body.get("totalCount"), "hasMore": body.get("hasMore")})
            return env
        if getattr(ns, "all", False):
            items, pages = [], 0
            for page, batch, _ in client.get_pages(path, params, max_pages=getattr(ns, "max_pages", 20),
                                                   page_size=params.get("pageSize")):
                pages = page
                items.extend(batch)
            env.update({"pages": pages, "count": len(items), "truncated": client.last_truncated,
                        "data": _trim(items, getattr(ns, "fields", None))})
            return env
    body = client.get(path, params)
    if getattr(ns, "raw", False):
        return body
    if isinstance(body, dict) and isinstance(body.get("data"), list):
        env.update({"page": body.get("page"), "pageSize": body.get("pageSize"), "hasMore": body.get("hasMore"),
                    "totalCount": body.get("totalCount"), "count": len(body["data"]),
                    "data": _trim(body["data"], getattr(ns, "fields", None))})
    elif isinstance(body, list):
        env.update({"count": len(body), "data": _trim(body, getattr(ns, "fields", None))})
    else:
        env["data"] = body
    return env


# ---------------------------------------------------------------- overviews
def overview():
    return {"source": "catalogue", "usage": "servicetitan api <module> [<resource> [<operation> [ARGS] [--options]]]",
            "note": "Reads run directly. Writes run only with --write, when the profile's write allowlist permits the "
                    "operation and the token carries its :w scope; --dry-run previews, audit.jsonl records. "
                    "Add --help at any level for a readable version, --describe on an operation for its contract.",
            "builtins": {"get": "GET any path directly", "endpoints": "search operations by words",
                         "modules": "module list", "catalogue": "status | refresh | reset of the operation catalogue",
                         "audit": "recent write attempts"},
            "catalogue": {k: v for k, v in endpoints.info().items() if k != "modules"},
            "modules": endpoints.modules()}


def module_overview(module):
    meta = next(m for m in endpoints.catalogue()["modules"] if m["module"] == module)
    return {"source": "catalogue", "module": module, "title": meta.get("title"), "description": meta.get("description"),
            "aliases": meta.get("aliases") or [], "versions": meta.get("versions") or [],
            "resources": endpoints.resources(module),
            "next": "servicetitan api %s <resource>" % module}


def resource_overview(module, resource):
    return {"source": "catalogue", "module": module, "resource": resource,
            "operations": endpoints.operations(module, resource),
            "next": "servicetitan api %s %s <operation> --describe" % (module, resource)}


def overview_text(data):
    """Human rendering of the overview levels for --help."""
    lines = []
    if "modules" in data:
        lines.append("ServiceTitan API modules (servicetitan api <module>):\n")
        for m in data["modules"]:
            alias = (" [%s]" % ", ".join(m["aliases"])) if m.get("aliases") else ""
            lines.append("  %-22s %-28s %3d reads %3d writes  %s" % (m["module"] + alias, m.get("title") or "", m["reads"], m["writes"],
                                                                     (m.get("description") or "")[:70]))
        lines.append("\nbuilt-ins: get <path>, endpoints --search WORDS, modules")
    elif "resources" in data:
        lines.append("%s (%s): resources (servicetitan api %s <resource>):\n" % (data["module"], data.get("title"), data["module"]))
        for r in data["resources"]:
            lines.append("  %-30s reads: %s" % (r["resource"], ", ".join(r["reads"]) or "-"))
            if r["writes"]:
                lines.append("  %-30s writes (gated): %s" % ("", ", ".join(r["writes"])))
    elif "operations" in data:
        lines.append("%s %s: operations (servicetitan api %s %s <operation> --help):\n" % (data["module"], data["resource"], data["module"], data["resource"]))
        for o in data["operations"]:
            flag = "" if o["method"] == "GET" else "  (write: needs --write, allowlist, scope)"
            if o.get("deprecated"):
                flag += "  (deprecated)"
            lines.append("  %-28s %-6s %s%s" % (o["operation"], o["method"], o["path"], flag))
            if o.get("summary"):
                lines.append("  %-28s        %s" % ("", o["summary"]))
    return "\n".join(lines) + "\n"
