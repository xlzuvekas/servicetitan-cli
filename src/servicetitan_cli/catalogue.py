"""Building and refreshing the endpoint catalogue from ServiceTitan's OpenAPI documents.

The developer portal (https://developer.servicetitan.io) serves its API list at
``/api/docs/apis`` and each API's OpenAPI 3.1 document at ``/api/docs/apis/<api-id>``.
A catalogue is built from those documents: every operation with its
parameters, enums, scope, response fields and request-body shape, named as
``module > resource > operation`` by fixed rules so names stay stable across
rebuilds.

The package ships a catalogue built at release time. ``servicetitan api
catalogue refresh`` builds a fresh one from the live portal into the config
directory, which then takes precedence, and reports the differences.
"""
import datetime as dt
import hashlib
import json
import os
import re
from collections import defaultdict

from .dates import UTC, iso_z
from .errors import ApiError
from .paths import config_dir, ensure_dir, write_private

PORTAL = "https://developer.servicetitan.io"
METHODS = ("get", "post", "put", "patch", "delete")
MODULE_ALIASES = {"salestech": ["sales", "estimates"], "jpm": ["jobs"], "settings": ["stt"], "telecom": ["calls", "tlc"],
                  "accounting": ["acc"], "memberships": ["mem"], "marketing": ["mrk"], "inventory": ["inv"],
                  "dispatch": ["dis"], "pricebook": ["pb"], "customer-interactions": ["ratings"]}


# ---------------------------------------------------------------- spec helpers
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
    schema = resolve(spec, schema)
    enum = schema.get("enum")
    t = schema.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        t = non_null[0] if non_null else "string"
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
    text = (text or "").replace("\\", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit - 3] + "..."


