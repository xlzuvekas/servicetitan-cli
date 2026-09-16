"""Local semantic index over everything the CLI has stored, built with LlamaIndex.

Sources (``servicetitan index build --sources ...``):

* ``snapshot``     every record of the profile's snapshot (jobs, invoices,
                   opportunities, purchase orders, calls, memberships,
                   technicians, campaigns, business units, people), one short
                   document each, names resolved the way ``list`` shows them;
* ``results``      every command result saved with ``--save`` (live API answers
                   included): one document per record plus one for the summary;
* ``catalogue``    every API operation, so "which operation gives me X" is a
                   semantic search rather than a keyword hunt;
* ``definitions``  the metric definitions;
* ``docs``         markdown files given with ``--docs`` (or the repository's
                   docs/ directory when the package runs from a checkout).

Everything stays on this machine. Embeddings are computed locally (fastembed's
ONNX models by default, cached under ``<config dir>/index/models``) and the
index is plain JSON under ``<config dir>/index/<profile>/``, owner-only. No
language model runs here: ``index query`` returns the closest documents and the
calling agent does the reasoning. Snapshots hold customer names and revenue, so
a hosted embedding service is never used unless a person configures one
explicitly (``huggingface:`` and ``ollama:`` run locally too; ``hash`` needs no
download at all and is crude).

LlamaIndex is an optional extra: ``pip install "servicetitan-cli[index]"``.
Saving results with ``--save`` needs nothing extra; they land on disk either way.
"""
import contextlib
import datetime as dt
import glob
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
import types

from . import __version__
from .dates import UTC, iso_z
from .errors import CliError, ConfigError, DataError, UsageError
from .output import progress
from .paths import config_dir, ensure_dir, write_private

DEFAULT_EMBED = "fastembed:BAAI/bge-small-en-v1.5"
SOURCES = ("snapshot", "results", "catalogue", "definitions", "docs")
RECORD_KINDS = ("jobs", "opportunities", "invoices", "purchase-orders", "calls", "memberships", "technicians",
                "campaigns", "business-units", "people")
KINDS = RECORD_KINDS + ("result", "api", "definition", "doc")
PREFIX = {"snapshot": "snapshot:", "results": "result:", "catalogue": "api:", "definitions": "definition:", "docs": "doc:"}
CHUNK_WORDS = 220
CHUNK_OVERLAP = 30
MAX_RESULT_RECORDS = 5000      # per saved result; beyond that only the summary document is indexed
MANIFEST = "manifest.json"


# ---------------------------------------------------------------- locations
def index_root():
    return os.path.join(config_dir(), "index")


def models_dir():
    return os.path.join(index_root(), "models")


def results_root():
    return os.path.join(config_dir(), "results")


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "default")).strip("._") or "default"


def index_dir(name):
    return os.path.join(index_root(), safe_name(name))


def results_dir(name):
    return os.path.join(results_root(), safe_name(name))


def _settings(ctx):
    """Profile settings, or the same defaults `list`/`metrics` use when an explicit --data file has no profile."""
    return ctx.config_or_default() if ctx.explicit_data_path else ctx.config


def index_name(ctx):
    """One index per profile ("default" for a --data file queried without a profile, like the other commands)."""
    try:
        return str(_settings(ctx).get("profile") or "default")
    except ConfigError:
        return "default"


# ---------------------------------------------------------------- availability
def available():
    try:
        import llama_index.core  # noqa: F401
    except ImportError as e:
        return False, str(e)
    return True, None


def require():
    ok, why = available()
    if not ok:
        raise ConfigError("Semantic indexing needs the optional LlamaIndex extra (%s)" % why, code="INDEX_UNAVAILABLE",
                          hint='pip install "servicetitan-cli[index]"  (Python 3.10 to 3.12 for the default local model; on '
                               '3.13 install llama-index-core plus llama-index-embeddings-huggingface and pass --embed '
                               'huggingface). Results saved with --save stay on disk either way.')


def _li():
    """LlamaIndex handles, imported lazily; anything it prints on import goes to stderr."""
    require()
    with contextlib.redirect_stdout(sys.stderr):
        from llama_index.core import Document, StorageContext, VectorStoreIndex, load_index_from_storage
        from llama_index.core.node_parser import SentenceSplitter
        from llama_index.core.schema import MetadataMode
        from llama_index.core.vector_stores import FilterOperator, MetadataFilter, MetadataFilters
    return types.SimpleNamespace(Document=Document, StorageContext=StorageContext, VectorStoreIndex=VectorStoreIndex,
                                 load_index_from_storage=load_index_from_storage, SentenceSplitter=SentenceSplitter,
                                 MetadataMode=MetadataMode, FilterOperator=FilterOperator, MetadataFilter=MetadataFilter,
                                 MetadataFilters=MetadataFilters)


