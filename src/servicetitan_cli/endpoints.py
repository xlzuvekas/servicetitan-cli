"""The ServiceTitan API as a command tree: module > resource > operation.

Generated from ServiceTitan's OpenAPI documents by
``scripts/build_endpoint_catalogue.py`` into ``data/endpoints.json``. Every
operation is listed so an agent can discover the whole API step by step; only
GET operations execute, because this tool is read-only.
"""
import difflib
import json
import os
import pkgutil

from .errors import UsageError

_CACHE = {}


def packaged():
    """The catalogue that ships with this release."""
    raw = pkgutil.get_data("servicetitan_cli", "data/endpoints.json")
    return json.loads(raw.decode("utf-8")) if raw else {"count": 0, "readCount": 0, "modules": [], "operations": []}


def _load():
    """A locally refreshed catalogue (`servicetitan api catalogue refresh`) beats the packaged one."""
    from .catalogue import local_path
    path = local_path()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            if isinstance(doc, dict) and doc.get("operations"):
                return doc, "local", path
        except (OSError, ValueError):
            pass
    return packaged(), "packaged", None


def _index(doc):
    index = {}
    for op in doc["operations"]:
        index.setdefault(op["module"], {}).setdefault(op["resource"], {})[op["operation"]] = op
    doc["_index"] = index
    doc["_by_path"] = {(op["path"], op["method"]): op for op in doc["operations"]}
    return doc


def catalogue():
    if "doc" not in _CACHE:
        doc, origin, path = _load()
        doc["_origin"] = origin
        doc["_path"] = path
        _CACHE["doc"] = _index(doc)
    return _CACHE["doc"]


def reload():
    _CACHE.clear()


def info():
    doc = catalogue()
    return {"origin": doc["_origin"], "path": doc.get("_path"), "generatedAt": doc.get("generatedAt"),
            "operations": doc.get("count"), "reads": doc.get("readCount"),
            "modules": [{"module": m["module"], "versions": m.get("versions") or [], "hash": m.get("hash")} for m in doc["modules"]]}


# ---------------------------------------------------------------- discovery
def modules():
    doc = catalogue()
    out = []
    for m in doc["modules"]:
        ops = [o for o in doc["operations"] if o["module"] == m["module"]]
        out.append({"module": m["module"], "title": m.get("title"), "description": m.get("description"),
                    "aliases": m.get("aliases") or [], "versions": m.get("versions") or [],
                    "resources": len(doc["_index"].get(m["module"], {})),
                    "operations": len(ops), "reads": sum(1 for o in ops if o["method"] == "GET"),
                    "writes": sum(1 for o in ops if o["method"] != "GET")})
    return out


def _module_lookup():
    if "modlookup" not in _CACHE:
        lookup = {}
        for m in catalogue()["modules"]:
            lookup[m["module"]] = m["module"]
            lookup[m["apiId"]] = m["module"]
            for a in m.get("aliases") or []:
                lookup[a] = m["module"]
        _CACHE["modlookup"] = lookup
    return _CACHE["modlookup"]


def _suggest(token, candidates):
    close = difflib.get_close_matches(token, candidates, n=4, cutoff=0.5)
    return ("did you mean %s? " % ", ".join(close)) if close else ""


def resolve_module(token):
    key = (token or "").lower().strip()
    lookup = _module_lookup()
    if key in lookup:
        return lookup[key]
    if key.endswith("-v2") and key[:-3] in lookup:
        return lookup[key[:-3]]
    names = sorted({m["module"] for m in catalogue()["modules"]})
    raise UsageError("Unknown API module %r" % token,
                     hint="%sModules: %s. Run `servicetitan api` to list them with descriptions." % (_suggest(key, names), ", ".join(names)))


def resources(module):
    index = catalogue()["_index"].get(module, {})
    out = []
    for res, ops in sorted(index.items()):
        out.append({"resource": res,
                    "reads": sorted(n for n, o in ops.items() if o["method"] == "GET"),
                    "writes": sorted(n for n, o in ops.items() if o["method"] != "GET")})
    return out


def resolve_resource(module, token):
    index = catalogue()["_index"].get(module, {})
    key = (token or "").lower().strip().replace("_", "-")
    if key in index:
        return key
    for res in index:
        if res.replace("-", "") == key.replace("-", ""):
            return res
    raise UsageError("Module %r has no resource %r" % (module, token),
                     hint="%sResources: %s" % (_suggest(key, sorted(index)), ", ".join(sorted(index))))


