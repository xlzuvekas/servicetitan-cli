"""The local semantic index: documents, saved results, and the build/query flow (hash embeddings, no downloads)."""
import json
import os

import pytest

from conftest import FIXED_NOW, run_cli
from servicetitan_cli import indexing
from servicetitan_cli.dates import get_tz
from servicetitan_cli.snapshot import Snapshot

HAS_LLAMA = indexing.available()[0]


def test_snapshot_documents_cover_every_kind(demo_data):
    snap = Snapshot(demo_data)
    docs = indexing.snapshot_documents(snap, FIXED_NOW, get_tz("America/Denver"))
    kinds = {}
    for d in docs:
        kinds.setdefault(d["metadata"]["kind"], []).append(d)
    for kind in ("jobs", "opportunities", "invoices", "purchase-orders", "calls", "memberships", "technicians", "campaigns",
                 "business-units", "people"):
        assert kinds.get(kind), kind
    assert len({d["id"] for d in docs}) == len(docs)                       # ids are unique
    job = kinds["jobs"][0]
    assert job["id"].startswith("snapshot:jobs:") and job["text"].startswith("Job ") and "Revenue: $" in job["text"]
    assert job["metadata"]["tenant"] in ("SPR", "LOC2") and len(job["metadata"]["date"]) == 10 and job["metadata"]["number"]
    inv = kinds["invoices"][0]
    assert "balance $" in inv["text"] and "days past due" in inv["text"] and inv["metadata"]["name"]
    call = kinds["calls"][0]
    assert call["text"].split(" ")[0] in ("Inbound", "Outbound") and call["id"].count(":") >= 4
    assert all(isinstance(v, (str, int, float)) for d in docs for v in d["metadata"].values())


def test_catalogue_definition_and_result_documents(tmp_path):
    api = indexing.catalogue_documents()
    assert len(api) == 600 and all(d["metadata"]["kind"] == "api" for d in api)
    jobs_list = next(d for d in api if d["id"] == "api:jpm/jobs/list")
    assert "servicetitan api jpm jobs list" in jobs_list["text"] and "Read operation." in jobs_list["text"]
    assert jobs_list["metadata"]["scope"] if "scope" in jobs_list["metadata"] else True
    notes = next(d for d in api if d["id"] == "api:jpm/jobs/create-notes")
    assert "Write operation (needs --write)." in notes["text"] and "Body fields: text" in notes["text"]
    defs = indexing.definition_documents()
    assert len(defs) > 20 and any(d["id"] == "definition:service.avgTicket" for d in defs)
    env = {"savedAt": "2026-09-16T10:00:00Z", "profile": "acme", "command": ["list", "invoices", "--min-days-past-due", "60"],
           "label": "overdue", "result": {"kind": "invoices", "tenant": "all", "total": 2,
                                          "records": [{"id": "SPR:1", "customer": "Jane Doe", "balance": 120.5, "tenant": "SPR"},
                                                      {"id": "SPR:2", "customer": "Bob Roe", "balance": 99, "tenant": "SPR"}]}}
    docs = indexing.result_documents(env, str(tmp_path / "20260916T100000Z-list-invoices-abcd1234.json"))
    assert len(docs) == 3 and docs[0]["id"] == "result:20260916T100000Z-list-invoices-abcd1234"
    assert "overdue" in docs[0]["text"] and "2 records." in docs[0]["text"] and docs[0]["metadata"]["label"] == "overdue"
    assert docs[1]["metadata"]["recordId"] == "SPR:1" and docs[1]["metadata"]["tenant"] == "SPR" and "Jane Doe" in docs[1]["text"]
    assert indexing.flatten_text({"a": {"b": 1}, "c": [1, 2], "d": [{"e": "x"}]}) == "a.b: 1; c: 1, 2; d.0.e: x"


def test_status_and_errors_without_the_extra(capsys, isolated_home, monkeypatch, demo_path):
    monkeypatch.setattr(indexing, "available", lambda: (False, "No module named 'llama_index'"))
    code, out, _ = run_cli(capsys, "index", "status", "--data", demo_path)
    assert code == 0 and out["exists"] is False and out["llamaIndex"]["available"] is False and "pip install" in out["next"]
    code, out, err = run_cli(capsys, "index", "build", "--data", demo_path)
    assert code == 3 and json.loads(err)["error"]["code"] == "INDEX_UNAVAILABLE"
    code, out, err = run_cli(capsys, "index", "query", "anything", "--data", demo_path)
    assert code == 3 and json.loads(err)["error"]["code"] == "INDEX_UNAVAILABLE"
    # --save still keeps the result on disk
    code, out, _ = run_cli(capsys, "list", "jobs", "--data", demo_path, "--limit", "2", "--save", "--save-label", "two jobs")
    assert code == 0 and out["saved"]["indexed"] is False and os.path.exists(out["saved"]["path"]) and "install" in out["saved"]["note"]
    with open(out["saved"]["path"], encoding="utf-8") as f:
        env = json.load(f)
    assert env["label"] == "two jobs" and "--save" not in env["command"] and env["result"]["kind"] == "jobs"
    code, out, _ = run_cli(capsys, "index", "results", "--data", demo_path)
    assert out["count"] == 1 and out["results"][0]["label"] == "two jobs" and out["results"][0]["command"].startswith("servicetitan list jobs")
    code, out, _ = run_cli(capsys, "index", "results", "--data", demo_path, "--show", out["results"][0]["file"][:15])
    assert code == 0 and out["result"]["kind"] == "jobs"


