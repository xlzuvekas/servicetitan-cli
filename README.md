# servicetitan-cli

A ServiceTitan command-line tool built for AI agents and the people who work
with them. It reads by default; a write runs only behind an explicit flag, a
per-profile allowlist and the matching API scope.

- **Multi-tenant.** One machine can hold many companies (*profiles*), each with
  one or more ServiceTitan accounts (*tenants*). `--profile viking --tenant TUC`.
- **Secrets stay out of files.** Client secrets and app keys go to the OS
  credential store (Windows Credential Manager, macOS Keychain, Linux Secret
  Service). Where no keyring exists they go to an owner-only file, and the CLI
  says so.
- **JSON in, JSON out.** Every command prints one JSON document; errors are JSON
  envelopes with stable codes and exit statuses; nothing prompts unless a person
  is at a terminal. `servicetitan schema` describes the whole tool in one call.
- **Derived metrics, not just raw records.** Revenue, average ticket, close
  rate, AR aging, booking rate, membership attach and more, computed the way the
  bundled dashboard computes them, with `--explain` definitions.
- **The whole ServiceTitan API, discoverable step by step.** `servicetitan api`
  is a command tree generated from ServiceTitan's own OpenAPI documents: modules,
  then resources, then operations, then every parameter typed and described.
  All 330 read operations run live. The 270 write operations run only behind
  three gates (`--write` on the call, the profile's write allowlist, a token
  with the operation's `:w` scope), preview with `--dry-run` and land in an
  audit log.
- **The dashboard is still here.** `servicetitan render` writes the same
  single-file operations dashboard the template produced.

Out of the box it only reads: every ServiceTitan call is a `GET` and the only
`POST` is the OAuth token exchange with ServiceTitan's auth server. Writes stay
off until a profile allows them; see [Writes](#writes).

## Install

```bash
pip install git+https://github.com/xlzuvekas/servicetitan-cli
servicetitan --version
```

Python 3.9 or newer. Windows, macOS and Linux. `pipx install` works too.

## Try it in one minute, no credentials

```bash
servicetitan demo --out demo.json            # synthetic snapshot, two tenants
servicetitan metrics service --data demo.json --range mtd
servicetitan list invoices --data demo.json --min-days-past-due 60 --sort=-balance
servicetitan render --data demo.json --out dashboard.html   # open it in a browser
```

Demo output is flagged `isDemo: true` everywhere and the dashboard shows a banner.

## Connect a company

You need four values per tenant: **app key**, **client id**, **client secret**
and **tenant id**. [docs/setup-servicetitan-api-access.md](docs/setup-servicetitan-api-access.md)
walks through getting them from the developer portal and ServiceTitan settings
(about ten minutes, done once per company). `servicetitan init` prints the same
steps and tells you what is still missing on this machine.

```bash
servicetitan profile add acme --company "Acme Home Services" --timezone America/Denver --use
printf '%s\n%s\n' "$CLIENT_SECRET" "$APP_KEY" | \
  servicetitan tenant add acme --code MAIN --label "Main Office" \
      --tenant-id 123456789 --client-id cid.xxxx --client-secret-stdin --app-key-stdin
servicetitan auth check
```

`tenant add` test-authenticates and shows how each business unit will be
classified. At a terminal you can omit the secret flags and paste when prompted;
secrets given with `--client-secret-env VAR` or on stdin never touch shell history.

A second location that is its own ServiceTitan tenant is one more `tenant add`
(the app key is reused from the first tenant automatically). A company with one
tenant and many business units needs only one.

Already set up with the dashboard template's `config.json` + `.env`? Run
`servicetitan profile import acme --use` in that folder: it moves the secrets
into the keyring and keeps your mapping, targets and rules. The legacy layout also
keeps working as is (`servicetitan fetch` in that directory reads `config.json`
and `.env` and writes `live_data.json`).

No profile at all? Plain environment variables work for CI and sandboxes:
`SERVICETITAN_TENANT_ID`, `SERVICETITAN_CLIENT_ID`, `SERVICETITAN_CLIENT_SECRET`,
`SERVICETITAN_APP_KEY` (plus `SERVICETITAN_TIMEZONE`, `SERVICETITAN_ENVIRONMENT`).

## Two kinds of answers

Every response says where it came from:

- `"source": "snapshot"` (`metrics`, `list`, `check`, `render`): computed from
  the snapshot that `fetch` wrote. Fast, offline, and the only way to get the
  derived numbers, because they need the whole year of records. Refresh with
  `fetch --max-age 6`.
- `"source": "live"` (`api ...`, `auth check`): a request to ServiceTitan right
  now, for one tenant.

## Daily use

```bash
servicetitan fetch --max-age 6        # pull a snapshot unless one is younger than 6h
servicetitan status                   # profile, tenants, snapshot age and counts
servicetitan metrics all --explain    # every area, with what each number means
servicetitan metrics sales --range qtd --tenant TUC
servicetitan list jobs --range next7 --booked --fields jobNumber,date,customer,revenue
servicetitan list memberships --renews-within 30
servicetitan api jpm appointments list --starts-on-or-after 2026-09-16T00:00:00Z --all
servicetitan render --out dashboard.html
```

### The API tree

```bash
servicetitan api                          # 25 modules with what they cover
servicetitan api jpm                      # resources: jobs, appointments, job-types, projects ...
servicetitan api jpm jobs                 # operations: list, get, export, history, notes ... (writes marked gated)
servicetitan api jpm jobs list --help     # every parameter with its description, type and allowed values
servicetitan api jpm jobs list --describe # the same contract as JSON, plus response fields (no credentials needed)
servicetitan api jpm jobs list --job-status Completed --completed-on-or-after 2026-09-01T00:00:00Z --all --fields jobNumber,total
servicetitan api jpm jobs get 12345
servicetitan api accounting invoices list --balance-filter-balance 0 --balance-filter-comparer Greater --count
servicetitan api jpm jobs export --from 2026-01-01           # bulk feed with continuation token
servicetitan api endpoints --search "technician rating"      # search when you do not know the module
```

Module aliases work (`sales` for `salestech`, `jobs` for `jpm`, `calls` for
`telecom`). Where ServiceTitan serves several versions of an operation, the
newest has the plain name and older ones a suffix (`telecom calls list` is v3,
`list-v2` the deprecated v2 list); `--describe` shows `deprecated` and
`replacedBy`. The tree is generated from a dated snapshot of ServiceTitan's
OpenAPI documents: `servicetitan api catalogue status` shows the date,
`servicetitan api catalogue refresh` downloads the current documents (kept in
the config directory, taking precedence) and reports every added, removed or
changed operation, parameter, enum value, field and scope; `reset` returns to
the packaged copy. Parameters are the spec's own names in kebab case; enum values are
matched case-insensitively and sent with the spec's spelling. Paged operations
take `--all`, `--max-pages` and `--count`; export feeds take `--from` and
`--max-batches`; everything takes `--fields` and `--raw`. `api get <path>` is
still there for a raw path.

### Writes

Writes are off by default and stay off until three independent gates pass:

1. `--write` on the invocation, so a copied read command can never turn into a
   write;
2. the profile's write allowlist permits the operation:
   `servicetitan profile set acme --allow-writes "jpm/jobs/create-notes,crm/customers"`.
   Patterns are `module`, `module/resource`, `module/resource/operation` or `*`;
   `--no-writes` clears the list; `SERVICETITAN_ALLOW_WRITES` does the same job
   in CI;
3. the token ServiceTitan issued carries the operation's `:w` scope, which the
   app registration must request and the tenant admin must approve.

```bash
servicetitan api jpm jobs create-notes --describe                                        # body fields, required ones, gates
servicetitan api jpm jobs create-notes 12345 --set text="Customer called back" --dry-run  # the exact request; nothing is sent
servicetitan api jpm jobs create-notes 12345 --set text="Customer called back" --write    # runs when the gates pass
servicetitan api jpm jobs create-notes 12345 --body @note.json --write --yes              # unattended
servicetitan api audit                                                                    # what was attempted, with results
```

At a terminal the CLI prints the request and asks you to type the tenant code
before sending; `--yes` skips that, and without a terminal nothing prompts.
Bodies come from `--body '{...}'`, `--body @file.json`, `--body-stdin` or
repeated `--set key=value`, and are checked against the OpenAPI document's
required fields before anything is sent. Every executed write and every dry run
is appended to `audit.jsonl` in the config directory with the tenant, the
operation, the request and the result. A write is never retried automatically
after a server or network error, because the change may already have been
applied; the error says so and the matching read operation tells you.

Snapshots live in the CLI's config directory (`servicetitan status` shows the
path). Every query answer carries `snapshot.generatedAt`, `ageHours` and
`stale`, so an agent can decide to fetch first; `--require-fresh` makes a stale
snapshot an error instead of a warning.

### Commands

| Command | What it does |
|---|---|
| `profile list/show/add/remove/use/set/import/export` | Companies, their dashboard settings and their write allowlist (`set --allow-writes`). `export` prints a shareable config with no ids or secrets. |
| `tenant list/add/update/set-secret/remove` | ServiceTitan accounts inside a profile, with secret storage. |
| `auth check` | Authenticate every tenant; preview business-unit classification. |
| `status` | Profile summary plus snapshot freshness and record counts. Cheap; run first. |
| `fetch` | Pull a snapshot (read-only). `--max-age`, `--tenant` for one tenant, `--max-pages`, `--page-size`. |
| `demo` | Synthetic snapshot for trying things offline. |
| `check` | Snapshot integrity checks; exit 6 on failure. Wire it into unattended refreshes. |
| `metrics <area>` | `revenue`, `service`, `sales`, `install`, `accounting`, `marketing`, `office`, `memberships`, `all`. |
| `list <kind>` | `jobs`, `opportunities`, `invoices`, `purchase-orders`, `calls`, `memberships`, `technicians`, `campaigns`, `business-units`, `people`, with filters, `--sort`, `--fields`, `--limit`, `--count`. |
| `api <module> <resource> <operation>` | The whole API as a tree; `--help`/`--describe` at every level. Reads run live; writes need `--write`, an allowlist match and the `:w` scope, with `--dry-run` to preview. |
| `api get <path>` / `api endpoints` / `api modules` / `api audit` | Raw path access, catalogue search and the write audit log. |
| `render` | Build `dashboard.html` (validates first; never overwrites a good dashboard with a broken pull). |
| `duplicates` | People who may hold two ServiceTitan user records. |
| `palette` | Accessibility check for chart colours. |
| `schema` | Machine-readable description of all of the above. |
| `init` | State-aware setup checklist. |

Global options work before or after the command: `--profile`, `--tenant`,
`--data`, `--format json|text`, `--compact`, `--as-of`, `--config`/`--env-file`
(legacy layout), `--secrets auto|keyring|file`, `--quiet`.

Ranges: `mtd` (default for metrics), `qtd`, `ytd`, `wtd`, `today`, `yesterday`,
`last7/30/90`, `next7/14/30/45/90`, `custom --start --end`. Ranges end at
`--as-of`, which defaults to now in the profile's timezone, so an answer can be
reproduced later.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | unexpected error (`SERVICETITAN_DEBUG=1` for a traceback) |
| 2 | usage error |
| 3 | configuration problem (no profile, missing credentials) |
| 4 | authentication failed |
| 5 | ServiceTitan API or network error |
| 6 | validation failed (snapshot checks, palette) |
| 7 | snapshot missing or stale |

Errors go to stderr as `{"error": {"code", "exit", "message", "hint", "details"}}`.

## For agents

[docs/agent-guide.md](docs/agent-guide.md) is the short version an agent can be
pointed at. The essentials:

1. `servicetitan status` to learn the profile, tenants and snapshot age.
2. `servicetitan fetch --max-age 6` when the snapshot is stale (minutes for a large tenant).
3. `servicetitan metrics <area>` for questions with a known answer shape; add `--explain` before quoting a number to a person.
4. `servicetitan list <kind>` for "which ones" questions; trim payloads with `--fields` and `--limit`.
5. `servicetitan api <module>` to discover, `--describe` to see a contract, then run the operation for anything the snapshot does not hold.
6. Writes: `--describe`, then `--dry-run`, then `--write`. If the profile refuses (exit 3, `WRITES_DISABLED`), the hint names the command a person runs to allow it.

Ask the CLI itself: `servicetitan schema`. For Claude Code or Cowork, install
`skills/servicetitan/SKILL.md` as a skill and the agent will reach for the CLI
on its own when ServiceTitan comes up.

## Use as a library

Everything the CLI does is importable, so a Python agent or a scheduled job can
skip the subprocess:

```python
from servicetitan_cli.config import Context
from servicetitan_cli.api import ServiceTitanClient
from servicetitan_cli.snapshot import load_snapshot
from servicetitan_cli.metrics import Query, compute

ctx = Context(profile="acme")                      # same resolution rules as the CLI
client = ServiceTitanClient(ctx.credentials_for(ctx.tenants[0]))
jobs = client.get_all("jpm/v2/jobs", {"jobStatus": "Completed", "completedOnOrAfter": "2026-09-01T00:00:00Z"})

snap = load_snapshot(ctx.data_path)
q = Query(snap, ctx.as_of, ctx.tz, range_kind="mtd", targets=ctx.config["targets"], rules=ctx.config["rules"])
print(compute(q, "service")["kpis"]["avgTicket"])
```

`ServiceTitanClient` reads with `get`, `get_all`, `get_pages` and `export`.
Its one write method, `write`, refuses to run without a `WriteAuthorization`,
and only `apitree.authorize_write` builds one, after the three gates pass; a
program that needs to write goes through `apitree.execute` like the CLI does. `servicetitan_cli.endpoints` exposes the
catalogue (`resolve_operation`, `describe`, `search`) and
`servicetitan_cli.apitree.execute` runs an operation from a parsed namespace.

## Reading the numbers

Several figures are derived rather than read from a field, because the obvious
approach gives a confidently wrong answer. `metrics --explain` carries these
definitions; the full reasoning is in the docstring of `src/servicetitan_cli/fetch.py`.

- **Avg ticket is measured on calls that billed.** Warranty and callback work is
  no-charge by design and a sales visit produces an estimate, not revenue.
  Revenue per dispatch is reported beside it.
- **Close rate counts opportunities, not estimates.** A Good/Better/Best
  presentation is one chance to sell, not three.
- **Booked revenue** is the price on the job dated to its next future
  appointment; open jobs with no future appointment are excluded and counted.
- **AR excludes undated invoices** (pre-created when a job is booked).
- **Committed on open POs is not accounts payable.** Vendor bills are reachable
  live (`servicetitan api accounting ap-bills list`) but not in the snapshot yet.
- **Membership attach is approximate:** same customer, same day.

## Where things live

```
<config dir>/profiles.json      companies, tenants, settings (no secrets)
<config dir>/credentials.json   secrets, only when no OS keyring is available (mode 0600)
<config dir>/tokens.json        15-minute access tokens (mode 0600)
<config dir>/audit.jsonl        every write attempt: dry runs, successes, failures (mode 0600)
<config dir>/snapshots/*.json   one snapshot per profile
```

The config dir is `%APPDATA%\servicetitan-cli` on Windows,
`~/Library/Application Support/servicetitan-cli` on macOS,
`~/.config/servicetitan-cli` on Linux, or `$SERVICETITAN_HOME`.

Snapshots and rendered dashboards contain real customer names, employee names
and revenue figures. Treat them like the ServiceTitan account itself.

## Development

```bash
git clone https://github.com/xlzuvekas/servicetitan-cli && cd servicetitan-cli
pip install -e ".[dev]"
pytest
python scripts/build_endpoint_catalogue.py   # refresh the endpoint catalogue from the portal
```

`docs/servicetitan-api.md` collects what the ServiceTitan documentation says
about auth, paging, rate limits and the data model, with sources.
`docs/publishing-the-dashboard.md` covers hosting the rendered page as an
installable app with a scheduled refresh. `BRANDING.md` covers logos, colours
and type.

## Migrating from the dashboard template

| Template | Now |
|---|---|
| `python test_connection.py` | `servicetitan auth check` |
| `python fetch_dashboard_data.py` | `servicetitan fetch` |
| `python render_dashboard.py [--demo]` | `servicetitan render [--data demo.json]` |
| `python generate_demo_data.py` | `servicetitan demo` |
| `python check_duplicates.py` | `servicetitan duplicates` |
| `python validate_palette.py ...` | `servicetitan palette ...` |
| `config.json` + `.env` | `servicetitan profile import`, or keep using them in place |

## License

MIT.