# ---------------------------------------------------------------- embeddings
def embed_spec(explicit=None, environ=None):
    environ = os.environ if environ is None else environ
    return (explicit or environ.get("SERVICETITAN_EMBED") or DEFAULT_EMBED).strip()


def _hash_embedding_class():
    from llama_index.core.embeddings import BaseEmbedding

    class HashEmbedding(BaseEmbedding):
        """Bag-of-words hashing. No download, deterministic, crude: for tests and air-gapped machines."""
        dim: int = 64

        def __init__(self, dim=64, **kw):
            super().__init__(model_name="hash-%d" % dim, dim=dim, **kw)

        @classmethod
        def class_name(cls):
            return "HashEmbedding"

        def _vec(self, text):
            v = [0.0] * self.dim
            for tok in re.findall(r"[a-z0-9]+", (text or "").lower()):
                v[int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:8], 16) % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            return [x / n for x in v]

        def _get_query_embedding(self, query):
            return self._vec(query)

        def _get_text_embedding(self, text):
            return self._vec(text)

        async def _aget_query_embedding(self, query):
            return self._vec(query)

        async def _aget_text_embedding(self, text):
            return self._vec(text)

    return HashEmbedding


def make_embed_model(spec):
    backend, _, model = spec.partition(":")
    backend = backend.strip().lower()
    hints = {"fastembed": 'pip install "servicetitan-cli[index]"',
             "huggingface": "pip install llama-index-embeddings-huggingface",
             "ollama": "pip install llama-index-embeddings-ollama, and run Ollama with the model pulled"}
    try:
        if backend in ("hash", "mock"):
            return _hash_embedding_class()(dim=int(model or 64))
        if backend == "fastembed":
            from llama_index.embeddings.fastembed import FastEmbedEmbedding
            ensure_dir(models_dir())
            return FastEmbedEmbedding(model_name=model or DEFAULT_EMBED.split(":", 1)[1], cache_dir=models_dir())
        if backend == "huggingface":
            from llama_index.embeddings.huggingface import HuggingFaceEmbedding
            ensure_dir(models_dir())
            return HuggingFaceEmbedding(model_name=model or "BAAI/bge-small-en-v1.5", cache_folder=models_dir())
        if backend == "ollama":
            from llama_index.embeddings.ollama import OllamaEmbedding
            return OllamaEmbedding(model_name=model or "nomic-embed-text")
    except ImportError as e:
        raise ConfigError("Embedding backend %r is not installed (%s)" % (backend, e), code="INDEX_UNAVAILABLE",
                          hint=hints.get(backend, ""))
    raise UsageError("Unknown embedding backend %r" % backend,
                     hint="Use fastembed[:MODEL] (default, local ONNX), huggingface:MODEL, ollama:MODEL, or hash (no download).")


# ---------------------------------------------------------------- documents
def money(v):
    try:
        return "${:,.2f}".format(float(v))
    except (TypeError, ValueError):
        return "-"


def _join(parts):
    return " ".join(p for p in parts if p)


def _when(v):
    return str(v)[:10] if v else None


