#!/usr/bin/env python3
"""Regenerate src/servicetitan_cli/data/endpoints.json from ServiceTitan's public OpenAPI specs.

The developer portal (https://developer.servicetitan.io) serves its API list at
/api/docs/apis and each API's OpenAPI 3.1 document at /api/docs/apis/<api-id>.
Only GET operations are kept: this CLI never writes to ServiceTitan.

    python scripts/build_endpoint_catalogue.py
"""
import json
import os
import sys
import urllib.request

PORTAL = "https://developer.servicetitan.io"
OUT = os.path.join(os.path.dirname(__file__), "..", "src", "servicetitan_cli", "data", "endpoints.json")


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=90) as r:
        return json.load(r)


def deref(spec, ref):
    node = spec
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    return node


def response_shape(spec, op):
    try:
        sch = op["responses"]["200"]["content"]["application/json"]["schema"]
    except KeyError:
        return "unknown", []
    if "$ref" in sch:
        sch = deref(spec, sch["$ref"])
    props = sch.get("properties", {})
    if "data" in props and props["data"].get("type") == "array":
        item = props["data"]["items"]
        if "$ref" in item:
            item = deref(spec, item["$ref"])
        return "paged", sorted(item.get("properties", {}).keys())
    if sch.get("type") == "array":
        item = sch["items"]
        if "$ref" in item:
            item = deref(spec, item["$ref"])
        return "array", sorted(item.get("properties", {}).keys())
    return "object", sorted(props.keys())


def main():
    groups = fetch_json(PORTAL + "/api/docs/apis")
    apis = [a for g in groups for a in g["apis"]]
    endpoints = []
    for api in apis:
        spec = fetch_json("%s/api/docs/apis/%s" % (PORTAL, api["id"]))
        prod = [s["url"] for s in spec.get("servers", []) if s["url"].startswith("https://api.servicetitan.io")]
        prefix = prod[0].replace("https://api.servicetitan.io", "") if prod else ""
        for path, ops in spec.get("paths", {}).items():
            op = ops.get("get")
            if not op:
                continue
            query = []
            for p in op.get("parameters", []):
                if "$ref" in p:
                    p = deref(spec, p["$ref"])
                if p.get("in") == "query":
                    query.append(p["name"])
            kind, fields = response_shape(spec, op)
            endpoints.append({
                "module": api["id"].replace("tenant-", ""),
                "title": api.get("displayName"),
                "operation": op.get("operationId"),
                "path": prefix + path,
                "summary": (op.get("summary") or (op.get("description") or "")[:160]),
                "kind": kind,
                "export": "/export/" in path,
                "query": query,
                "fields": fields[:60],
            })
        sys.stderr.write("%s: %d GET operations\n" % (api["id"], sum(1 for e in endpoints if e["module"] == api["id"].replace("tenant-", ""))))
    endpoints.sort(key=lambda e: (e["module"], e["path"]))
    doc = {"source": "%s/api/docs/apis/<api-id> (OpenAPI 3.1), read-only GET operations only" % PORTAL,
           "generatedFrom": "scripts/build_endpoint_catalogue.py", "count": len(endpoints), "endpoints": endpoints}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=0, separators=(",", ":"))
    print("wrote %s (%d endpoints)" % (os.path.normpath(OUT), len(endpoints)))


if __name__ == "__main__":
    main()
