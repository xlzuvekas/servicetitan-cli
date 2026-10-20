"""``servicetitan mcp``: the CLI as a local MCP server over stdio, named st-operator.

st-operator is an independent, third-party integration. It is not made, endorsed or supported by ServiceTitan.

Transport: MCP's stdio transport, JSON-RPC 2.0 with one UTF-8 message per line on stdin and stdout. Only
protocol messages ever reach stdout. While a tool runs, anything printed goes to stderr, and stderr lines become
progress notifications when the client asked for them.

Every tool call goes through ``cli.run_captured``, the same code path as the command line, so answers, errors and
hints are identical, and each call reads the profile store afresh: a person can run ``servicetitan setup`` while
the server is up. Tools are deliberately few, because hosts put every tool description into the model's context.
The API's 600 operations are reached through api_discover, api_read and api_write rather than one tool each.
"""
import argparse
import contextlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading

from . import __version__
from .errors import CliError
from .output import json_default

SERVER_NAME = "st-operator"
SERVER_TITLE = "ST Operator: unofficial third-party tools for ServiceTitan data"
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")   # newest first
MAX_TEXT = 60000              # characters in one tool answer; larger results are kept locally and summarised
DEFAULT_REFRESH_MAX_AGE = 6.0

INSTRUCTIONS = (
    "Unofficial third-party tools for one company's ServiceTitan data; not affiliated with ServiceTitan. Start "
    "with status. "
    "brief, metrics and list_records answer from a local snapshot: if status says it is missing, stale or "
    "incomplete, call refresh first (a full pull takes minutes). Before quoting a number to a person, call "
    "metrics with explain true once and keep the definition with the number. For anything the snapshot does not hold, find the "
    "operation with api_discover, then call api_read. Changes in ServiceTitan go through api_write: preview with "
    "dry_run true (the default) and run it with dry_run false only when the person asked for that change. Never ask "
    "for the client secret or app key; if nothing is set up, ask the person to run `servicetitan setup` in their "
    "own terminal.")

COMMON = {
    "profile": {"type": "string", "description": "company profile to use (default: the profile the server was started "
                                                 "with, else the machine's default)"},
    "tenant": {"type": "string", "description": "one ServiceTitan account of the profile, by its code (default: all)"},
    "as_of": {"type": "string", "description": "treat this ISO date or datetime as now, for reproducible answers"},
    "save": {"type": "boolean", "description": "also keep this result on this machine, findable later with search "
                                               "kind=result or saved_results"},
    "save_label": {"type": "string", "description": "a label stored with a saved result"},
}
SKIP_DESTS = {"help", "config", "env_file", "data", "format", "compact", "secrets", "quiet"} | set(COMMON)

READ_LOCAL = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
READ_REMOTE = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}


class ToolInputError(Exception):
    """Arguments that do not fit the tool; reported to the model as a tool error so it can correct itself."""


# ---------------------------------------------------------------- schemas from the real parser
def _find_parser(parser, words):
    p = parser
    for word in words:
        sub = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        p = sub.choices[word]
    return p


def _help_text(action):
    text = action.help or ""
    if text == argparse.SUPPRESS:
        return ""
    try:
        return text % dict(vars(action), prog="servicetitan")
    except (KeyError, TypeError, ValueError):
        return text


def _property(action):
    if isinstance(action, argparse._StoreTrueAction):
        prop = {"type": "boolean"}
    elif action.type is int:
        prop = {"type": "integer"}
    elif action.type is float:
        prop = {"type": "number"}
    else:
        prop = {"type": "string"}
    if action.choices and prop["type"] == "string":
        prop["enum"] = [str(c) for c in action.choices]
    if isinstance(action, argparse._AppendAction):
        prop = {"type": "array", "items": prop}
    text = _help_text(action)
    if text:
        prop["description"] = text
    if (action.default not in (None, False, [], argparse.SUPPRESS) and not isinstance(action, argparse._StoreTrueAction)
            and isinstance(action.default, (str, int, float))):
        prop["default"] = action.default
    return prop


