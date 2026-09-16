#!/usr/bin/env python3
"""Regenerate src/servicetitan_cli/data/endpoints.json from ServiceTitan's public OpenAPI specs.

The developer portal (https://developer.servicetitan.io) serves its API list at
/api/docs/apis and each API's OpenAPI 3.1 document at /api/docs/apis/<api-id>.
Every operation is catalogued with its parameters so the CLI can expose the
whole API as a command tree (module > resource > operation). GET operations
are executable; write operations are listed for discovery only.

    python scripts/build_endpoint_catalogue.py                 # download from the portal
    python scripts/build_endpoint_catalogue.py --specs-dir DIR # reuse downloaded specs (tenant-*.json + apis.json)
"""
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict

PORTAL = "https://developer.servicetitan.io"
OUT = os.path.join(os.path.dirname(__file__), "..", "src", "servicetitan_cli", "data", "endpoints.json")
METHODS = ("get", "post", "put", "patch", "delete")
MODULE_ALIASES = {"salestech": ["sales", "estimates"], "jpm": ["jobs"], "settings": ["stt"], "telecom": ["calls", "tlc"],
                  "accounting": ["acc"], "memberships": ["mem"], "marketing": ["mrk"], "inventory": ["inv"],
                  "dispatch": ["dis"], "pricebook": ["pb"], "customer-interactions": ["ratings"]}


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=90) as r:
        return json.load(r)


def deref(spec, ref):
    node = spec
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    return node


def resolve(spec, schema):
    """Follow $ref chains, including the `oneOf: [{$ref}]` wrapper ServiceTitan uses for enum parameters."""
    seen = 0
    while isinstance(schema, dict) and seen < 10:
        if "$ref" in schema:
            schema = deref(spec, schema["$ref"])
        elif len(schema.get("oneOf") or schema.get("anyOf") or schema.get("allOf") or []) == 1 and not schema.get("enum"):
            inner = (schema.get("oneOf") or schema.get("anyOf") or schema.get("allOf"))[0]
            merged = dict(schema)
            for key in ("oneOf", "anyOf", "allOf"):
                merged.pop(key, None)
            inner = resolve(spec, inner)
            merged.update({k: v for k, v in inner.items() if k not in merged or k in ("enum", "type", "format", "properties", "items")})
            schema = merged
        else:
            break
        seen += 1
    return schema or {}


def schema_type(spec, schema):
    """(type, enum, format) for a parameter or property schema."""
    schema = resolve(spec, schema)
    enum = schema.get("enum")
    t = schema.get("type")
    if isinstance(t, list):
        t = [x for x in t if x != "null"][0] if [x for x in t if x != "null"] else "string"
    if not t and enum:
        t = "string"
    if t == "array":
        items = resolve(spec, schema.get("items") or {})
        enum = enum or items.get("enum")
        return "array", enum, items.get("format")
    if not t and schema.get("properties"):
        t = "object"
    return t or "string", enum, schema.get("format")


def oauth_scope(op):
    for req in op.get("security") or []:
        for scheme, scopes in req.items():
            if scheme.lower().startswith("oauth") and scopes:
                return scopes[0]
    return None


def response_shape(spec, op):
    try:
        sch = op["responses"]["200"]["content"]["application/json"]["schema"]
    except KeyError:
        return "unknown", []
    sch = resolve(spec, sch)
    props = sch.get("properties", {})
    if "data" in props and resolve(spec, props["data"]).get("type") == "array":
        item = resolve(spec, resolve(spec, props["data"]).get("items") or {})
        return "paged", sorted(item.get("properties", {}).keys())
    if sch.get("type") == "array":
        item = resolve(spec, sch.get("items") or {})
        return "array", sorted(item.get("properties", {}).keys())
    return "object", sorted(props.keys())


def body_shape(spec, op):
    try:
        sch = op["requestBody"]["content"]["application/json"]["schema"]
    except KeyError:
        return None
    sch = resolve(spec, sch)
    props = {}
    for name, ps in (sch.get("properties") or {}).items():
        t, enum, _ = schema_type(spec, ps)
        props[name] = t if not enum else "enum:" + "|".join(str(e) for e in enum[:12])
    return {"required": sch.get("required") or [], "properties": props}


def clean(text, limit=300):
    text = (text or "").replace("\\", " ")          # line-continuation backslashes in the portal's markdown
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit - 3] + "..."


def derive_names(prefix, path, method, kind):
    """(resource, operation) from a path like /jpm/v2/tenant/{tenant}/jobs/{id}/history."""
    rel = path[len(prefix):] if prefix and path.startswith(prefix) else path
    segs = [s for s in rel.split("/") if s]
    version = None
    if segs and re.match(r"^v\d+$", segs[0]):        # telecom: /v2/tenant/{tenant}/calls
        version = segs.pop(0)
    if segs[:2] == ["tenant", "{tenant}"]:
        segs = segs[2:]
    if not segs:
        return "root", method, version
    if segs[0] == "export":
        literal = [s for s in segs[1:] if not s.startswith("{")]
        return "-".join(literal) or "export", "export", version
    resource = segs[0]
    rest = segs[1:]
    literal = [s for s in rest if not s.startswith("{")]
    ends_with_param = bool(rest) and rest[-1].startswith("{")
    if method == "get":
        if literal == ["export"]:                   # legacy "/estimates/export" style feed
            op = "export-legacy"
        elif not rest:
            op = "list" if kind == "paged" else "get"
        elif not literal:
            op = "get"
        else:
            op = "-".join(literal) + ("-get" if ends_with_param else "")
    else:
        verb = {"post": "create", "put": "update", "patch": "patch", "delete": "delete"}[method]
        op = "%s-%s" % (verb, "-".join(literal)) if literal else verb
    return resource, op, version


