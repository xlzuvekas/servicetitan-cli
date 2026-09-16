"""The catalogue of ServiceTitan read endpoints, generated from the official
OpenAPI documents (see scripts/build_endpoint_catalogue.py)."""
import json
import pkgutil

_CACHE = {}


def catalogue():
    if "doc" not in _CACHE:
        raw = pkgutil.get_data("servicetitan_cli", "data/endpoints.json")
        _CACHE["doc"] = json.loads(raw.decode("utf-8")) if raw else {"count": 0, "endpoints": []}
    return _CACHE["doc"]


def modules():
    seen = {}
    for e in catalogue()["endpoints"]:
        m = seen.setdefault(e["module"], {"module": e["module"], "title": e.get("title"), "endpoints": 0, "exports": 0})
        m["endpoints"] += 1
        m["exports"] += 1 if e.get("export") else 0
    return sorted(seen.values(), key=lambda m: m["module"])


def search(text=None, module=None, exports_only=False, include_fields=False, limit=None):
    text = (text or "").lower().strip()
    out = []
    for e in catalogue()["endpoints"]:
        if module and e["module"] != module and e["module"] != module + "-v2":
            continue
        if exports_only and not e.get("export"):
            continue
        hay = " ".join([e["path"], e.get("operation") or "", e.get("summary") or "", " ".join(e.get("query") or [])]).lower()
        if text and not all(tok in hay for tok in text.split()):
            continue
        item = {k: e.get(k) for k in ("module", "path", "operation", "summary", "kind", "export", "scope", "query")}
        if include_fields:
            item["fields"] = e.get("fields") or []
        out.append(item)
        if limit and len(out) >= limit:
            break
    return out


def describe(path_or_operation):
    key = (path_or_operation or "").strip().lower()
    for e in catalogue()["endpoints"]:
        if e["path"].lower() == key or (e.get("operation") or "").lower() == key or e["path"].lower().replace("/tenant/{tenant}", "") == key:
            return e
    return None


def scopes_for(paths):
    """{path: scope} for catalogue paths (missing paths map to None)."""
    by = {e["path"]: e.get("scope") for e in catalogue()["endpoints"]}
    return {p: by.get(p) for p in paths}


def missing_scopes(granted, paths):
    """Scopes needed by ``paths`` that a token's granted scope list lacks.

    ServiceTitan grants read (``:r``) and write (``:w``) scopes separately; a
    write scope does not imply read. Returns [] when ``granted`` is unknown.
    """
    if granted is None:
        return []
    have = set(granted)
    need = scopes_for(paths)
    return sorted({scope for scope in need.values() if scope and scope not in have})