def render_record(kind, r):
    """One readable sentence per record; this is the text that gets embedded."""
    where = "(%s)" % r["tenant"] if r.get("tenant") else ""
    if kind == "jobs":
        flags = [x for x, on in (("booked", r.get("booked")), ("low ticket", r.get("lowTicket")),
                                 ("membership sold", r.get("membershipSold")), ("no charge", r.get("noCharge"))) if on]
        return _join(["Job %s %s: %s on %s." % (r.get("jobNumber") or r.get("id"), where, r.get("status"), _when(r.get("date"))),
                      "Customer: %s." % r["customer"] if r.get("customer") else None,
                      "Technician: %s." % r["technician"] if r.get("technician") else None,
                      "Department: %s." % r["dept"] if r.get("dept") else None,
                      "Business unit: %s." % r["businessUnit"] if r.get("businessUnit") else None,
                      "Job type: %s." % r["jobType"] if r.get("jobType") else None,
                      "Campaign: %s." % r["campaign"] if r.get("campaign") else None,
                      "Revenue: %s." % money(r.get("revenue")),
                      "Options presented: %s." % r["options"] if r.get("options") else None,
                      "Flags: %s." % ", ".join(flags) if flags else None])
    if kind == "opportunities":
        return _join(["Opportunity %s for %s %s: %s." % (r.get("title") or r.get("id"), r.get("customer") or "unknown customer",
                                                          where, "won" if r.get("won") else "open"),
                      "Presented %s." % _when(r.get("presentedOn")) if r.get("presentedOn") else None,
                      "Won %s." % _when(r.get("wonOn")) if r.get("wonOn") else None,
                      "Sales rep: %s." % r["rep"] if r.get("rep") else None,
                      "Department: %s." % r["dept"] if r.get("dept") else None,
                      "City: %s." % r["city"] if r.get("city") else None,
                      "Job %s." % r["jobNumber"] if r.get("jobNumber") else None,
                      "Value: %s, best option %s, %s option(s)." % (money(r.get("value")), money(r.get("bestOption")), r.get("options") or 0)])
    if kind == "invoices":
        return _join(["Invoice %s %s for %s: balance %s of %s." % (r.get("invoiceNumber") or r.get("id"), where,
                                                                    r.get("customer") or "unknown customer", money(r.get("balance")), money(r.get("total"))),
                      "Invoice date %s." % _when(r.get("invoiceDate")) if r.get("invoiceDate") else None,
                      "Due %s." % _when(r.get("dueDate")) if r.get("dueDate") else None,
                      "%s days past due (%s)." % (r.get("daysPastDue"), r.get("bucket")) if r.get("daysPastDue") is not None else None,
                      "Business unit: %s." % r["businessUnit"] if r.get("businessUnit") else None])
    if kind == "purchase-orders":
        return _join(["Purchase order %s %s with vendor %s: status %s, total %s." % (r.get("number") or r.get("id"), where,
                                                                                     r.get("vendor") or "unknown", r.get("status"), money(r.get("total"))),
                      "Date %s." % _when(r.get("date")) if r.get("date") else None])
    if kind == "calls":
        return _join(["%s calls on %s %s: %s call(s), %s booked, %s abandoned." % ("Inbound" if r.get("inbound") else "Outbound",
                                                                                   _when(r.get("date")), where, r.get("calls"), r.get("booked"), r.get("abandoned")),
                      "Agent: %s." % r["agent"] if r.get("agent") else None,
                      "Campaign: %s." % r["campaign"] if r.get("campaign") else None])
    if kind == "memberships":
        return _join(["Membership %s %s for %s: status %s." % (r.get("plan") or "plan", where, r.get("customer") or "unknown customer", r.get("status")),
                      "From %s to %s." % (_when(r.get("from")), _when(r.get("to"))) if r.get("from") or r.get("to") else None,
                      "Renews in %s days." % r["daysToRenewal"] if r.get("daysToRenewal") is not None else None,
                      "Sold by %s." % r["soldBy"] if r.get("soldBy") else None])
    if kind == "technicians":
        return _join(["Technician %s %s." % (r.get("name"), where),
                      "Department: %s." % r["dept"] if r.get("dept") else None,
                      "Active." if r.get("active", True) else "Inactive."])
    if kind == "campaigns":
        return _join(["Marketing campaign %s %s." % (r.get("name"), where),
                      "Category: %s." % r["category"] if r.get("category") else None,
                      "Active." if r.get("active", True) else "Inactive."])
    if kind == "business-units":
        return _join(["Business unit %s %s." % (r.get("name"), where),
                      "Department: %s." % r["dept"] if r.get("dept") else None])
    if kind == "people":
        return "Person %s %s, id %s." % (r.get("name"), where, r.get("id"))
    return flatten_text(r)


def _record_id(kind, r, seen):
    base = r.get("id") or r.get("invoiceNumber") or r.get("number")
    if kind == "calls" or not base:
        base = ":".join(str(x) for x in (r.get("tenant"), _when(r.get("date")), r.get("agent"), r.get("campaign"),
                                          "in" if r.get("inbound") else "out"))
    base = str(base)
    n = seen.get(base, 0) + 1
    seen[base] = n
    return base if n == 1 else "%s#%d" % (base, n)


def snapshot_documents(snap, as_of, tz, tenant=None):
    """One document per snapshot record, using the same rows `list` produces."""
    from .listing import _rows
    from .metrics import Query
    q = Query(snap, as_of, tz, range_kind="ytd", tenant=tenant)
    docs = []
    for kind in RECORD_KINDS:
        seen = {}
        for r in _rows(q, kind, {}):
            r.pop("_dt", None)
            rid = _record_id(kind, r, seen)
            meta = {"kind": kind, "id": rid}
            if r.get("tenant"):
                meta["tenant"] = r["tenant"]
            date = _when(r.get("date") or r.get("presentedOn") or r.get("invoiceDate") or r.get("from"))
            if date:
                meta["date"] = date
            name = r.get("customer") or r.get("name") or r.get("vendor") or r.get("agent")
            if name:
                meta["name"] = str(name)
            number = r.get("jobNumber") or r.get("invoiceNumber") or r.get("number")
            if number:
                meta["number"] = str(number)
            docs.append({"id": "snapshot:%s:%s" % (kind, rid), "text": render_record(kind, r), "metadata": meta})
    return docs


