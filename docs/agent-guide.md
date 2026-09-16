# servicetitan-cli for agents

You are driving a ServiceTitan tool that reads by default. Nothing changes in
ServiceTitan unless you run an `api` write operation with `--write` and the
profile's allowlist permits it (see Writes below). Every command prints one
JSON document on stdout. Errors are JSON
on stderr with a non-zero exit code.

## Learn the tool

```bash
servicetitan schema            # every command, argument, exit code, metric definition
servicetitan <command> --help
```

## Standard workflow

```bash
servicetitan status                       # which profile/tenants, snapshot age
servicetitan brief                        # today, next 7 days, month to date, receivables, memberships, flags
servicetitan fetch --max-age 6            # refresh if older than 6 hours (skips otherwise)
servicetitan metrics <area> [--range mtd|qtd|ytd|last30|custom --start --end] [--tenant CODE] [--explain]
servicetitan list <kind> [filters] --fields a,b,c --limit 20
```

`status` costs nothing. `fetch` calls ServiceTitan (minutes on a large tenant)
and is safe to repeat. Everything else answers from the local snapshot; each
answer carries `snapshot.generatedAt`, `snapshot.ageHours` and `snapshot.stale`.
Add `--require-fresh` when a stale answer would be worse than no answer.

## Choosing a command

| Question | Command |
|---|---|
| How is revenue / service / sales / install doing this month? | `metrics revenue`, `metrics service`, `metrics sales`, `metrics install` |
| What is overdue? Who owes us? | `metrics accounting`; `list invoices --min-days-past-due 30 --sort=-balance` |
| Which campaigns produce? | `metrics marketing --range ytd` |
| Call handling, booking rate, abandoned calls | `metrics office` |
| Memberships due for renewal | `metrics memberships`; `list memberships --renews-within 30` |
| What is booked next week? | `list jobs --range next7 --booked` |
| A technician's jobs | `list jobs --tech "Jordan" --range mtd` |
| One rep's open quotes | `list opportunities --open --rep "Casey" --sort=-bestOption` |
| Something the snapshot does not hold | `api endpoints --search "<words>"` then `api get <path> --param k=v [--all]` |
| Is the data trustworthy? | `check` (exit 6 lists failed integrity checks) |
| A fuzzy question: a customer by a half-remembered name, "the operation for X", "what did we find yesterday" | `index query "<words>" [--kind invoices,result,api]` (when `index status` says an index exists) |

Before quoting a number to a person, run the metric with `--explain` once and
carry the definition: for example "avg ticket" is measured on billed calls only,
and "close rate" counts opportunities (one per job), not estimates.

## Multi-company machines

`servicetitan profile list` shows every company configured here. Pass
`--profile NAME` on every command (or set `SERVICETITAN_PROFILE`). Within a
profile, `--tenant CODE` narrows to one ServiceTitan account; the default is the
combined view. Record ids are prefixed with the tenant code (`SD:5510`).

## Live API access

Discover, then call. Never guess a path.

```bash
servicetitan api                              # modules
servicetitan api jpm                          # resources in a module (aliases: sales, jobs, calls ...)
servicetitan api jpm jobs                     # operations; writes are marked gated
servicetitan api jpm jobs list --describe     # parameters, enums, response fields (JSON)
servicetitan api jpm jobs list --job-status Completed --completed-on-or-after 2026-09-01T00:00:00Z --count
servicetitan api jpm jobs get 12345 --fields jobNumber,total,jobStatus
servicetitan api endpoints --search "words"   # when the module is unknown
```

- Every response carries `source`: `snapshot` (metrics/list), `live` (api), `catalogue` (discovery) or `audit` (api audit).
- `servicetitan api catalogue status` tells you how old the operation catalogue is; `refresh` updates it
  from ServiceTitan's current OpenAPI documents and lists what changed. Operations with `deprecated: true`
  in `--describe` name their replacement in `replacedBy`.
- `api ...` needs exactly one tenant; pass `--tenant` when the profile has several.
- `api get <path>` still exists for a raw path (`jpm/v2/jobs` shorthand or the
  documented form). The tenant id is filled in. Nothing is ever sent to another host.