def kebab(name):
    """camelCase / snake_case -> kebab-case, so every operation name is lowercase."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name).replace("_", "-").lower()


def derive_names(prefix, path, method, kind):
    """(resource, operation, version) from a path like /jpm/v2/tenant/{tenant}/jobs/{id}/history.

    Reads: no suffix -> list (paged) or get; `{id}` only -> get; literal sub-path -> that
    path (with -get when it ends in an id); /export/x -> resource x, operation export;
    legacy /x/export -> export-legacy. Writes: verb prefix (create/update/patch/delete)
    plus the literal sub-path.
    """
    rel = path[len(prefix):] if prefix and path.startswith(prefix) else path
    segs = [s for s in rel.split("/") if s]
    version = None
    if segs and re.match(r"^v\d+$", segs[0]):
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
        if literal == ["export"]:
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


# ---------------------------------------------------------------- building
def build_catalogue(groups, spec_loader, progress=None, generated_at=None):
    """``groups`` is the portal's API list; ``spec_loader(api_id)`` returns an OpenAPI document."""
    progress = progress or (lambda m: None)
    apis = [a for g in groups for a in g["apis"]]
    modules, operations = [], []
    for api in apis:
        spec = spec_loader(api["id"])
        prod = [s["url"] for s in spec.get("servers", []) if s["url"].startswith("https://api.servicetitan.io")]
        prefix = prod[0].replace("https://api.servicetitan.io", "") if prod else ""
        module = re.sub(r"-v\d+$", "", api["id"].replace("tenant-", ""))
        per_resource = defaultdict(list)
        mod_ops = []
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
                if op.get("deprecated") or "deprecated" in " ".join(op.get("tags") or []).lower():
                    entry["deprecated"] = True
                per_resource[(resource, opname)].append(entry)
        for (resource, opname), entries in per_resource.items():
            if len(entries) == 1:
                mod_ops.append(entries[0])
                continue
            entries.sort(key=lambda e: (-(int(e.get("version", "v0")[1:])), len([p for p in e["params"] if p["in"] == "path"]), e["path"]))
            used = set()
            for i, e in enumerate(entries):
                name = opname
                if e.get("version") and i > 0:
                    name = "%s-%s" % (opname, e["version"])
                elif i > 0:
                    path_params = [kebab(p["name"]) for p in e["params"] if p["in"] == "path"]
                    name = "%s-by-%s" % (opname, "-".join(path_params)) if path_params else "%s-%d" % (opname, i + 1)
                while name in used:
                    name += "-alt"
                used.add(name)
                e["operation"] = name
                mod_ops.append(e)
        mod_ops.sort(key=lambda e: (e["resource"], e["method"] != "GET", e["operation"]))
        digest = hashlib.sha256(json.dumps(mod_ops, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        modules.append({"module": module, "apiId": api["id"], "title": api.get("displayName"),
                        "description": clean(api.get("description"), 400), "prefix": prefix,
                        "aliases": MODULE_ALIASES.get(module, []), "hash": digest,
                        "versions": sorted({re.search(r"/(v\d+)/", o["path"]).group(1) for o in mod_ops if re.search(r"/(v\d+)/", o["path"])})})
        operations.extend(mod_ops)
        progress("%s: %d operations" % (api["id"], len(mod_ops)))
    operations.sort(key=lambda e: (e["module"], e["resource"], e["method"] != "GET", e["operation"]))
    return {"source": "%s/api/docs/apis/<api-id> (OpenAPI 3.1)" % PORTAL, "generatedFrom": "servicetitan_cli.catalogue",
            "generatedAt": generated_at or iso_z(dt.datetime.now(UTC)), "count": len(operations),
            "readCount": sum(1 for o in operations if o["method"] == "GET"), "modules": modules, "operations": operations}


def fetch_json(url, timeout=90):
    import requests
    try:
        r = requests.get(url, timeout=timeout, headers={"Accept": "application/json"})
    except requests.RequestException as e:
        raise ApiError("Could not reach the developer portal: %s" % e, url=url)
    if not r.ok:
        raise ApiError("Developer portal returned HTTP %s" % r.status_code, status=r.status_code, url=url, body=r.text)
    return r.json()


def build_from_portal(progress=None):
    groups = fetch_json(PORTAL + "/api/docs/apis")
    return build_catalogue(groups, lambda api_id: fetch_json("%s/api/docs/apis/%s" % (PORTAL, api_id)), progress=progress)


def build_from_dir(specs_dir, progress=None):
    with open(os.path.join(specs_dir, "apis.json"), encoding="utf-8") as f:
        groups = json.load(f)

    def load(api_id):
        with open(os.path.join(specs_dir, api_id + ".json"), encoding="utf-8") as f:
            return json.load(f)
    return build_catalogue(groups, load, progress=progress)


# ---------------------------------------------------------------- local override
def local_path():
    return os.path.join(config_dir(), "catalogue", "endpoints.json")


def save_local(doc):
    ensure_dir(os.path.dirname(local_path()))
    write_private(local_path(), json.dumps(doc, separators=(",", ":"), ensure_ascii=False))
    return local_path()


def remove_local():
    if os.path.exists(local_path()):
        os.remove(local_path())
        return True
    return False


# ---------------------------------------------------------------- diffing
def _key(op):
    return "%s %s %s" % (op["module"], op["resource"], op["operation"])


def diff(old, new):
    """What changed between two catalogues, by operation."""
    a = {_key(o): o for o in old.get("operations", [])}
    b = {_key(o): o for o in new.get("operations", [])}
    added = sorted(k for k in b if k not in a)
    removed = sorted(k for k in a if k not in b)
    changed = []
    for k in sorted(set(a) & set(b)):
        x, y = a[k], b[k]
        notes = []
        if x["path"] != y["path"]:
            notes.append("path %s -> %s" % (x["path"], y["path"]))
        px = {p["name"]: p for p in x.get("params", [])}
        py = {p["name"]: p for p in y.get("params", [])}
        for name in sorted(set(py) - set(px)):
            notes.append("new parameter %s" % name)
        for name in sorted(set(px) - set(py)):
            notes.append("removed parameter %s" % name)
        for name in sorted(set(px) & set(py)):
            if (px[name].get("enum") or []) != (py[name].get("enum") or []):
                notes.append("parameter %s values %s -> %s" % (name, px[name].get("enum"), py[name].get("enum")))
        fx, fy = set(x.get("fields") or []), set(y.get("fields") or [])
        if fy - fx:
            notes.append("new response fields %s" % sorted(fy - fx))
        if fx - fy:
            notes.append("removed response fields %s" % sorted(fx - fy))
        if x.get("scope") != y.get("scope"):
            notes.append("scope %s -> %s" % (x.get("scope"), y.get("scope")))
        if bool(x.get("deprecated")) != bool(y.get("deprecated")):
            notes.append("deprecated: %s" % bool(y.get("deprecated")))
        if notes:
            changed.append({"operation": k, "method": y["method"], "changes": notes})
    old_mods = {m["module"]: m for m in old.get("modules", [])}
    new_mods = {m["module"]: m for m in new.get("modules", [])}
    modules = {"added": sorted(set(new_mods) - set(old_mods)), "removed": sorted(set(old_mods) - set(new_mods)),
               "changed": sorted(m for m in set(old_mods) & set(new_mods) if old_mods[m].get("hash") != new_mods[m].get("hash"))}
    return {"modules": modules, "added": added, "removed": removed, "changed": changed,
            "summary": "%d added, %d removed, %d changed operations across %d changed module(s)"
                       % (len(added), len(removed), len(changed), len(modules["changed"]))}