def operations(module, resource):
    ops = catalogue()["_index"].get(module, {}).get(resource, {})
    out = []
    for name in sorted(ops, key=lambda n: (ops[n]["method"] != "GET", n)):
        o = ops[name]
        item = {"operation": name, "method": o["method"], "path": o["path"], "summary": o.get("summary"),
                "scope": o.get("scope"), "kind": o.get("kind"), "executable": o["method"] == "GET",
                "pathParams": [p["name"] for p in o.get("params", []) if p["in"] == "path"]}
        if o.get("version"):
            item["version"] = o["version"]
        if o.get("deprecated"):
            item["deprecated"] = True
        out.append(item)
    return out


def resolve_operation(module, resource, token):
    ops = catalogue()["_index"].get(module, {}).get(resource, {})
    key = (token or "").lower().strip().replace("_", "-")
    if key in ops:
        return ops[key]
    raise UsageError("Resource %r has no operation %r" % (resource, token),
                     hint="%sOperations: %s" % (_suggest(key, sorted(ops)), ", ".join(sorted(ops))))


def describe(op):
    """Everything an agent needs before calling: parameters, response fields, example."""
    positional = [p["name"] for p in op.get("params", []) if p["in"] == "path"]
    example = "servicetitan api %s %s %s" % (op["module"], op["resource"], op["operation"])
    example += "".join(" <%s>" % p.upper() for p in positional)
    if op.get("kind") == "paged":
        example += " --page-size 50"
    out = {"module": op["module"], "resource": op["resource"], "operation": op["operation"], "method": op["method"],
           "path": op["path"], "operationId": op.get("operationId"), "summary": op.get("summary"),
           "description": op.get("description"), "scope": op.get("scope"), "kind": op.get("kind"),
           "export": op.get("export", False), "executable": op["method"] == "GET",
           "pathParams": [p for p in op.get("params", []) if p["in"] == "path"],
           "queryParams": [p for p in op.get("params", []) if p["in"] == "query"],
           "responseFields": op.get("fields") or [], "example": example}
    if op.get("body"):
        out["requestBody"] = op["body"]
    if op.get("version"):
        out["version"] = op["version"]
    if op.get("deprecated"):
        out["deprecated"] = True
        newer = [n for n, o in catalogue()["_index"].get(op["module"], {}).get(op["resource"], {}).items()
                 if o["method"] == op["method"] and not o.get("deprecated") and n.split("-v")[0] == op["operation"].split("-v")[0]]
        if newer:
            out["replacedBy"] = sorted(newer)
    return out


def by_path(path, method="GET"):
    return catalogue()["_by_path"].get((path, method))


# ---------------------------------------------------------------- search / scopes
def search(text=None, module=None, exports_only=False, include_fields=False, limit=None, methods=("GET",)):
    text = (text or "").lower().strip()
    mod = resolve_module(module) if module else None
    out = []
    for e in catalogue()["operations"]:
        if methods and e["method"] not in methods:
            continue
        if mod and e["module"] != mod:
            continue
        if exports_only and not e.get("export"):
            continue
        hay = " ".join([e["path"], e.get("operationId") or "", e.get("summary") or "", e["resource"], e["operation"],
                        " ".join(p["name"] for p in e.get("params", []))]).lower()
        if text and not all(tok in hay for tok in text.split()):
            continue
        item = {"command": "servicetitan api %s %s %s" % (e["module"], e["resource"], e["operation"]),
                "method": e["method"], "path": e["path"], "summary": e.get("summary"), "kind": e.get("kind"),
                "export": e.get("export", False), "scope": e.get("scope"),
                "query": [p["name"] for p in e.get("params", []) if p["in"] == "query"]}
        if include_fields:
            item["fields"] = e.get("fields") or []
        out.append(item)
        if limit and len(out) >= limit:
            break
    return out


def scopes_for(paths):
    """{path: scope} for catalogue GET paths (missing paths map to None)."""
    return {p: (by_path(p, "GET") or {}).get("scope") for p in paths}


def missing_scopes(granted, paths):
    """Scopes needed by ``paths`` that a token's granted scope list lacks.

    Read (``:r``) and write (``:w``) scopes are separate; a write scope does not
    imply read. Returns [] when ``granted`` is unknown.
    """
    if granted is None:
        return []
    have = set(granted)
    return sorted({scope for scope in scopes_for(paths).values() if scope and scope not in have})