- Paged lists: `--all --max-pages N` follows `hasMore`. `pageSize` defaults to 50 on the API (200 here);
  the client caps it at 500. `ids=` filters take at most 50 ids. `active` filters take `True`, `Any`
  or `False` (default: active records only).
- Bulk feeds: `api get jpm/v2/export/jobs --export --from 2026-01-01`; keep the
  returned `continueFrom` to resume later.
- Rate limit is 600 calls per 10 seconds per tenant; the client backs off on 429.
- Report *data* (Reporting API) is fetched with a POST, so here it counts as a write and runs through the
  write gates like any other non-GET operation, even though ServiceTitan only asks for the `:r` scope.

## Writes

Write operations (POST, PUT, PATCH, DELETE in the tree) run only when all three
hold:

1. you pass `--write` on that invocation;
2. the profile's write policy permits the operation. The default policy `auto`
   allows exactly the operations whose `:w` scope the credential was granted
   (`servicetitan auth check` lists them under `writes.credentialAllows`); a
   person may have narrowed it with `profile set NAME --allow-writes ...` or
   turned it off with `--no-writes` (`profile show` reports it under `writes`).
   Exit 3 with code `WRITES_DISABLED` means the policy refuses, and the hint
   shows the exact command a person would run;
3. the token carries the operation's `:w` scope (exit 4, code `SCOPE`,
   otherwise). Only the app registration and the tenant admin can change that.

Always look, then preview, then run:

```bash
servicetitan api jpm jobs create-notes --describe                        # requestBody (fields, required ones) and `requires`
servicetitan api jpm jobs create-notes 12345 --set text="..." --dry-run  # exact method, URL, params, body; sends nothing
servicetitan api jpm jobs create-notes 12345 --set text="..." --write --yes
servicetitan api audit --limit 5                                         # what was attempted, with status and result id
```

- Bodies: `--body '{"a": 1}'`, `--body @file.json`, `--body-stdin`, or repeated
  `--set key=value` (values parse as JSON when they can, else text). Required
  fields from the OpenAPI document are checked before sending.
- A successful write returns `status`, `data` (the response, usually the created
  or changed record) and `audit` (the log path). Report the result id.
- After exit 5 on a write, the change may or may not have been applied: the CLI
  never retries a write on its own. Check with the matching read operation
  before repeating it.
- Do not ask for the policy to be widened beyond the operation at hand, and
  never suggest `*`.

## Remembering and searching

`--save` on any command keeps its result under the config directory with the
command line that produced it; add `--save-label "why"` so it can be found
again. Save live API answers you may need later (`api jpm jobs get 12345
--save`), not routine snapshot queries you can rerun.

When the index extra is installed (`servicetitan index status` says so):

```bash
servicetitan index status                              # exists? behind the snapshot? results saved since?
servicetitan index build                               # incremental; a person may prefer `fetch --index`
servicetitan index query "noisy furnace" --top 5       # hits: score, kind, id, tenant, text, next command
servicetitan index query "technician ratings" --kind api
servicetitan index query "overdue" --kind result       # what was saved before
servicetitan index results --show FILE                 # the full saved result
```

Exact numbers work: `index query 1619` or `index query "INV-2231"` finds the record
carrying that number through the lexical side and lists it first with `matched`.
Narrow with `--kind`, `--tenant`, `--since`/`--until` (records only) and
`--name "<customer>"`; `--mode lexical` searches numbers and words only and needs
no model, so it answers in well under a second; `--mode vector` is meaning only.

Hits are retrieval, not answers: run the `next` command on a hit before quoting
a figure, and prefer `list`/`metrics` for anything numeric. A warning on stderr
says when the index is behind the snapshot. Everything runs on this machine;
nothing is sent to an embedding service.

`--format csv` on any command writes its main table (records, data, hits) as
CSV for a spreadsheet.

## Exit codes

0 ok, 2 usage, 3 configuration (no profile or credentials), 4 auth, 5 API or
network, 6 validation failed, 7 snapshot missing or stale, 1 anything else.
The `hint` field of an error says what to do next.

## Do not

- Do not paste secrets into command arguments; use `--client-secret-stdin`
  or `--client-secret-env VAR`.
- Do not send snapshot files or rendered dashboards outside the company: they
  contain customer names and revenue.