def catalogue_documents():
    from . import endpoints
    docs = []
    for op in endpoints.catalogue()["operations"]:
        key = "%s/%s/%s" % (op["module"], op["resource"], op["operation"])
        params = ", ".join("%s (%s)%s" % (p["name"], p.get("type") or "string",
                                           (": " + str(p.get("description") or p.get("help") or "")) if (p.get("description") or p.get("help")) else "")
                           for p in op.get("params", []) if p.get("in") == "query")
        body = op.get("body") or {}
        text = _join(["API operation `servicetitan api %s %s %s`: %s %s." % (op["module"], op["resource"], op["operation"], op["method"], op["path"]),
                      (op.get("summary") or "").rstrip(".") + "." if op.get("summary") else None,
                      op.get("description"),
                      "Read operation." if op["method"] == "GET" else "Write operation (needs --write).",
                      "Scope: %s." % op["scope"] if op.get("scope") else None,
                      "Deprecated." if op.get("deprecated") else None,
                      "Query parameters: %s." % params[:1500] if params else None,
                      "Body fields: %s." % ", ".join(list(body.get("properties", {}).keys())[:40]) if body.get("properties") else None,
                      "Response fields: %s." % ", ".join((op.get("fields") or [])[:40]) if op.get("fields") else None])
        docs.append({"id": "api:%s" % key, "text": text,
                     "metadata": {"kind": "api", "id": key, "name": "%s %s %s" % (op["module"], op["resource"], op["operation"]),
                                  "module": op["module"], "resource": op["resource"], "operation": op["operation"],
                                  "method": op["method"]}})
    return docs


def definition_documents():
    from .metrics import DEFINITIONS
    docs = []
    for area, items in DEFINITIONS.items():
        for name, text in (items or {}).items():
            if not isinstance(text, str):
                text = json.dumps(text, ensure_ascii=False)
            docs.append({"id": "definition:%s.%s" % (area, name), "text": "Metric %s.%s: %s" % (area, name, text),
                         "metadata": {"kind": "definition", "id": "%s.%s" % (area, name), "name": "%s.%s" % (area, name), "area": area}})
    return docs


def default_docs_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.abspath(os.path.join(here, "..", "..", "docs"))
    return cand if os.path.isfile(os.path.join(cand, "servicetitan-api.md")) else None


def doc_documents(paths):
    docs = []
    for p in paths or []:
        if os.path.isdir(p):
            files = [(os.path.relpath(f, p), f) for f in sorted(glob.glob(os.path.join(p, "**", "*.md"), recursive=True))]
        elif os.path.isfile(p):
            files = [(os.path.basename(p), p)]
        else:
            raise UsageError("No such file or directory: %s" % p)
        for rel, f in files:
            with open(f, encoding="utf-8") as fh:
                text = fh.read()
            title = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")), rel)
            docs.append({"id": "doc:%s" % rel.replace(os.sep, "/"), "text": text,
                         "metadata": {"kind": "doc", "id": rel.replace(os.sep, "/"), "name": title, "path": os.path.abspath(f)}})
    return docs


def flatten_text(value, limit=1500):
    """`a.b: 1; c: x` rendering of a nested result, capped."""
    parts = []

    def walk(v, pre):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, pre + str(k) + ".")
        elif isinstance(v, list):
            if all(not isinstance(x, (dict, list)) for x in v):
                parts.append("%s: %s" % (pre.rstrip("."), ", ".join(str(x) for x in v[:20])))
            else:
                for i, x in enumerate(v[:20]):
                    walk(x, "%s%d." % (pre, i))
        else:
            parts.append("%s: %s" % (pre.rstrip("."), v))
    walk(value, "")
    return "; ".join(parts)[:limit]


def _find_rows(res):
    if isinstance(res, list) and res and all(isinstance(x, dict) for x in res):
        return "items", res
    if isinstance(res, dict):
        for key in ("records", "data", "hits", "entries", "endpoints", "operations", "results", "tenants"):
            v = res.get(key)
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                return key, v
    return None, []


