"""Machine-readable description of the CLI, for agents to read once."""
import argparse

from . import __version__
from .errors import EXIT_CODES
from .listing import KINDS
from .metrics import AREAS, DEFINITIONS
from .dates import RANGE_KINDS


def _actions(parser):
    out = []
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction) or a.dest == "help":
            continue
        if a.default is argparse.SUPPRESS and not a.option_strings:
            continue
        item = {"name": a.dest, "flags": list(a.option_strings) or None,
                "help": (a.help or "").replace("%(default)s", str(a.default)) if a.help != argparse.SUPPRESS else None,
                "required": bool(getattr(a, "required", False))}
        if a.choices:
            item["choices"] = list(a.choices)
        if a.default not in (None, argparse.SUPPRESS, False):
            item["default"] = a.default
        if a.nargs in ("*", "+"):
            item["repeatable"] = True
        if getattr(a, "nargs", None) == 0 or isinstance(a, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
            item["type"] = "flag"
        elif a.type is int:
            item["type"] = "integer"
        elif a.type is float:
            item["type"] = "number"
        else:
            item["type"] = "string"
        out.append(item)
    return out


def _walk(parser, path):
    node = {"command": " ".join(path), "description": (parser.description or "").strip(), "arguments": _actions(parser)}
    subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if subs:
        node["subcommands"] = []
        seen = set()
        for sub in subs:
            for name, sp in sub.choices.items():
                if sp in seen:
                    continue
                seen.add(sp)
                node["subcommands"].append(_walk(sp, path + [name]))
    return node


SNAPSHOT_RECORDS = {
    "jobs": "One per completed job in the window (date=completedOn) or booked job with a future appointment (date=next appointment, future=true). Fields: id, jobNumber, date, loc (tenant code), dept, tech, revenue, future, campaign, membershipSold, lowTicket, options, customer, businessUnit, jobType, noCharge, status.",
    "opportunities": "One per job's estimate presentation (estimates grouped by jobId). Fields: id, loc, dept, customer, city, jobNumber, title, presentedOn, wonOn, won, options, owner, value (sold options only), topOption.",
    "ar": "Open invoices with an invoice date and balance > 0. Fields: id, invoiceNumber, loc, customer, invoiceDate, dueDate, balance, total, businessUnit.",
    "purchaseOrders": "Purchase orders not Received/Canceled. Fields: id, number, loc, vendor, date, total, status.",
    "calls": "Call log rolled up per (date, agent, campaign, direction). Fields: date, loc, agentName, campaign, inbound, n, booked, abandoned, unbooked, excused, notLead (callType counts; outbound calls have no type).",
    "memberships": "Customer memberships. Fields: id, loc, status, type (plan), customer, from, to, soldBy.",
    "technicians": "Roster incl. inactive (historical jobs reference them). Fields: id, name, loc, dept, active.",
    "businessUnits": "Fields: id, loc, name, dept (None = excluded), active.",
    "campaigns": "Fields: id, loc, name, category, active.",
    "people": "Map of namespaced user id -> name for technicians and office employees.",
    "meta": "generatedAt (UTC), isDemo, profile, environment, timezone, tenants[], window{operationalStart, arStart}, pulls{code: stats}, poTotals{code: all-status PO count}, rawCallCount, estimateCount, cli{version}.",
}


def _api_tree():
    from . import endpoints
    doc = endpoints.catalogue()
    tree = []
    for m in doc["modules"]:
        res = {}
        for r in endpoints.resources(m["module"]):
            res[r["resource"]] = {"reads": r["reads"], "writes": r["writes"]}
        tree.append({"module": m["module"], "title": m.get("title"), "aliases": m.get("aliases") or [], "resources": res})
    return {"grammar": "servicetitan api <module> <resource> <operation> [PATH-ARGS] [--query-options] [--all|--count|--fields|--raw|--describe]",
            "note": "Reads execute directly; writes need --write plus the profile allowlist and the :w scope. `--describe` returns the operation's parameters, types, enums, response fields and (for writes) the request body shape.",
            "catalogue": {"origin": doc.get("_origin"), "generatedAt": doc.get("generatedAt"),
                          "refresh": "servicetitan api catalogue refresh (downloads ServiceTitan's current OpenAPI documents into the config dir; status/reset also available)"},
            "operations": doc["count"], "reads": doc["readCount"], "modules": tree}


def build_schema(parser):
    return {
        "name": "servicetitan",
        "version": __version__,
        "purpose": "ServiceTitan CLI for AI agents. Reads by default; writes exist for every documented operation but run only behind three explicit gates (see conventions.writes).",
        "conventions": {
            "stdout": "exactly one JSON document per invocation (pretty by default; --compact for one line). --format text renders the same structure for people; --format csv writes the result's main table (records, data, hits ...).",
            "stderr": "progress lines and JSON error envelopes {\"error\": {code, exit, message, hint, details}}.",
            "exitCodes": {str(k): v for k, v in EXIT_CODES.items()},
            "profiles": "A profile is one company with one or more ServiceTitan tenants. Select with --profile NAME or SERVICETITAN_PROFILE; the store's default profile is used otherwise. Legacy config.json + .env in the working directory and plain SERVICETITAN_* environment variables also work.",
            "tenants": "--tenant CODE narrows to one tenant; default is all tenants of the profile (the dashboard's 'Combined').",
            "snapshots": "fetch writes <config dir>/snapshots/<profile>.json; metrics, list, check, render, duplicates and status read it. Use --data PATH to point elsewhere. Every query response carries snapshot.generatedAt, ageHours and stale so you can decide whether to fetch first (or run `fetch --max-age 6`).",
            "source": "every response says where its data came from: 'snapshot' (metrics, list, brief), 'live' (api ..., auth check), 'catalogue' (api discovery), 'audit' (api audit), 'index' (index query/status) or 'results' (saved results).",
            "save": "--save on any command keeps its result under <config dir>/results/<profile>/ (owner-only JSON with the command line and a --save-label) and adds it to the local index when one exists, so what an agent found live can be searched later.",
            "setup": "servicetitan setup is the interactive first-run path for a person at a terminal (secrets typed into hidden prompts, connection tested before saving); it exits 2 without a terminal. Agents use profile add + tenant add with --client-secret-stdin/--app-key-stdin, and never ask a person for secrets. servicetitan init returns the checklist and `next`, the command to run now.",
            "index": "servicetitan index build|status|query|results: a local LlamaIndex (optional extra: pip install \"servicetitan-cli[index]\") over snapshot records, saved results, the API catalogue, metric definitions and docs. Embeddings are computed on this machine (fastembed ONNX by default; SERVICETITAN_EMBED or --embed selects huggingface:, ollama: or hash) and nothing leaves it. query is hybrid retrieval: vectors for meaning plus a BM25 index for exact tokens (job and invoice numbers, ids, dates, amounts), fused, with rare identifiers pinned to the top (`matched`); --mode vector|lexical uses one side (lexical loads no model and answers in well under a second), --since/--until/--name/--kind/--tenant narrow before ranking, --rerank cross re-scores with a local cross-encoder. Hits carry score, scores {vector, lexical, fused, rerank}, kind, id, tenant, text and a `next` command; the caller reasons over them.",
            "writes": "POST/PUT/PATCH/DELETE operations in the api tree run only when (1) the invocation carries --write, (2) the profile's write policy permits the operation: the default policy 'auto' allows whatever :w scopes the credential was granted (servicetitan auth check lists them), `servicetitan profile set NAME --allow-writes 'module/resource[/operation]'` narrows it to named operations, `--no-writes` turns it off, and SERVICETITAN_ALLOW_WRITES replaces it in CI ('none' = off), and (3) the token's granted scopes include the operation's :w scope, checked on every write. --dry-run shows the request without sending; --yes skips the terminal confirmation; every attempt is appended to <config dir>/audit.jsonl (servicetitan api audit). Writes are never retried on 5xx or network errors.",
            "ranges": {"kinds": list(RANGE_KINDS), "note": "Ranges end at --as-of (default now, in the profile timezone). custom needs --start and optionally --end (YYYY-MM-DD)."},
            "ids": "Record ids are namespaced by tenant code ('MAIN:5510') because separate ServiceTitan tenants reuse raw ids.",
            "environment": {
                "SERVICETITAN_HOME": "config directory override",
                "SERVICETITAN_PROFILE": "default profile name",
                "SERVICETITAN_DATA": "snapshot path override",
                "SERVICETITAN_FORMAT": "json or text",
                "SERVICETITAN_SECRETS": "auto, keyring or file",
                "SERVICETITAN_CLIENT_ID / _CLIENT_SECRET / _TENANT_ID / _APP_KEY": "credentials without a profile (suffix _<CODE> per tenant); also override stored values",
                "SERVICETITAN_ENVIRONMENT": "production (default) or integration, for env-only credentials",
                "SERVICETITAN_TIMEZONE": "IANA zone for env-only credentials",
                "SERVICETITAN_DEBUG": "print tracebacks for unexpected errors",
            },
        },
        "metrics": {"areas": list(AREAS) + ["all"], "definitions": DEFINITIONS},
        "api": _api_tree(),
        "list": {"kinds": list(KINDS)},
        "snapshotRecords": SNAPSHOT_RECORDS,
        "commands": _walk(parser, ["servicetitan"]),
    }
