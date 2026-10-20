# Changelog

servicetitan-cli is unofficial: it is not made, endorsed or supported by
ServiceTitan.

## 0.2.0 (2026-10-19)

The first public release. Earlier, private development (all reported as
version 0.1.0) was squashed into this release, and this entry summarizes it.

### The command-line tool

- One `servicetitan` command built for AI agents and the people who work with
  them. Every command prints one JSON document on stdout (`--format text` or
  `--format csv` for people). Errors are JSON on stderr with stable codes and
  exit statuses, nothing prompts unless a person is at a terminal, and
  `servicetitan schema` describes every command.
- Profiles (companies) hold one or more tenants (ServiceTitan accounts).
  Client secrets and app keys go to the OS credential store, or to an
  owner-only file where none exists. `profile import` brings over the old
  dashboard template's `config.json` and `.env`.
- Guided first run. `servicetitan setup` asks for the four ServiceTitan values
  at a terminal, hides the secrets, and saves nothing until the connection test
  passes. `servicetitan init` prints a checklist with the next command to run.
  SETUP.md lets Claude walk someone through installation without the secrets
  entering the chat.

### Data and answers

- `fetch` pulls a snapshot from ServiceTitan, all tenants in parallel. It reads
  every page of every collection. If a collection stops early (`--max-pages`,
  or an answer that would page forever), the snapshot says `complete: false`,
  answers carry a warning and `check` fails. A record seen twice while paging
  counts once.
- `metrics` (revenue, service, sales, install, accounting, marketing, office,
  memberships) computes the dashboard's numbers, with `--explain` definitions.
  `brief` gives the day's picture in one call, and `list` answers "which ones"
  questions with filters, sorting and field trimming.
- The derived numbers follow rules worked out against real accounts. Ids are
  namespaced per tenant. An unpriced booked job takes its sold estimate's
  total. Good/Better/Best estimates count as one opportunity. Pre-created,
  unbilled invoices stay out of receivables, and Dispatched jobs count as open
  work.

### The whole ServiceTitan API

- `servicetitan api <module> <resource> <operation>` is generated from
  ServiceTitan's OpenAPI documents: 600 operations, with typed and described
  parameters and `--describe` contracts. `api catalogue refresh` downloads the
  current documents and reports what changed.
- Writes run only behind three gates: `--write`, the profile's write policy
  (by default, whatever the credential's granted `:w` scopes allow) and the
  token's scope, checked on every call. `--dry-run` previews a write, every
  attempt is logged to `audit.jsonl`, and a write is never retried after a 5xx
  or a dropped connection.
- `docs/servicetitan-api.md` collects what ServiceTitan's documentation says
  about authentication, paging, rate limits, scopes and versioning, with
  sources.

### Search and memory

- `--save` keeps any result on disk. The optional `[index]` extra builds a
  local LlamaIndex over snapshot records, saved results, the API catalogue and
  the docs. Queries combine meaning and exact matches (vectors plus BM25, with
  exact identifiers pinned to the top), and nothing leaves the machine.

### MCP

- `servicetitan mcp` serves ten tools to Claude Desktop, Claude Code and other
  MCP clients, as a local server named st-operator. Every tool runs through the
  same code as the command line. `api_write` previews by default, and
  `--read-only` leaves it out.

### Fixes

- Output is UTF-8 whatever the Windows console codepage says, so a record with
  an emoji no longer ends a long pull partway through.

### Experimental

- The HTML dashboard (`render`, its template, `palette` and the publishing
  guide) is experimental: its layout, figures and options may change between
  versions. Every page says it is unofficial and experimental.