def result_documents(envelope, path):
    """Documents for one saved result: a summary plus one per record."""
    res = envelope.get("result")
    cmd = "servicetitan " + " ".join(envelope.get("command") or [])
    rid = os.path.basename(path)[:-5] if path.endswith(".json") else os.path.basename(path)
    base = {"kind": "result", "savedAt": envelope.get("savedAt"), "command": cmd, "file": os.path.basename(path)}
    if envelope.get("label"):
        base["label"] = envelope["label"]
    rows_key, rows = _find_rows(res)
    summary = {k: v for k, v in res.items() if k != rows_key} if isinstance(res, dict) else {"value": res}
    tenant = res.get("tenant") if isinstance(res, dict) and isinstance(res.get("tenant"), str) else None
    docs = [{"id": "result:%s" % rid,
             "text": _join(["Saved result of `%s` on %s%s." % (cmd, envelope.get("savedAt"), (" (%s)" % envelope["label"]) if envelope.get("label") else ""),
                            "%d %s." % (len(rows), rows_key) if rows else None, flatten_text(summary, limit=3000)]),
             "metadata": dict(base, id=rid, name=envelope.get("label") or cmd, **({"tenant": tenant} if tenant else {}))}]
    for i, row in enumerate(rows[:MAX_RESULT_RECORDS]):
        rec = str(row.get("id") or row.get("jobNumber") or row.get("number") or row.get("name") or i)
        meta = dict(base, id="%s#%s" % (rid, rec), recordId=rec,
                    name=str(row.get("name") or row.get("customer") or row.get("jobNumber") or rec))
        if isinstance(row.get("tenant"), str):
            meta["tenant"] = row["tenant"]
        elif tenant:
            meta["tenant"] = tenant
        docs.append({"id": "result:%s:%d" % (rid, i), "text": _join(["From `%s`:" % cmd, flatten_text(row)]), "metadata": meta})
    return docs


def results_documents(name):
    d = results_dir(name)
    files = sorted(glob.glob(os.path.join(d, "*.json"))) if os.path.isdir(d) else []
    docs = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                env = json.load(fh)
        except (OSError, ValueError):
            continue
        docs.extend(result_documents(env, f))
    return docs, files


# ---------------------------------------------------------------- saved results
def _strip_save_flags(argv):
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a == "--save":
            continue
        if a == "--save-label":
            skip = True
            continue
        if a.startswith("--save-label="):
            continue
        out.append(a)
    return out