def load_spec(api_id, specs_dir):
    if specs_dir:
        with open(os.path.join(specs_dir, api_id + ".json"), encoding="utf-8") as f:
            return json.load(f)
    return fetch_json("%s/api/docs/apis/%s" % (PORTAL, api_id))


def main():
    specs_dir = None
    if "--specs-dir" in sys.argv:
        specs_dir = sys.argv[sys.argv.index("--specs-dir") + 1]
    if specs_dir:
        with open(os.path.join(specs_dir, "apis.json"), encoding="utf-8") as f:
            groups = json.load(f)
    else:
        groups = fetch_json(PORTAL + "/api/docs/apis")
    apis = [a for g in groups for a in g["apis"]]
    modules, operations = [], []
    for api in apis:
        spec = load_spec(api["id"], specs_dir)
        prod = [s["url"] for s in spec.get("servers", []) if s["url"].startswith("https://api.servicetitan.io")]
        prefix = prod[0].replace("https://api.servicetitan.io", "") if prod else ""
        module = re.sub(r"-v\d+$", "", api["id"].replace("tenant-", ""))
        modules.append({"module": module, "apiId": api["id"], "title": api.get("displayName"),
                        "description": clean(api.get("description"), 400), "prefix": prefix,
                        "aliases": MODULE_ALIASES.get(module, [])})
        per_resource = defaultdict(list)
        for path, ops in spec.get("paths", {}).items():
            for method in METHODS:
                op = ops.get(method)
                if not op:
                    continue
                kind, fields = response_shape(spec, op) if method == "get" else ("unknown", [])
                resource, opname, version = derive_names(prefix, path, method, kind)
                params = []
                for p in op.get("parameters", []):
                    p = resolve(spec, p) if "$ref" in p else p
                    if p.get("name") == "tenant" or p.get("in") not in ("query", "path"):
                        continue
                    t, enum, fmt = schema_type(spec, p.get("schema") or {})
                    item = {"name": p["name"], "in": p["in"], "type": t, "required": bool(p.get("required"))}
                    if fmt:
                        item["format"] = fmt
                    if enum:
                        item["enum"] = [str(e) for e in enum]
                    if p.get("description"):
                        item["description"] = clean(p["description"])
                    params.append(item)
                entry = {"module": module, "resource": resource, "operation": opname, "method": method.upper(),
                         "path": prefix + path, "operationId": op.get("operationId"),
                         "summary": clean(op.get("summary") or op.get("description"), 160),
                         "scope": oauth_scope(op), "kind": kind, "export": "/export/" in path, "params": params}
                if version:
                    entry["version"] = version
                if op.get("description") and op.get("summary") and op["description"].strip() != op["summary"].strip():
                    entry["description"] = clean(op["description"], 600)
                if method == "get":
                    entry["fields"] = fields[:80]
                else:
                    body = body_shape(spec, op)
                    if body:
                        entry["body"] = body
                per_resource[(resource, opname)].append(entry)
        # Resolve name collisions inside a module deterministically.
        for (resource, opname), entries in per_resource.items():
            if len(entries) == 1:
                operations.append(entries[0])
                continue
            entries.sort(key=lambda e: (-(int(e.get("version", "v0")[1:])), len([p for p in e["params"] if p["in"] == "path"]), e["path"]))
            used = set()
            for i, e in enumerate(entries):
                name = opname
                if e.get("version") and i > 0:
                    name = "%s-%s" % (opname, e["version"])
                elif i > 0:
                    path_params = [p["name"] for p in e["params"] if p["in"] == "path"]
                    name = "%s-by-%s" % (opname, "-".join(path_params)) if path_params else "%s-%d" % (opname, i + 1)
                while name in used:
                    name += "-alt"
                used.add(name)
                e["operation"] = name
                operations.append(e)
        sys.stderr.write("%s: %d operations\n" % (api["id"], sum(1 for o in operations if o["module"] == module)))
    operations.sort(key=lambda e: (e["module"], e["resource"], e["method"] != "GET", e["operation"]))
    doc = {"source": "%s/api/docs/apis/<api-id> (OpenAPI 3.1)" % PORTAL, "generatedFrom": "scripts/build_endpoint_catalogue.py",
           "count": len(operations), "readCount": sum(1 for o in operations if o["method"] == "GET"),
           "modules": modules, "operations": operations}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=0, separators=(",", ":"), ensure_ascii=False)
    print("wrote %s (%d operations, %d GET)" % (os.path.normpath(OUT), len(operations), doc["readCount"]))


if __name__ == "__main__":
    main()
