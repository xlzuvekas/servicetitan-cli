# Working on servicetitan-cli

## Helping someone install or use it?

You are probably not here to change the code. Follow SETUP.md with them: one
step at a time, commands for their operating system only, and check each
step's output before the next. Never ask for or accept the client secret or the
app key in the conversation; the person types them into `servicetitan setup` in
their own terminal. README.md covers daily use and `servicetitan schema`
describes every command. The rest of this file is for changing the code.

## The code

ServiceTitan CLI for AI agents: reads by default, writes behind explicit gates.
Python 3.9+, `requests` + `keyring` only. Source in `src/servicetitan_cli/`, tests in `tests/`, run `pytest`.

## Non-negotiables

- **Reads are GET; writes have one gate.** `ServiceTitanClient.write` is the
  only code that sends a non-GET request to the API host, and it refuses to run
  without a `WriteAuthorization`, which only `apitree.authorize_write` builds
  after the three gates pass: `--write`; the profile's write policy, where
  `auto` (the default) follows the credential's granted `:w` scopes, patterns
  narrow, `--no-writes` turns off and `SERVICETITAN_ALLOW_WRITES` replaces;
  and the token's granted `:w` scope, checked on every write. Never give
  fetch, metrics or listing code a way to write. The token POST to the auth
  host is the other exception.
  `tests/test_api.py::test_reads_never_send_anything_but_get_to_the_api_host`
  and `tests/test_writes.py` guard this.
- **Writes are previewable, logged and never auto-retried.** `--dry-run` shows
  the exact request; every attempt lands in `audit.jsonl`; a 5xx or a dropped
  connection on a write is reported, not repeated.
- **No client information, ever.** Examples, help text, hints, tests and docs
  use the fictional Acme Home Services with tenant codes MAIN, NORTH and EAST.
  Never commit a client's name, locations, people, ids or data, and keep them
  out of commit messages, branch names and pull requests too. The repository
  is public: anything pushed can be read by anyone, and rewriting history
  does not take it back.
- **Setup is guided and secrets stay out of chats.** `servicetitan setup`
  (`onboarding.py`) is the interactive path for people; it refuses without a
  terminal and prints the scripted `profile add` + `tenant add` equivalent.
  Nothing it asks is saved until the connection test passes or the person
  chooses to keep the values. Keep SETUP.md in step with it.
- **Credentials never go to a foreign host.** `resolve_url` refuses URLs off
  the environment's API host. Keep it that way.
- **The index stays local.** `indexing.py` computes embeddings on the machine
  (fastembed, huggingface, ollama or hash); never default to a hosted
  embedding or language-model service, and `index query` never calls an LLM.
  Index and results directories are owner-only. LlamaIndex stays an optional
  extra; core commands must work without it.
- **stdout is one JSON document.** Progress, warnings and errors go to stderr.
  Use `output.progress`, never `print`. The one exception is `servicetitan
  mcp`, whose stdout carries only MCP protocol messages; tool runs redirect any
  stray print to stderr.
- **The MCP server is the CLI.** `mcp_server.py` (server name st-operator, an
  independent third-party integration) runs every tool through
  `cli.run_captured`, the same path as the command line, so answers and errors
  match. Keep the tool list short: hosts load every tool description into the
  model's context. Parser-backed tools read their schemas from argparse. Writes
  go only through `api_write`, which previews by default; `--read-only` leaves
  it out. `tests/test_mcp.py` includes a real stdio session.
- **Errors are `CliError` subclasses** with a code, exit status and a hint that
  says what to do. See `errors.py` and keep the exit-code table in sync with
  `README.md` and `schema.py`.
- **Secrets are redacted** in every output (`output.redact`); never log them.
- **Metric math mirrors the dashboard.** `metrics.py` is a port of the tab
  renderers in `data/dashboard_template.html`; change both or neither, and
  update `DEFINITIONS` when a number's meaning changes.

## Layout

| Module | Role |
|---|---|
| `cli.py` | argparse tree + handlers. Global options are attached to every subparser with `default=SUPPRESS`; read them with `_opt`. |
| `config.py` | `Context`: which profile/tenant, credentials (env > secret store), timezone, `as_of`, snapshot path. Lazy, so profile-less commands work. |
| `store.py` / `secrets.py` | `profiles.json` (no secrets) and the keyring/file secret backends. |
| `api.py` | Client: token cache, retries, 429 handling, paging, export feeds, path shorthand, and the single gated `write`. |
| `fetch.py` | Snapshot builder; the derived-number rules are documented at the top. |
| `metrics.py` / `listing.py` | Query layer over a snapshot. |
| `endpoints.py` + `data/endpoints.json` | Catalogue of all 600 operations (module > resource > operation, parameters, enums, scopes, response fields) generated by `scripts/build_endpoint_catalogue.py`; regenerate, do not hand-edit. Naming rules live in the script. |
| `apitree.py` | Builds an argparse parser per operation from the catalogue and executes it; writes pass through `authorize_write` (flag, allowlist, scope) and `execute_write` (body, dry run, confirmation, audit). |
| `writes.py` | Write policy: allowlist patterns, request-body assembly and validation, terminal confirmation, audit log. |
| `catalogue.py` | Builds a catalogue from OpenAPI documents (naming rules, hashes, versions, deprecation), refreshes from the live portal into the config dir, diffs two catalogues. |
| `indexing.py` | Local LlamaIndex: documents from snapshot records, saved results (`--save`), the catalogue, definitions and docs; build/update, status, results store; hybrid query (vectors + BM25 `lexical.json`, RRF fusion, rare identifiers pinned, optional local cross-encoder). Lazy imports; `hash` embeddings for tests. |
| `mcp_server.py` | `servicetitan mcp`: MCP over stdio (JSON-RPC lines), ten tools mapped onto CLI commands, progress notifications, oversize answers saved locally, `--client-config` for Claude Desktop and Claude Code. |
| `onboarding.py` | `servicetitan setup` (the interactive wizard: prompts on stderr, secrets via getpass, test before save) and `servicetitan init` (offline checklist with the next command). |
| `brief.py` | `servicetitan brief`: a selection from the metrics areas for one-call orientation; no new math. |
| `render.py` + `data/dashboard_template.html` | The single-file dashboard. |
| `schema.py` | `servicetitan schema`; derived from the parser, so new commands appear automatically. |

## Conventions

- Add a test for every command or metric change; `tests/test_metrics.py` has a
  hand-built snapshot with known answers, extend it rather than asserting on
  the synthetic fixture. `tests/synthetic.py` generates that fixture; it is
  test support, not a product feature (there is no demo command).
- Keep help text and `DEFINITIONS` in plain language: agents read them.
- Do not add dependencies without a reason that survives a Windows laptop with
  no compiler.
- `docs/servicetitan-api.md` is the source of truth for API facts; cite it in
  code comments when relying on a limit or a field.