def save_result(ctx, argv, result, label=None, quiet=False, words=None):
    """Write a command result under <config dir>/results/<profile>/ and add it to the index when one exists.

    ``words`` (the command and its subcommand words, from the parser) name the file; the full command line is
    kept inside it.
    """
    name = index_name(ctx)
    d = results_dir(name)
    ensure_dir(d)
    stamp = dt.datetime.now(UTC)
    argv = _strip_save_flags(list(argv))
    words = list(words or [a for a in argv if not a.startswith("-")])[:6]
    slug = safe_name("-".join(str(w) for w in words))[:60] or "result"
    digest = hashlib.sha1(json.dumps(result, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:8]
    path = os.path.join(d, "%s-%s-%s.json" % (stamp.strftime("%Y%m%dT%H%M%SZ"), slug, digest))
    envelope = {"savedAt": iso_z(stamp), "profile": name, "command": argv, "label": label, "cliVersion": __version__,
                "result": result}
    write_private(path, json.dumps(envelope, default=str, ensure_ascii=False))
    rec = {"path": path, "savedAt": envelope["savedAt"], "label": label, "indexed": False}
    ok, why = available()
    if not ok:
        rec["note"] = "saved to disk; install the index extra to make saved results searchable"
        return rec
    if not read_manifest(index_dir(name)):
        rec["note"] = "saved to disk; run `servicetitan index build` to index it (no index yet for %r)" % name
        return rec
    try:
        added = add_documents(ctx, result_documents(envelope, path), quiet=quiet)
        rec.update({"indexed": True, "documents": added})
    except CliError as e:
        rec["note"] = "saved to disk; indexing failed: %s" % e.message
    return rec


def list_results(ctx, limit=50, show=None):
    name = index_name(ctx)
    d = results_dir(name)
    files = sorted(glob.glob(os.path.join(d, "*.json"))) if os.path.isdir(d) else []
    if show:
        match = [f for f in files if os.path.basename(f) == show or os.path.basename(f).startswith(show)]
        if not match:
            raise DataError("No saved result matching %r" % show, hint="servicetitan index results  lists them")
        with open(match[-1], encoding="utf-8") as fh:
            env = json.load(fh)
        env["path"] = match[-1]
        env["source"] = "results"
        return env
    rows = []
    for f in files[-limit:] if limit else files:
        try:
            with open(f, encoding="utf-8") as fh:
                env = json.load(fh)
        except (OSError, ValueError):
            continue
        rows.append({"file": os.path.basename(f), "savedAt": env.get("savedAt"), "label": env.get("label"),
                     "command": "servicetitan " + " ".join(env.get("command") or []), "bytes": os.path.getsize(f)})
    return {"source": "results", "profile": name, "path": d, "count": len(files), "results": rows,
            "next": "servicetitan index results --show FILE  |  servicetitan index query \"...\" --kind result"}


# ---------------------------------------------------------------- build / update
def read_manifest(path):
    try:
        with open(os.path.join(path, MANIFEST), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _lock_down(path):
    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o700)
        for f in os.listdir(path):
            fp = os.path.join(path, f)
            if os.path.isfile(fp):
                os.chmod(fp, 0o600)
    except OSError:
        pass


def _to_documents(li, docs):
    out = []
    for d in docs:
        keys = [k for k in d["metadata"] if k != "name"]
        out.append(li.Document(text=d["text"], doc_id=d["id"], metadata=d["metadata"],
                               excluded_embed_metadata_keys=keys, excluded_llm_metadata_keys=keys))
    return out


def _splitter(li):
    return li.SentenceSplitter(chunk_size=CHUNK_WORDS, chunk_overlap=CHUNK_OVERLAP, tokenizer=lambda t: t.split())


def collect(ctx, sources, docs_paths=None, snapshot_path=None):
    """Gather documents for the requested sources. Returns (docs, report)."""
    docs, report = [], {}
    name = index_name(ctx)
    if "snapshot" in sources:
        from .snapshot import load_snapshot
        try:
            _settings(ctx)                      # data-only mode: default timezone and settings before as_of/tz
            snap = load_snapshot(snapshot_path or ctx.data_path)
            sd = snapshot_documents(snap, ctx.as_of, ctx.tz)
            docs += sd
            report["snapshot"] = {"documents": len(sd), "path": snap.path, "generatedAt": snap.data.get("generatedAt"),
                                  "tenants": snap.tenant_codes(), "isDemo": snap.is_demo}
        except (DataError, ConfigError) as e:
            report["snapshot"] = {"documents": 0, "skipped": e.message}
    if "results" in sources:
        rd, files = results_documents(name)
        docs += rd
        report["results"] = {"documents": len(rd), "files": len(files)}
    if "catalogue" in sources:
        from . import endpoints
        cd = catalogue_documents()
        docs += cd
        report["catalogue"] = {"documents": len(cd), "generatedAt": endpoints.info().get("generatedAt")}
    if "definitions" in sources:
        dd = definition_documents()
        docs += dd
        report["definitions"] = {"documents": len(dd)}
    if "docs" in sources:
        paths = list(docs_paths or [])
        if not paths and default_docs_dir():
            paths = [default_docs_dir()]
        dd = doc_documents(paths)
        docs += dd
        report["docs"] = {"documents": len(dd), "paths": [os.path.abspath(p) for p in paths]}
    return docs, report


def build(ctx, sources=SOURCES, docs_paths=None, embed=None, rebuild=False, snapshot_path=None, quiet=False):
    li = _li()
    t0 = time.time()
    sources = [s.strip().lower() for s in sources if s.strip()]
    unknown = [s for s in sources if s not in SOURCES]
    if unknown:
        raise UsageError("Unknown index source(s): %s" % ", ".join(unknown), hint="Sources: %s" % ", ".join(SOURCES))
    name = index_name(ctx)
    path = index_dir(name)
    spec = embed_spec(embed, ctx.environ)
    manifest = read_manifest(path)
    if manifest and manifest.get("embed") != spec and not rebuild:
        raise ConfigError("The index for %r was built with %s; rebuild it to switch to %s" % (name, manifest.get("embed"), spec),
                          code="INDEX_EMBED_MISMATCH", hint="servicetitan index build --rebuild --embed %s" % spec)
    progress("Collecting documents for %s ..." % ", ".join(sources), quiet)
    docs, report = collect(ctx, sources, docs_paths, snapshot_path)
    if not docs and not manifest:
        raise DataError("Nothing to index: every source was empty or skipped", details=report,
                        hint="Run `servicetitan fetch` for a snapshot, or save a result with --save first.")
    embed_model = make_embed_model(spec)
    documents = _to_documents(li, docs)
    stats = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0}
    ensure_dir(index_root())
    with contextlib.redirect_stdout(sys.stderr):
        fresh = rebuild or not manifest or not os.path.isdir(path)
        if fresh:
            if os.path.isdir(path):
                shutil.rmtree(path)
            progress("Embedding %d documents with %s ..." % (len(documents), spec), quiet)
            index = li.VectorStoreIndex.from_documents(documents, embed_model=embed_model, transformations=[_splitter(li)],
                                                       show_progress=False)
            stats["added"] = len(documents)
        else:
            index = li.load_index_from_storage(li.StorageContext.from_defaults(persist_dir=path), embed_model=embed_model,
                                               transformations=[_splitter(li)])
            new_ids = {d.doc_id for d in documents}
            prefixes = tuple(PREFIX[s] for s in sources)
            for rid in list(index.ref_doc_info.keys()):
                if rid.startswith(prefixes) and rid not in new_ids:
                    index.delete_ref_doc(rid, delete_from_docstore=True)
                    stats["removed"] += 1
            todo = []
            for d in documents:
                h = index.docstore.get_document_hash(d.doc_id)
                if h is None:
                    stats["added"] += 1
                    todo.append(d)
                elif h != d.hash:
                    stats["updated"] += 1
                    todo.append(d)
                else:
                    stats["unchanged"] += 1
            if todo:
                progress("Embedding %d new or changed documents with %s ..." % (len(todo), spec), quiet)
                index.refresh_ref_docs(todo)
        index.storage_context.persist(persist_dir=path)
        total = len(index.ref_doc_info)
    manifest = {"name": name, "embed": spec, "builtAt": iso_z(dt.datetime.now(UTC)), "cliVersion": __version__,
                "documents": total, "sources": dict((manifest or {}).get("sources") or {}, **report) if not fresh else report}
    write_private(os.path.join(path, MANIFEST), json.dumps(manifest, indent=2))
    _lock_down(path)
    out = {"source": "index", "profile": name, "path": path, "embed": spec, "rebuilt": fresh, "documents": total,
           "sources": report, "seconds": round(time.time() - t0, 1)}
    out.update(stats)
    out["next"] = "servicetitan index query \"...\" [--kind jobs,invoices,api ...]"
    return out


