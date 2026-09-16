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
import re

from . import endpoints
from .errors import UsageError
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
    if op.get("kind"):
        extra.append("response: %s" % op["kind"])
    if op.get("fields"):
        extra.append("record fields: %s" % ", ".join(op["fields"][:40]) + (" ..." if len(op["fields"]) > 40 else ""))
    if extra:
        desc.append("\n".join(extra))
    if op["method"] != "GET":
        desc.append("WRITE OPERATION: listed for discovery only; this read-only tool never executes it.")
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
    if add_common:
        add_common(parser)   # last, so the operation's own parameters lead the help text
    parser.set_defaults(_query_names=query_names)
    return parser


def _trim(records, fields):
    if not fields:
        return records
    want = [f.strip() for f in fields.split(",") if f.strip()]
    return [{k: r.get(k) for k in want} if isinstance(r, dict) else r for r in records]


def execute(op, ns, ctx, client_factory, tenant_code=None):
    """Run a GET operation for one tenant. Returns the result envelope."""
    if op["method"] != "GET":
        raise UsageError("%s %s is a write operation; this tool is read-only and only lists it for discovery."
                         % (op["method"], op["path"]),
                         hint="Use `--describe` to see its request body. Reads for this resource: %s" % ", ".join(
                             o["operation"] for o in endpoints.operations(op["module"], op["resource"]) if o["executable"]))
    from .api import ServiceTitanClient
    tenants = ctx.select_tenants(tenant_code)
    if len(tenants) != 1:
        raise UsageError("Profile %r has %d tenants; pass --tenant CODE" % (ctx.profile_name, len(tenants)),
                         hint="Codes: %s" % ", ".join(ctx.tenant_codes()))
    creds = ctx.credentials_for(tenants[0])
    client = (client_factory or ServiceTitanClient)(creds, progress=(lambda m: progress(m, ctx.quiet)))
    path = op["path"]
    for p in op.get("params", []):
        if p["in"] == "path":
            path = path.replace("{%s}" % p["name"], str(getattr(ns, p["name"])))
    params = {}
    for dest, original in (getattr(ns, "_query_names", None) or {}).items():
        val = getattr(ns, dest, None)
        if val is not None:
            params[original] = val
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
            "note": "GET operations run against ServiceTitan; write operations are listed for discovery only. "
                    "Add --help at any level for a readable version, --describe on an operation for its contract.",
            "builtins": {"get": "GET any path directly", "endpoints": "search operations by words",
                         "modules": "module list"},
            "modules": endpoints.modules()}


def module_overview(module):
    meta = next(m for m in endpoints.catalogue()["modules"] if m["module"] == module)
    return {"source": "catalogue", "module": module, "title": meta.get("title"), "description": meta.get("description"),
            "aliases": meta.get("aliases") or [], "resources": endpoints.resources(module),
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
                lines.append("  %-30s writes (disabled): %s" % ("", ", ".join(r["writes"])))
    elif "operations" in data:
        lines.append("%s %s: operations (servicetitan api %s %s <operation> --help):\n" % (data["module"], data["resource"], data["module"], data["resource"]))
        for o in data["operations"]:
            flag = "" if o["executable"] else "  (write, disabled)"
            lines.append("  %-28s %-6s %s%s" % (o["operation"], o["method"], o["path"], flag))
            if o.get("summary"):
                lines.append("  %-28s        %s" % ("", o["summary"]))
    return "\n".join(lines) + "\n"