@pytest.mark.skipif(not HAS_LLAMA, reason="llama-index-core not installed")
def test_build_query_and_save_end_to_end(capsys, isolated_home, monkeypatch, demo_path):
    monkeypatch.setenv("SERVICETITAN_EMBED", "hash:256")
    code, out, err = run_cli(capsys, "index", "build", "--data", demo_path, "--sources", "snapshot,catalogue,definitions")
    assert code == 0, err
    assert out["profile"] == "default" and out["rebuilt"] is True and out["embed"] == "hash:256"
    assert out["sources"]["snapshot"]["documents"] > 100 and out["sources"]["catalogue"]["documents"] == 600
    assert out["added"] == out["documents"] > 700 and out["path"].startswith(str(isolated_home))
    if os.name != "nt":
        import stat
        assert stat.S_IMODE(os.stat(out["path"]).st_mode) == 0o700
    code, out, _ = run_cli(capsys, "index", "status", "--data", demo_path)
    assert out["exists"] is True and out["embed"] == "hash:256" and out["snapshot"]["changed"] is False and out["stale"] is False
    # records: filtered by kind, ranked, with a follow-up command
    code, out, err = run_cli(capsys, "index", "query", "invoice balance past due", "--data", demo_path, "--kind", "invoices", "--top", "5")
    assert code == 0, err
    assert out["count"] == 5 and all(h["kind"] == "invoices" for h in out["hits"]) and out["hits"][0]["score"] is not None
    assert out["hits"][0]["next"].startswith("servicetitan list invoices") and out["indexes"][0]["profile"] == "default"
    code, out, _ = run_cli(capsys, "index", "query", "membership", "--data", demo_path, "--tenant", "LOC2", "--top", "3")
    assert all(h["tenant"] == "LOC2" for h in out["hits"]) and out["filters"]["tenant"] == "LOC2"
    # the API catalogue is searchable
    code, out, _ = run_cli(capsys, "index", "query", "delete equipment by id", "--data", demo_path, "--kind", "api", "--top", "3")
    assert any(h["id"].startswith("jpm/jobs/delete-equipment") for h in out["hits"]) and out["hits"][0]["next"].endswith("--describe")
    assert all(h["kind"] == "api" for h in out["hits"])
    code, out, err = run_cli(capsys, "index", "query", "x", "--data", demo_path, "--kind", "nope")
    assert code == 2
    # a second build is incremental and finds nothing to do
    code, out, _ = run_cli(capsys, "index", "build", "--data", demo_path, "--sources", "snapshot,catalogue,definitions")
    assert out["rebuilt"] is False and out["added"] == 0 and out["updated"] == 0 and out["removed"] == 0 and out["unchanged"] > 700
    # --save stores the result and indexes it at once
    code, out, err = run_cli(capsys, "list", "invoices", "--data", demo_path, "--min-days-past-due", "60", "--limit", "5",
                             "--save", "--save-label", "overdue sixty")
    assert code == 0, err
    assert out["saved"]["indexed"] is True and out["saved"]["documents"] == 6 and os.path.exists(out["saved"]["path"])
    assert os.path.basename(out["saved"]["path"]).split("-")[1:4] == ["list", "invoices"] + [os.path.basename(out["saved"]["path"]).split("-")[3]]
    assert "/" not in os.path.basename(out["saved"]["path"]).split("-", 1)[1].rsplit("-", 1)[0]
    code, out, _ = run_cli(capsys, "index", "results", "--data", demo_path)
    assert out["count"] == 1 and out["results"][0]["label"] == "overdue sixty"
    code, out, _ = run_cli(capsys, "index", "query", "overdue sixty", "--data", demo_path, "--kind", "result", "--top", "3")
    assert out["count"] >= 1 and out["hits"][0]["kind"] == "result" and out["hits"][0]["next"].startswith("servicetitan index results --show")
    code, out, _ = run_cli(capsys, "index", "status", "--data", demo_path)
    assert out["results"]["saved"] == 1 and out["documents"] > 706
    # changing the embedding model needs --rebuild
    monkeypatch.setenv("SERVICETITAN_EMBED", "hash:32")
    code, out, err = run_cli(capsys, "index", "build", "--data", demo_path)
    assert code == 3 and json.loads(err)["error"]["code"] == "INDEX_EMBED_MISMATCH"
    code, out, err = run_cli(capsys, "index", "build", "--data", demo_path, "--rebuild", "--sources", "snapshot,results")
    assert code == 0, err
    assert out["embed"] == "hash:32" and out["rebuilt"] is True and out["sources"]["results"]["files"] == 1
    # csv output of hits
    code, out, _ = run_cli(capsys, "index", "query", "invoice", "--data", demo_path, "--kind", "invoices", "--top", "2", "--format", "csv")
    assert code == 0 and out.splitlines()[0].startswith("score,kind,id") and len(out.splitlines()) == 3