def add_documents(ctx, docs, quiet=False):
    """Insert or update documents in an existing index (used by --save)."""
    li = _li()
    name = index_name(ctx)
    path = index_dir(name)
    manifest = read_manifest(path)
    if not manifest:
        raise DataError("No index for profile %r" % name, code="INDEX_MISSING", hint="servicetitan index build")
    embed_model = make_embed_model(manifest["embed"])
    with contextlib.redirect_stdout(sys.stderr):
        index = li.load_index_from_storage(li.StorageContext.from_defaults(persist_dir=path), embed_model=embed_model,
                                           transformations=[_splitter(li)])
        index.refresh_ref_docs(_to_documents(li, docs))
        index.storage_context.persist(persist_dir=path)
        manifest["documents"] = len(index.ref_doc_info)
    manifest["updatedAt"] = iso_z(dt.datetime.now(UTC))
    write_private(os.path.join(path, MANIFEST), json.dumps(manifest, indent=2))
    _lock_down(path)
    return len(docs)


# ---------------------------------------------------------------- status / query
def _snapshot_generated_at(path):
    try:
        with open(path, encoding="utf-8") as f:
            return (json.load(f) or {}).get("generatedAt")
    except (OSError, ValueError, TypeError):
        return None


def _summary(ctx, name, manifest):
    path = index_dir(name)
    out = {"profile": name, "path": path, "builtAt": manifest.get("builtAt"), "updatedAt": manifest.get("updatedAt"),
           "embed": manifest.get("embed"), "documents": manifest.get("documents"), "sources": manifest.get("sources"),
           "bytes": sum(os.path.getsize(os.path.join(path, f)) for f in os.listdir(path) if os.path.isfile(os.path.join(path, f)))}
    indexed = ((manifest.get("sources") or {}).get("snapshot") or {})
    current = None
    try:
        current = _snapshot_generated_at(indexed.get("path") or ctx.data_path)
    except ConfigError:
        pass
    out["snapshot"] = {"indexedGeneratedAt": indexed.get("generatedAt"), "currentGeneratedAt": current,
                       "changed": bool(current and indexed.get("generatedAt") and current != indexed.get("generatedAt"))}
    d = results_dir(name)
    files = sorted(glob.glob(os.path.join(d, "*.json"))) if os.path.isdir(d) else []
    stamp = manifest.get("updatedAt") or manifest.get("builtAt") or ""
    out["results"] = {"saved": len(files), "savedSinceIndexed": sum(1 for f in files if os.path.basename(f)[:16] > stamp.replace("-", "").replace(":", "")[:16])}
    out["stale"] = out["snapshot"]["changed"] or out["results"]["savedSinceIndexed"] > 0
    return out