def parser_spec(words, common=(), skip=()):
    """Input schema plus the positional and option names of ``servicetitan <words>``, read from argparse itself,
    so a new CLI option shows up in the MCP tool automatically."""
    from .cli import build_parser
    p = _find_parser(build_parser(), words)
    props, required, positionals, options = {}, [], [], {}
    for a in p._actions:
        if isinstance(a, (argparse._HelpAction, argparse._SubParsersAction)) or a.dest in skip:
            continue
        if not a.option_strings:
            positionals.append(a.dest)
            props[a.dest] = _property(a)
            required.append(a.dest)
        elif a.dest not in SKIP_DESTS:
            props[a.dest] = _property(a)
            options[a.dest] = a
    for key in common:
        props[key] = dict(COMMON[key])
    schema = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = required
    return {"schema": schema, "positionals": positionals, "options": options}


def _scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (list, tuple)):
        return ",".join(_scalar(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _argv_from(words, spec, args):
    argv = list(words)
    for name in spec["positionals"]:
        value = args.get(name)
        if value in (None, ""):
            raise ToolInputError("%s is required" % name)
        argv.append(_scalar(value))
    for name, action in spec["options"].items():
        value = args.get(name)
        if value is None:
            continue
        flag = max(action.option_strings, key=len)
        if isinstance(action, argparse._StoreTrueAction):
            if value is True:
                argv.append(flag)
            continue
        for item in (value if isinstance(action, argparse._AppendAction) and isinstance(value, list) else [value]):
            argv.append("%s=%s" % (flag, _scalar(item)))       # --sort=-balance: a leading dash stays a value
    return argv


_TYPES = {"string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,), "object": (dict,),
          "array": (list,)}


def check_arguments(args, schema):
    props = schema.get("properties") or {}
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise ToolInputError("unknown argument(s): %s. Allowed: %s" % (", ".join(unknown), ", ".join(sorted(props))))
    for name in schema.get("required") or []:
        if args.get(name) in (None, ""):
            raise ToolInputError("%s is required" % name)
    for name, value in args.items():
        if value is None:
            continue
        kind = props[name].get("type")
        ok = isinstance(value, _TYPES.get(kind, (object,)))
        if kind in ("integer", "number") and isinstance(value, bool):
            ok = False
        if kind == "integer" and isinstance(value, float) and value.is_integer():
            ok = True
        if kind == "string" and isinstance(value, (list, int, float)) and not isinstance(value, bool):
            ok = True                                            # lists join with commas, numbers become text
        if not ok:
            raise ToolInputError("%s must be %s" % (name, "an " + kind if kind in ("integer", "object", "array")
                                                    else "a " + kind))
        if "enum" in props[name] and str(value).lower() not in [e.lower() for e in props[name]["enum"]]:
            raise ToolInputError("%s must be one of: %s" % (name, ", ".join(props[name]["enum"])))


def _global_flags(args, keys, default_profile):
    out = []
    profile = args.get("profile") or default_profile
    if "profile" in keys and profile:
        out.append("--profile=%s" % profile)
    if "tenant" in keys and args.get("tenant"):
        out.append("--tenant=%s" % args["tenant"])
    if "as_of" in keys and args.get("as_of"):
        out.append("--as-of=%s" % args["as_of"])
    if "save" in keys and args.get("save"):
        out.append("--save")
        if args.get("save_label"):
            out.append("--save-label=%s" % args["save_label"])
    return out


# ---------------------------------------------------------------- tools
class Tool(object):
    def __init__(self, name, title, description, schema, annotations, run):
        self.name, self.title, self.description = name, title, description
        self.schema, self.annotations, self.run = schema, annotations, run

    def describe(self):
        return {"name": self.name, "title": self.title, "description": self.description, "inputSchema": self.schema,
                "annotations": dict(self.annotations, title=self.title)}


def _resolve_operation(args):
    from . import endpoints
    for key in ("module", "resource", "operation"):
        if not args.get(key):
            raise ToolInputError("%s is required (find it with api_discover)" % key)
    module = endpoints.resolve_module(args["module"])
    resource = endpoints.resolve_resource(module, args["resource"])
    return endpoints.resolve_operation(module, resource, args["operation"])


def _norm(name):
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def _operation_argv(op, args):
    from .apitree import kebab
    argv = ["api", op["module"], op["resource"], op["operation"]]
    given = dict(args.get("path_params") or {})
    names = [p["name"] for p in op.get("params", []) if p["in"] == "path"]
    lookup = {_norm(k): v for k, v in given.items()}
    unknown = [k for k in given if _norm(k) not in {_norm(n) for n in names}]
    if unknown:
        raise ToolInputError("unknown path parameter(s) %s; %s takes: %s" % (", ".join(unknown), op["operation"],
                                                                             ", ".join(names) or "none"))
    for name in names:
        if _norm(name) not in lookup or lookup[_norm(name)] in (None, ""):
            raise ToolInputError("path_params needs %s for %s %s %s" % (", ".join(names), op["module"], op["resource"],
                                                                         op["operation"]))
        argv.append(_scalar(lookup[_norm(name)]))
    query_names = {_norm(p["name"]): p["name"] for p in op.get("params", []) if p["in"] == "query"}
    for key, value in (args.get("query") or {}).items():
        if value is None:
            continue
        name = query_names.get(_norm(key))
        if name is None:
            raise ToolInputError("%s is not a query parameter of %s %s %s. Valid: %s"
                                 % (key, op["module"], op["resource"], op["operation"],
                                    ", ".join(sorted(query_names.values())[:60]) or "none"))
        argv.append("--%s=%s" % (kebab(name), _scalar(value)))
    return argv


def build_tools(read_only=False, default_profile=None):
    """The tool list. ``read_only`` leaves api_write out entirely."""
    from .cli import run_captured
    tools = []

    def from_parser(name, title, description, words, annotations, common, skip=(), defaults=None):
        spec = parser_spec(words, common=common, skip=skip)

        def run(args):
            merged = dict(defaults or {})
            merged.update({k: v for k, v in args.items() if v is not None})
            argv = _argv_from(words, spec, merged) + _global_flags(merged, common, default_profile)
            return argv, run_captured(argv)
        tools.append(Tool(name, title, description, spec["schema"], annotations, run))
        return spec

    # status: orientation, including the setup checklist when nothing is configured yet
    def run_status(args):
        flags = _global_flags(args, ("profile",), default_profile)
        checklist = run_captured(["init"] + flags)
        status = run_captured(["status"] + flags)
        return ["status"] + flags, {
            "ok": True, "warnings": status.get("warnings") or [],
            "result": {"checklist": checklist["result"] if checklist["ok"] else {"error": checklist["error"]},
                       "status": status["result"] if status["ok"] else {"error": status["error"]}}}
    tools.append(Tool(
        "status", "Status and setup checklist",
        "Start here. Which company profiles and ServiceTitan accounts are set up, how old the local snapshot is, the "
        "write policy, and a setup checklist whose `next` is the command to run now. If nothing is set up, ask the "
        "person to run `servicetitan setup` in their own terminal; never ask them for secrets.",
        {"type": "object", "properties": {"profile": COMMON["profile"]}, "additionalProperties": False},
        READ_LOCAL, run_status))

    from_parser("brief", "Operating picture",
                "The day's operating picture in one call, from the local snapshot: today, the next 7 days, month to "
                "date, receivables, memberships, open pipeline and flags worth a look. If the snapshot is missing or "
                "stale, call refresh first.",
                ["brief"], READ_LOCAL, ("profile", "tenant", "as_of", "save", "save_label"))

    from_parser("metrics", "Derived KPIs",
                "KPIs for one area (revenue, service, sales, install, accounting, marketing, office, memberships) or "
                "all, computed from the local snapshot exactly as the dashboard computes them. Ranges end at as_of. "
                "Set explain true once before quoting a number to a person and keep the definition with it: for "
                "example avg ticket counts billed calls only, and close rate counts opportunities, not estimates.",
                ["metrics"], READ_LOCAL, ("profile", "tenant", "as_of", "save", "save_label"))

    from_parser("list_records", "Snapshot records",
                "Records from the local snapshot: jobs, opportunities, invoices, purchase-orders, calls, memberships, "
                "technicians, campaigns, business-units, people. Each filter applies to the kinds its description "
                "names. Dates apply only when range or start is given. Keep answers small with fields (comma-separated) "
                "and limit; count true returns only the total; sort takes a field name, with a leading - for "
                "descending.",
                ["list"], READ_LOCAL, ("profile", "tenant", "as_of", "save", "save_label"))

    from_parser("search", "Search stored data",
                "Search everything stored on this machine, by meaning and by exact numbers: snapshot records, saved "
                "results, the API's operations, metric definitions and docs. Each hit carries a `next` command; "
                "confirm figures with list_records or metrics before quoting them. Needs the local index, which "
                "refresh with index true builds (that needs the optional [index] install).",
                ["index", "query"], READ_LOCAL, ("profile", "tenant"))

    from_parser("saved_results", "Saved results",
                "Results kept with save true: lists them, or returns one in full when show names a file from the list.",
                ["index", "results"], READ_LOCAL, ("profile",))

    from_parser("refresh", "Pull a fresh snapshot",
                "Pull a fresh snapshot from ServiceTitan; this only reads from ServiceTitan. Skips the pull when the "
                "snapshot is younger than max_age hours (default %g; 0 forces a pull). A full pull takes minutes, and "
                "progress is reported when the client supports it. index true also updates the local search index."
                % DEFAULT_REFRESH_MAX_AGE,
                ["fetch"], {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
                ("profile", "tenant"), skip=("out",), defaults={"max_age": DEFAULT_REFRESH_MAX_AGE})

    # the API tree, progressively
    def run_discover(args):
        if args.get("search"):
            argv = ["api", "endpoints", "--search=%s" % args["search"], "--limit=%d" % int(args.get("limit") or 20)]
            if args.get("module"):
                argv.append("--module=%s" % args["module"])
            if args.get("include_writes"):
                argv.append("--all-methods")
        elif args.get("operation"):
            if not (args.get("module") and args.get("resource")):
                raise ToolInputError("operation needs module and resource")
            argv = ["api", args["module"], args["resource"], args["operation"], "--describe"]
        elif args.get("resource"):
            if not args.get("module"):
                raise ToolInputError("resource needs module")
            argv = ["api", args["module"], args["resource"]]
        elif args.get("module"):
            argv = ["api", args["module"]]
        else:
            argv = ["api"]
        return argv, run_captured(argv)
    tools.append(Tool(
        "api_discover", "Browse the ServiceTitan API",
        "Browse ServiceTitan's API one level at a time, from its published OpenAPI documents (no credentials "
        "needed). No arguments lists the modules; module lists its resources; module and resource list the "
        "operations; adding operation returns its contract: path and query parameters, enums, request body and "
        "the scope it needs. search finds operations by words when the module is unknown.",
        {"type": "object", "additionalProperties": False, "properties": {
            "module": {"type": "string", "description": "e.g. jpm, crm, accounting, settings, telecom (aliases: jobs, sales, calls ...)"},
            "resource": {"type": "string", "description": "e.g. jobs, appointments, invoices"},
            "operation": {"type": "string", "description": "e.g. list, get, export"},
            "search": {"type": "string", "description": "words that must all appear in the operation's path, name, summary or parameters"},
            "include_writes": {"type": "boolean", "description": "search: include write operations"},
            "limit": {"type": "integer", "description": "search: maximum operations to return (default 20)"}}},
        READ_LOCAL, run_discover))

    operation_props = {
        "module": {"type": "string", "description": "module, from api_discover"},
        "resource": {"type": "string", "description": "resource, from api_discover"},
        "operation": {"type": "string", "description": "operation, from api_discover"},
        "path_params": {"type": "object", "description": "the operation's path parameters by name, e.g. {\"id\": 12345}"},
        "query": {"type": "object", "description": "query parameters by their API names, e.g. {\"jobStatus\": "
                                                   "\"Completed\", \"completedOnOrAfter\": \"2026-09-01T00:00:00Z\"}"},
    }

    def run_read(args):
        if args.get("raw_path"):
            if args.get("module") or args.get("operation"):
                raise ToolInputError("pass either raw_path or module, resource and operation, not both")
            argv = ["api", "get", args["raw_path"]]
            argv += ["--param=%s=%s" % (k, _scalar(v)) for k, v in (args.get("params") or {}).items() if v is not None]
            if args.get("all"):
                argv.append("--all")
            if args.get("max_pages"):
                argv.append("--max-pages=%d" % int(args["max_pages"]))
        else:
            op = _resolve_operation(args)
            if op["method"] != "GET":
                raise ToolInputError("%s %s %s is a write operation (%s); use api_write"
                                     % (op["module"], op["resource"], op["operation"], op["method"]))
            argv = _operation_argv(op, args)
            paged = op.get("kind") == "paged"
            for key, flag in (("all", "--all"), ("count", "--count")):
                if args.get(key):
                    if not paged:
                        raise ToolInputError("%s applies to paged list operations only" % key)
                    argv.append(flag)
            if args.get("max_pages"):
                if not paged:
                    raise ToolInputError("max_pages applies to paged list operations only")
                argv.append("--max-pages=%d" % int(args["max_pages"]))
            if args.get("fields"):
                argv.append("--fields=%s" % _scalar(args["fields"]))
        argv += _global_flags(args, ("profile", "tenant", "save", "save_label"), default_profile)
        return argv, run_captured(argv)
    read_props = dict(operation_props)
    read_props.update({
        "all": {"type": "boolean", "description": "paged lists: follow pages until the end (capped by max_pages)"},
        "max_pages": {"type": "integer", "description": "page cap with all (default 20)"},
        "count": {"type": "boolean", "description": "paged lists: return only totalCount"},
        "fields": {"type": "string", "description": "keep only these fields on each record, comma-separated"},
        "raw_path": {"type": "string", "description": "instead of an operation: any GET path, e.g. jpm/v2/jobs"},
        "params": {"type": "object", "description": "query parameters for raw_path"},
        "profile": COMMON["profile"], "tenant": COMMON["tenant"], "save": COMMON["save"],
        "save_label": COMMON["save_label"]})
    tools.append(Tool(
        "api_read", "Read from the ServiceTitan API",
        "Run one read (GET) operation against ServiceTitan now, for one tenant. Find the operation and its "
        "parameters with api_discover first. Paged lists return one page unless all is true; use count for totals "
        "and fields to keep answers small.",
        {"type": "object", "properties": read_props, "additionalProperties": False}, READ_REMOTE, run_read))

    if not read_only:
        def run_write(args):
            op = _resolve_operation(args)
            if op["method"] == "GET":
                raise ToolInputError("%s %s %s only reads; use api_read" % (op["module"], op["resource"], op["operation"]))
            argv = _operation_argv(op, args)
            if args.get("body") is not None:
                argv.append("--body=%s" % json.dumps(args["body"], ensure_ascii=False))
            argv += ["--dry-run", "--write"] if args.get("dry_run", True) else ["--write", "--yes"]
            argv += _global_flags(args, ("profile", "tenant", "save", "save_label"), default_profile)
            return argv, run_captured(argv)
        write_props = dict(operation_props)
        write_props.update({
            "body": {"type": "object", "description": "the JSON request body; api_discover with operation shows its "
                                                      "fields and which are required"},
            "dry_run": {"type": "boolean", "default": True,
                        "description": "true (the default): return the exact request and whether the write policy "
                                       "allows it, and send nothing. false: send it."},
            "profile": COMMON["profile"], "tenant": COMMON["tenant"], "save": COMMON["save"],
            "save_label": COMMON["save_label"]})
        tools.append(Tool(
            "api_write", "Change data in ServiceTitan",
            "Run one write operation (create, update, delete) against ServiceTitan. Find it with api_discover first. "
            "dry_run true, the default, returns the exact request and whether the profile's write policy would "
            "allow it, and sends nothing. Use dry_run false only when the person asked for this change. The "
            "profile's write policy and the credential's granted scopes still apply, failures are never retried, "
            "and every attempt is logged on this machine.",
            {"type": "object", "properties": write_props, "required": ["module", "resource", "operation"],
             "additionalProperties": False},
            {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}, run_write))
    return tools


# ---------------------------------------------------------------- results
def _dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=json_default)


def _shape(value, depth=0):
    if isinstance(value, dict):
        if depth >= 2 or len(value) > 40:
            return "object with %d keys" % len(value)
        return {k: _shape(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return "list of %d" % len(value)
    if isinstance(value, str) and len(value) > 200:
        return value[:200] + "..."
    return value


def _oversize(result, argv, args, size, default_profile):
    """Keep a too-large result on disk and answer with its outline instead."""
    saved = result.get("saved") if isinstance(result, dict) else None
    if not saved:
        from . import indexing
        from .config import Context
        ctx = Context(profile=args.get("profile") or default_profile, quiet=True)
        saved = indexing.save_result(ctx, argv, result, label="kept automatically: too large for one answer",
                                     quiet=True, words=[w for w in argv if not w.startswith("-")][:4])
    name = os.path.basename(saved["path"])
    return {"truncated": True, "characters": size, "saved": {"file": name, "path": saved["path"]},
            "outline": _shape(result),
            "next": "Narrow the request (fields, limit, count or a shorter range), or read the full copy with "
                    "saved_results show=%s" % name}


def _tool_error(error):
    return {"content": [{"type": "text", "text": _dumps({"error": error})}], "isError": True}


def _format(argv, response, args, default_profile):
    if not response.get("ok"):
        return _tool_error(response["error"])
    result = response.get("result")
    text = _dumps(result)
    if len(text) > MAX_TEXT:
        text = _dumps(_oversize(result, argv, args, len(text), default_profile))
    content = [{"type": "text", "text": text}]
    if response.get("warnings"):
        content.append({"type": "text", "text": "Warnings: " + " | ".join(response["warnings"])})
    return {"content": content, "isError": False}


class _ProgressStream(object):
    """stderr while a tool runs: passes everything through, and turns each line into a progress notification
    when the client sent a progress token. Thread-safe, because fetch pulls tenants in parallel."""

    def __init__(self, real, send, token, with_message=True):
        self.real, self.send, self.token, self.with_message = real, send, token, with_message
        self.count = 0
        self._partial = ""
        self._lock = threading.Lock()

    def write(self, text):
        with self._lock:
            self.real.write(text)
            if self.token is None:
                return len(text)
            self._partial += text
            *done, self._partial = self._partial.split("\n")
            for line in done:
                line = line.strip()
                if not line:
                    continue
                self.count += 1
                params = {"progressToken": self.token, "progress": self.count}
                if self.with_message:
                    params["message"] = line[:300]
                self.send({"jsonrpc": "2.0", "method": "notifications/progress", "params": params})
        return len(text)

    def flush(self):
        self.real.flush()

    def isatty(self):
        return False


# ---------------------------------------------------------------- protocol
def _result(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": mid, "error": err}


class Server(object):
    def __init__(self, read_only=False, default_profile=None, send=None, log=None):
        self.read_only = read_only
        self.default_profile = default_profile
        self.send = send
        self.log = log or sys.stderr
        self.version = PROTOCOL_VERSIONS[0]
        self.tools = {t.name: t for t in build_tools(read_only=read_only, default_profile=default_profile)}

    def handle(self, message):
        if not isinstance(message, dict):
            return _error(None, -32600, "Invalid request: expected a JSON-RPC object")
        method = message.get("method")
        mid = message.get("id")
        if method is None:                   # a response to a request of ours; this server sends none
            return None
        if "id" not in message:              # a notification: never answered
            return None
        params = message.get("params") or {}
        try:
            if method == "initialize":
                requested = params.get("protocolVersion")
                self.version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
                info = {"name": SERVER_NAME, "version": __version__}
                if self.version >= "2025-06-18":
                    info["title"] = SERVER_TITLE
                return _result(mid, {"protocolVersion": self.version, "capabilities": {"tools": {"listChanged": False}},
                                     "serverInfo": info, "instructions": INSTRUCTIONS})
            if method == "ping":
                return _result(mid, {})
            if method == "tools/list":
                return _result(mid, {"tools": [t.describe() for t in self.tools.values()]})
            if method == "tools/call":
                return self._call(mid, params)
            if method in ("resources/list", "resources/templates/list", "prompts/list"):
                key = {"resources/list": "resources", "resources/templates/list": "resourceTemplates",
                       "prompts/list": "prompts"}[method]
                return _result(mid, {key: []})
            if method == "logging/setLevel":
                return _result(mid, {})
            return _error(mid, -32601, "Method not found: %s" % method)
        except Exception as e:  # one bad message must not stop the server
            self.log.write("%s: %s while handling %s: %s\n" % (SERVER_NAME, type(e).__name__, method, e))
            return _error(mid, -32603, "Internal error: %s" % e)

    def _call(self, mid, params):
        name = params.get("name")
        tool = self.tools.get(name)
        if tool is None:
            return _error(mid, -32602, "Unknown tool: %s" % name, {"tools": sorted(self.tools)})
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _result(mid, _tool_error({"code": "USAGE", "message": "arguments must be a JSON object"}))
        token = (params.get("_meta") or {}).get("progressToken")
        stream = _ProgressStream(sys.stderr, self.send, token, with_message=self.version != "2024-11-05")
        try:
            check_arguments(args, tool.schema)
            with contextlib.redirect_stdout(sys.stderr), contextlib.redirect_stderr(stream):
                argv, response = tool.run(args)
        except ToolInputError as e:
            return _result(mid, _tool_error({"code": "USAGE", "exit": 2, "message": str(e),
                                             "hint": "Check the arguments against the tool's input schema."}))
        except CliError as e:
            return _result(mid, _tool_error(e.to_dict()["error"]))
        return _result(mid, _format(argv, response, args, self.default_profile))


def serve(stdin=None, stdout=None, stderr=None, read_only=False, default_profile=None):
    """Serve MCP over stdio until the client closes stdin."""
    inp = stdin if stdin is not None else sys.stdin.buffer
    out = stdout if stdout is not None else sys.stdout.buffer
    lock = threading.Lock()

    def send(message):
        data = _dumps(message).encode("utf-8") + b"\n"
        with lock:
            out.write(data)
            out.flush()

    server = Server(read_only=read_only, default_profile=default_profile, send=send, log=stderr)
    while True:
        raw = inp.readline()
        if not raw:
            return
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            send(_error(None, -32700, "Parse error: each line must be one JSON-RPC message"))
            continue
        if isinstance(message, list):        # JSON-RPC batch, from older protocol versions
            replies = [r for r in (server.handle(m) for m in message) if r is not None]
            if replies:
                send(replies)
            continue
        reply = server.handle(message)
        if reply is not None:
            send(reply)


# ---------------------------------------------------------------- client configuration
def launch_command():
    """How an MCP client should start this server: an absolute path, because desktop apps do not see the shell's PATH."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    argv0 = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    base = os.path.basename(argv0).lower()
    if base.startswith("servicetitan") and not base.endswith(".py"):
        for candidate in (argv0, argv0 + ".exe"):
            if os.path.isfile(candidate):
                return [candidate]
    found = shutil.which("servicetitan")
    if found:
        return [os.path.abspath(found)]
    return [sys.executable, "-m", "servicetitan_cli"]


def desktop_config_path():
    home = os.path.expanduser("~")
    if sys.platform.startswith("win"):
        return os.path.join(os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming"), "Claude",
                            "claude_desktop_config.json")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "Claude", "claude_desktop_config.json")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), "Claude",
                        "claude_desktop_config.json")


def client_config(read_only=False):
    command = launch_command()
    args = command[1:] + ["mcp"] + (["--read-only"] if read_only else [])
    entry = {"command": command[0], "args": args}
    env = {k: os.environ[k] for k in ("SERVICETITAN_HOME", "SERVICETITAN_PROFILE", "SERVICETITAN_SECRETS")
           if os.environ.get(k)}
    if env:
        entry["env"] = env
    full = [command[0]] + args
    joined = subprocess.list2cmdline(full) if sys.platform.startswith("win") else " ".join(shlex.quote(x) for x in full)
    return {"source": "mcp", "server": SERVER_NAME, "thirdParty": "st-operator is independent and not affiliated "
                                                                  "with ServiceTitan.",
            "claudeDesktop": {"configFile": desktop_config_path(), "add": {"mcpServers": {SERVER_NAME: entry}},
                              "how": "In Claude Desktop: Settings > Developer > Edit Config, merge the `add` block into "
                                     "the file, save, and restart Claude Desktop."},
            "claudeCode": "claude mcp add %s -- %s" % (SERVER_NAME, joined),
            "anyClient": entry,
            "note": "The absolute path matters: desktop apps start servers without your shell's PATH. Add --read-only "
                    "to leave out the write tool."}
