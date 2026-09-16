# servicetitan-cli for agents

You are driving a read-only ServiceTitan tool. It cannot change anything in
ServiceTitan. Every command prints one JSON document on stdout. Errors are JSON
on stderr with a non-zero exit code.

## Learn the tool

```bash
servicetitan schema            # every command, argument, exit code, metric definition
servicetitan <command> --help
```

## Standard workflow

```bash
servicetitan status                       # which profile/tenants, snapshot age
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
servicetitan api jpm jobs                     # operations; writes are marked disabled
servicetitan api jpm jobs list --describe     # parameters, enums, response fields (JSON)
servicetitan api jpm jobs list --job-status Completed --completed-on-or-after 2026-09-01T00:00:00Z --count
servicetitan api jpm jobs get 12345 --fields jobNumber,total,jobStatus
servicetitan api endpoints --search "words"   # when the module is unknown
```

- Every response carries `source`: `snapshot` (metrics/list) or `live` (api).
- `api ...` needs exactly one tenant; pass `--tenant` when the profile has several.
- `api get <path>` still exists for a raw path (`jpm/v2/jobs` shorthand or the
  documented form). The tenant id is filled in. Nothing is ever sent to another host.
- Paged lists: `--all --max-pages N` follows `hasMore`. `pageSize` defaults to 50 on the API (200 here);
  the client caps it at 500. `ids=` filters take at most 50 ids. `active` filters take `True`, `Any`
  or `False` (default: active records only).
- Bulk feeds: `api get jpm/v2/export/jobs --export --from 2026-01-01`; keep the
  returned `continueFrom` to resume later.
- Rate limit is 600 calls per 10 seconds per tenant; the client backs off on 429.
- Report *data* (Reporting API) needs a POST and is therefore not available here.

## Exit codes

0 ok, 2 usage, 3 configuration (no profile or credentials), 4 auth, 5 API or
network, 6 validation failed, 7 snapshot missing or stale, 1 anything else.
The `hint` field of an error says what to do next.

## Do not

- Do not paste secrets into command arguments; use `--client-secret-stdin`
  or `--client-secret-env VAR`.
- Do not send snapshot files or rendered dashboards outside the company: they
  contain customer names and revenue.