def status(ctx):
    name = index_name(ctx)
    manifest = read_manifest(index_dir(name))
    ok, why = available()
    out = {"source": "index", "profile": name, "path": index_dir(name), "exists": bool(manifest),
           "llamaIndex": {"available": ok, "detail": why}, "embedDefault": embed_spec(None, ctx.environ)}
    if manifest:
        out.update(_summary(ctx, name, manifest))
    else:
        d = results_dir(name)
        out["results"] = {"saved": len(glob.glob(os.path.join(d, "*.json"))) if os.path.isdir(d) else 0}
        out["next"] = "servicetitan index build" if ok else 'pip install "servicetitan-cli[index]"'
    return out


def _next_command(md):
    kind = md.get("kind")
    if kind == "jobs" and md.get("number"):
        return "servicetitan list jobs --range all --job-number %s" % md["number"]
    if kind in ("invoices", "opportunities", "memberships") and md.get("name"):
        return "servicetitan list %s --range all --customer \"%s\"" % (kind, md["name"])
    if kind == "api":
        return "servicetitan api %s %s %s --describe" % (md.get("module"), md.get("resource"), md.get("operation"))
    if kind == "definition":
        return "servicetitan metrics %s --explain" % md.get("area")
    if kind == "result":
        return "servicetitan index results --show %s" % md.get("file")
    if kind == "doc":
        return md.get("path")
    return None


def query(ctx, text, top=8, kinds=None, tenant=None, all_profiles=False, min_score=None):
    li = _li()
    if not (text or "").strip():
        raise UsageError("Give the query some text")
    kinds = [k.strip().lower() for k in (kinds or []) if k and k.strip()]
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        raise UsageError("Unknown kind(s): %s" % ", ".join(bad), hint="Kinds: %s" % ", ".join(KINDS))
    if all_profiles:
        names = sorted(n for n in (os.listdir(index_root()) if os.path.isdir(index_root()) else [])
                       if read_manifest(os.path.join(index_root(), n)))
    else:
        names = [index_name(ctx)]
    hits, indexes = [], []
    for name in names:
        manifest = read_manifest(index_dir(name))
        if not manifest:
            raise DataError("No index for profile %r" % name, code="INDEX_MISSING",
                            hint="servicetitan index build  (needs a snapshot or saved results first)")
        summary = _summary(ctx, name, manifest)
        indexes.append(summary)
        if summary["stale"]:
            ctx.warnings.append("index for %r is behind: %s; run `servicetitan index build`"
                                % (name, "the snapshot changed" if summary["snapshot"]["changed"] else "results were saved since"))
        embed_model = make_embed_model(manifest["embed"])
        with contextlib.redirect_stdout(sys.stderr):
            index = li.load_index_from_storage(li.StorageContext.from_defaults(persist_dir=index_dir(name)), embed_model=embed_model)
            filters = []
            if kinds:
                filters.append(li.MetadataFilter(key="kind", value=kinds, operator=li.FilterOperator.IN))
            if tenant:
                filters.append(li.MetadataFilter(key="tenant", value=str(tenant).upper()))
            retriever = index.as_retriever(similarity_top_k=top, filters=li.MetadataFilters(filters=filters) if filters else None)
            nodes = retriever.retrieve(text)
        for n in nodes:
            md = dict(n.node.metadata or {})
            score = round(float(n.score), 4) if n.score is not None else None
            if min_score is not None and score is not None and score < min_score:
                continue
            hit = {"score": score, "kind": md.get("kind"), "id": md.get("id"), "profile": name, "tenant": md.get("tenant"),
                   "name": md.get("name"), "date": md.get("date"), "text": n.node.get_content(metadata_mode=li.MetadataMode.NONE),
                   "next": _next_command(md)}
            extra = {k: v for k, v in md.items() if k not in ("kind", "id", "tenant", "name", "date")}
            if extra:
                hit["metadata"] = extra
            hits.append(hit)
    hits.sort(key=lambda h: (h["score"] is None, -(h["score"] or 0.0)))
    hits = hits[:top]
    return {"source": "index", "query": text, "top": top, "filters": {"kind": kinds or None, "tenant": (str(tenant).upper() if tenant else None)},
            "profiles": names, "indexes": indexes, "count": len(hits), "hits": hits,
            "note": "Retrieval only; scores are cosine similarities from the local embedding model. Confirm figures with list/metrics."}
