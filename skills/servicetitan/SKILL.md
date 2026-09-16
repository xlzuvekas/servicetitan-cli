---
name: servicetitan
description: Answer questions about a company's ServiceTitan data (revenue, service calls, sales close rate, installs, accounts receivable, marketing, call handling, memberships) and refresh or render its operations dashboard, using the `servicetitan` CLI, which reads by default and writes only behind explicit gates. Use whenever ServiceTitan numbers, jobs, invoices, technicians, campaigns, or the dashboard come up.
---

# ServiceTitan via the servicetitan CLI

The `servicetitan` command is installed on this machine and holds credentials
for one or more companies. Nothing you run changes ServiceTitan unless you
run an `api` write operation with `--write` and the profile allows it (see
Writes below).

## Orient first

```bash
servicetitan status            # profile, tenants, snapshot age
servicetitan profile list      # if several companies are configured; then pass --profile NAME everywhere
```

If `snapshot.stale` is true (older than 6 hours) or there is no snapshot:

```bash
servicetitan fetch --max-age 6
```

That call talks to ServiceTitan and can take a few minutes. Everything below
answers from the local snapshot in well under a second.

## Answer questions

| Question | Command |
|---|---|
| Revenue this month / quarter / year, run rate, what is booked | `servicetitan metrics revenue --range mtd` (qtd, ytd, last30, custom --start --end) |
| Service department: avg ticket, membership attach, technician scorecard | `servicetitan metrics service` |
| Sales: close rate, booked value, rep table, open pipeline | `servicetitan metrics sales` |
| Installs completed and scheduled by installer | `servicetitan metrics install` |
| AR, aging buckets, who owes what, open POs | `servicetitan metrics accounting` |
| Campaign performance | `servicetitan metrics marketing --range ytd` |
| Inbound calls, booking rate, abandoned, by agent | `servicetitan metrics office` |
| Active members, renewals due | `servicetitan metrics memberships` |
| Everything at once | `servicetitan metrics all --explain` |
| Specific records | `servicetitan list jobs|opportunities|invoices|purchase-orders|calls|memberships|technicians|campaigns ...` |

One tenant only: add `--tenant CODE` (codes appear in `status`). Everything
else is the combined company view.

Useful list filters: `--range next7 --booked` (upcoming jobs), `--tech "Name"`,
`--customer "Name"`, `--min-days-past-due 60 --sort=-balance`, `--won` / `--open`
`--rep "Name"`, `--renews-within 30`, `--fields a,b,c --limit 20`, `--count`.

Anything the snapshot does not hold comes live from ServiceTitan through the
API tree. Discover first, then call; never guess a path:

```bash
servicetitan api                                  # modules
servicetitan api jpm                              # resources (aliases: sales, jobs, calls ...)
servicetitan api jpm appointments                 # operations
servicetitan api jpm appointments list --describe # parameters and response fields
servicetitan api jpm appointments list --starts-on-or-after 2026-09-16T00:00:00Z --all --fields jobId,start,status
servicetitan api endpoints --search "<words>"     # when the module is unknown
```

Responses say `"source": "live"`; snapshot answers say `"source": "snapshot"`.

## Writes

Write operations (POST, PUT, PATCH, DELETE) are marked gated in the tree. They
run only with `--write` on the call, when the profile's write policy permits
the operation (by default: whatever `:w` scopes the credential was granted; a
person may have narrowed it) and the token carries that scope. Look, preview,
run, then report the result id:

```bash
servicetitan api jpm jobs create-notes --describe                        # body fields, required ones, gates
servicetitan api jpm jobs create-notes 12345 --set text="..." --dry-run  # exact request; nothing is sent
servicetitan api jpm jobs create-notes 12345 --set text="..." --write --yes
servicetitan api audit --limit 5                                         # what was attempted and what came back
```

If the policy refuses (exit 3, `WRITES_DISABLED`) or the credential lacks the
scope (exit 4, `SCOPE`), give the person the hint from the error; do not look
for another way in. After exit 5 on
a write, check with the matching read before repeating it: the CLI never
retries a write by itself.

## Quoting numbers to people

Run the metric once with `--explain` and keep the definition next to the
number. In particular:

- **Avg ticket** is measured on calls that billed; revenue per dispatch counts
  every call.
- **Close rate** counts opportunities (one per job), not estimates; a
  Good/Better/Best set is one opportunity.
- **Booked revenue** is priced jobs with a future appointment; jobs with no
  appointment are excluded and reported as a hygiene count.
- **AR** excludes invoices that have no invoice date yet.
- **Committed on open POs** is not accounts payable.

State the range and the snapshot time (`snapshot.generatedAt`) with any
figure. Demo data (`snapshot.isDemo: true`) is synthetic; never present it as real.

## Dashboard

```bash
servicetitan fetch --max-age 2 && servicetitan render --out dashboard.html
```

`render` refuses to write when the snapshot fails its checks; report the
failed checks from the error's `details` rather than retrying blindly.

## Setup and errors

- No profile yet: `servicetitan init` prints the state-aware setup steps;
  credentials come from the ServiceTitan developer portal and Settings >
  Integrations > API Application Access (see docs/setup-servicetitan-api-access.md).
- Exit 3 = configuration, 4 = credentials rejected, 5 = API/network, 6 =
  validation failed, 7 = snapshot missing/stale. The error JSON's `hint` says
  what to do.
- Never put secrets on the command line; use `--client-secret-stdin` or
  `--client-secret-env VAR`.
- Snapshots and rendered pages contain customer names and revenue: keep them
  inside the company.
