"""Catalogue building, diffing and the local refresh override."""
import copy
import json

from conftest import run_cli

from servicetitan_cli import catalogue, endpoints

FAKE_GROUPS = [{"displayName": "Other API", "apis": [
    {"id": "tenant-widgets-v2", "displayName": "Widgets", "description": "Widgets and gadgets"},
]}]
FAKE_SPEC = {
    "servers": [{"url": "https://api.servicetitan.io/widgets/v2"}, {"url": "https://api-integration.servicetitan.io/widgets/v2"}],
    "components": {"schemas": {"Status": {"type": "string", "enum": ["Open", "Closed"]},
                               "Paged": {"type": "object", "properties": {"page": {"type": "integer"}, "hasMore": {"type": "boolean"},
                                                                          "data": {"type": "array", "items": {"$ref": "#/components/schemas/Widget"}}}},
                               "Widget": {"type": "object", "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}}}},
    "paths": {
        "/tenant/{tenant}/widgets": {
            "get": {"operationId": "Widgets_GetList", "summary": "List widgets", "security": [{"apiKeyHeader": [], "oauth": ["tn.wdg.widgets:r"]}],
                    "parameters": [{"name": "tenant", "in": "path", "required": True, "schema": {"type": "integer"}},
                                   {"name": "status", "in": "query", "schema": {"type": "string", "oneOf": [{"$ref": "#/components/schemas/Status"}]},
                                    "description": "Filter by status"},
                                   {"name": "pageSize", "in": "query", "schema": {"type": "integer"}}],
                    "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Paged"}}}}}},
            "post": {"operationId": "Widgets_Create", "summary": "Create a widget", "security": [{"oauth": ["tn.wdg.widgets:w"]}],
                     "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Widget"}}}},
                     "responses": {"200": {}}},
        },
        "/tenant/{tenant}/widgets/{id}": {
            "get": {"operationId": "Widgets_Get", "summary": "Get a widget", "security": [{"oauth": ["tn.wdg.widgets:r"]}],
                    "parameters": [{"name": "tenant", "in": "path", "schema": {"type": "integer"}}, {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Widget"}}}}}},
        },
        "/tenant/{tenant}/export/widgets": {
            "get": {"operationId": "Export_Widgets", "summary": "Export feed", "tags": ["Widgets"], "security": [{"oauth": ["tn.wdg.widgets:r"]}],
                    "parameters": [{"name": "from", "in": "query", "schema": {"type": "string"}}], "responses": {"200": {}}},
        },
        "/tenant/{tenant}/widgets/legacy": {
            "get": {"operationId": "Widgets_Legacy", "summary": "Old list", "tags": ["Widgets (Deprecated)"], "security": [{"oauth": ["tn.wdg.widgets:r"]}],
                    "parameters": [], "responses": {"200": {}}},
        },
    },
}


def build_fake():
    return catalogue.build_catalogue(FAKE_GROUPS, lambda api_id: copy.deepcopy(FAKE_SPEC), generated_at="2026-01-01T00:00:00Z")


def test_build_catalogue_names_and_details():
    doc = build_fake()
    ops = {(o["resource"], o["operation"]): o for o in doc["operations"]}
    assert set(ops) == {("widgets", "list"), ("widgets", "create"), ("widgets", "get"), ("widgets", "export"), ("widgets", "legacy")}
    lst = ops[("widgets", "list")]
    assert lst["kind"] == "paged" and lst["fields"] == ["id", "name"] and lst["scope"] == "tn.wdg.widgets:r"
    status = [p for p in lst["params"] if p["name"] == "status"][0]
    assert status["enum"] == ["Open", "Closed"] and status["description"] == "Filter by status"
    assert [p["name"] for p in lst["params"] if p["in"] == "path"] == []
    assert ops[("widgets", "get")]["params"][0] == {"name": "id", "in": "path", "type": "integer", "required": True}
    assert ops[("widgets", "create")]["method"] == "POST" and ops[("widgets", "create")]["body"]["properties"] == {"id": "integer", "name": "string"}
    assert ops[("widgets", "export")]["export"] is True and ops[("widgets", "legacy")]["deprecated"] is True
    assert doc["modules"][0]["module"] == "widgets" and doc["modules"][0]["versions"] == ["v2"] and doc["modules"][0]["hash"]
    assert doc["count"] == 5 and doc["readCount"] == 4 and doc["generatedAt"] == "2026-01-01T00:00:00Z"


def test_diff_reports_added_removed_changed():
    old = build_fake()
    new = build_fake()
    new["operations"] = [o for o in new["operations"] if o["operation"] != "legacy"]
    lst = [o for o in new["operations"] if o["operation"] == "list"][0]
    lst["params"].append({"name": "createdOnOrAfter", "in": "query", "type": "string"})
    lst["fields"].append("createdOn")
    new["operations"].append(dict(lst, operation="count", path=lst["path"] + "/count"))
    new["modules"][0]["hash"] = "different"
    d = catalogue.diff(old, new)
    assert d["added"] == ["widgets widgets count"] and d["removed"] == ["widgets widgets legacy"]
    assert d["changed"][0]["operation"] == "widgets widgets list"
    assert any("new parameter createdOnOrAfter" in c for c in d["changed"][0]["changes"])
    assert any("new response fields" in c for c in d["changed"][0]["changes"])
    assert d["modules"]["changed"] == ["widgets"] and "1 added, 1 removed, 1 changed" in d["summary"]


def test_local_catalogue_overrides_packaged(isolated_home):
    endpoints.reload()
    try:
        assert endpoints.info()["origin"] == "packaged" and endpoints.info()["operations"] >= 590
        path = catalogue.save_local(build_fake())
        endpoints.reload()
        assert endpoints.info() == {"origin": "local", "path": path, "generatedAt": "2026-01-01T00:00:00Z", "operations": 5,
                                    "reads": 4, "modules": [{"module": "widgets", "versions": ["v2"], "hash": endpoints.catalogue()["modules"][0]["hash"]}]}
        assert endpoints.resolve_module("widgets") == "widgets"
        assert catalogue.remove_local() is True and catalogue.remove_local() is False
    finally:
        catalogue.remove_local()
        endpoints.reload()
    assert endpoints.info()["origin"] == "packaged"


def test_cli_catalogue_commands(capsys, isolated_home, monkeypatch):
    endpoints.reload()
    try:
        code, out, _ = run_cli(capsys, "api", "catalogue", "status")
        assert code == 0 and out["active"]["origin"] == "packaged" and out["localPresent"] is False
        fresh = copy.deepcopy(endpoints.packaged())
        fresh["generatedAt"] = "2099-01-01T00:00:00Z"
        fresh["operations"] = [o for o in fresh["operations"] if not (o["module"] == "jpm" and o["operation"] == "hold-reasons")]
        fresh["count"] = len(fresh["operations"])
        monkeypatch.setattr(catalogue, "build_from_portal", lambda progress=None: copy.deepcopy(fresh))
        code, out, _ = run_cli(capsys, "api", "catalogue", "refresh", "--dry-run")
        assert code == 0 and out["dryRun"] is True and "jpm jobs hold-reasons" in out["diff"]["removed"] and "saved" not in out
        assert endpoints.info()["origin"] == "packaged"
        code, out, _ = run_cli(capsys, "api", "catalogue", "refresh")
        assert code == 0 and out["saved"] and out["active"]["origin"] == "local" and out["active"]["generatedAt"] == "2099-01-01T00:00:00Z"
        code, out, _ = run_cli(capsys, "api", "catalogue", "status")
        assert code == 0 and out["active"]["origin"] == "local" and "1 removed" in out["diffFromPackaged"]
        code, out, _ = run_cli(capsys, "api", "jpm", "jobs")
        assert code == 0 and not any(o["operation"] == "hold-reasons" for o in out["operations"])
        code, out, _ = run_cli(capsys, "api", "catalogue", "reset")
        assert code == 0 and out["removed"] is True and out["active"]["origin"] == "packaged"
        code, out, _ = run_cli(capsys, "api", "telecom", "calls", "list-v2", "--describe")
        assert code == 0 and out["deprecated"] is True and out["replacedBy"] == ["list"]
        code, out, _ = run_cli(capsys, "api", "modules")
        assert code == 0 and [m for m in out["modules"] if m["module"] == "telecom"][0]["versions"] == ["v2", "v3"]
    finally:
        catalogue.remove_local()
        endpoints.reload()


def test_operation_names_are_lowercase_and_resolvable():
    from servicetitan_cli import endpoints
    from servicetitan_cli.catalogue import kebab
    assert kebab("equipmentId") == "equipment-id" and kebab("report_category") == "report-category" and kebab("id") == "id"
    doc = endpoints.packaged()
    assert all(o["operation"] == o["operation"].lower() for o in doc["operations"])
    for o in doc["operations"]:      # every listed name resolves back to its own operation
        assert endpoints.resolve_operation(o["module"], o["resource"], o["operation"])["path"] == o["path"]
    op = endpoints.resolve_operation("jpm", "jobs", "delete-equipment-by-id-equipment-id")
    assert op["method"] == "DELETE" and op["path"].endswith("/jobs/{id}/equipment/{equipmentId}")
    assert endpoints.resolve_operation("crm", "contacts", "contact-methods-by-contact-id")["method"] == "GET"
