# servicetitan-cli

A read-only ServiceTitan command-line tool built for AI agents and the people
who work with them.

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
- **Every ServiceTitan endpoint, read-only.** `servicetitan api get` reaches any
  of the 330 GET operations in ServiceTitan's public API, with paging and export
  continuation handled, and a searchable catalogue generated from the official
  OpenAPI documents.
- **The dashboard is still here.** `servicetitan render` writes the same
  single-file operations dashboard the template produced.

It only ever reads. Every ServiceTitan call is a `GET`; the only `POST` is the
OAuth token exchange with ServiceTitan's auth server.

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

## Daily use

```bash
servicetitan fetch --max-age 6        # pull a snapshot unless one is younger than 6h
servicetitan status                   # profile, tenants, snapshot age and counts
servicetitan metrics all --explain    # every area, with what each number means
servicetitan metrics sales --range qtd --tenant TUC
servicetitan list jobs --range next7 --booked --fields jobNumber,date,customer,revenue
servicetitan list memberships --renews-within 30
servicetitan api get jpm/v2/appointments --param startsOnOrAfter=2026-09-16T00:00:00Z --all
servicetitan render --out dashboard.html
```

Snapshots live in the CLI's config directory (`servicetitan status` shows the
path). Every query answer carries `snapshot.generatedAt`, `ageHours` and
`stale`, so an agent can decide to fetch first; `--require-fresh` makes a stale
snapshot an error instead of a warning.

### Commands

| Command | What it does |
|---|---|
| `profile list/show/add/remove/use/set/import/export` | Companies and their dashboard settings. `export` prints a shareable config with no ids or secrets. |
| `tenant list/add/update/set-secret/remove` | ServiceTitan accounts inside a profile, with secret storage. |
| `auth check` | Authenticate every tenant; preview business-unit classification. |
| `status` | Profile summary plus snapshot freshness and record counts. Cheap; run first. |
| `fetch` | Pull a snapshot (read-only). `--max-age`, `--tenant` for one tenant, `--max-pages`, `--page-size`. |
| `demo` | Synthetic snapshot for trying things offline. |
| `check` | Snapshot integrity checks; exit 6 on failure. Wire it into unattended refreshes. |
| `metrics <area>` | `revenue`, `service`, `sales`, `install`, `accounting`, `marketing`, `office`, `memberships`, `all`. |
| `list <kind>` | `jobs`, `opportunities`, `invoices`, `purchase-orders`, `calls`, `memberships`, `technicians`, `campaigns`, `business-units`, `people`, with filters, `--sort`, `--fields`, `--limit`, `--count`. |
| `api get <path>` | Any GET endpoint for one tenant. `--param k=v`, `--all` (paging), `--export --from TOKEN` (bulk export feeds), `--raw`. |
| `api endpoints` / `api modules` | Search the catalogue built from ServiceTitan's OpenAPI documents. |
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
5. `servicetitan api endpoints --search ...` then `servicetitan api get ...` for anything the snapshot does not hold.

Ask the CLI itself: `servicetitan schema`.

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
- **Committed on open POs is not accounts payable.**
- **Membership attach is approximate:** same customer, same day.

## Where things live

```
<config dir>/profiles.json      companies, tenants, settings (no secrets)
<config dir>/credentials.json   secrets, only when no OS keyring is available (mode 0600)
<config dir>/tokens.json        15-minute access tokens (mode 0600)
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
`BRANDING.md` covers logos, colours and type for the rendered dashboard.

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
