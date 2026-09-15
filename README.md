# ServiceTitan Operations Dashboard

A single-file operations dashboard that reads your ServiceTitan account and
renders eight tabs — Revenue, Service, Sales, Installation, Accounting,
Marketing, Office and Memberships — across one or many locations.

The output is **one self-contained HTML file**. No server, no build step, no
database. Open it from disk, email it, or drop it on any static host.

**It only ever reads.** Every ServiceTitan call is a `GET`. Nothing is written
back to your account, ever.

---

## Quick look, before you connect anything

You can see the whole thing working on synthetic data in under a minute:

```bash
pip install requests
cp config.example.json config.json
python generate_demo_data.py
python render_dashboard.py --demo
```

Open `dashboard.html`. Every tab, filter and drill-down works. The figures are
randomly generated and the page says so in a banner across the top.

---

## Setup

### Step 1: configure

```bash
cp config.example.json config.json
```

Open `config.json` and set:

- **`company.name`** and **`company.subtitle`** — what appears in the header.
- **`locations`** — one entry per ServiceTitan tenant. `code` is a short unique
  id, `label` is what people see, `env` is the suffix on that location's `.env`
  keys. **One location can omit `env`** and use the unsuffixed keys; with more
  than one, each needs its own. A single-location shop lists one entry and the
  location toggle hides itself.
- **`departmentMapping`** — how your business-unit names map to departments.
  **Check this one.** It matches keywords against your business-unit names, and
  the defaults assume names containing "install", "service", "maint" and so on.
  `test_connection.py` prints exactly how each of your units gets classified.
- **`targets`** — the target lines on the Service tab. Set any to `null` to
  hide that line rather than show a target nobody agreed to.

### Step 2: connect ServiceTitan

```bash
cp .env.example .env
```

Four values are needed. In the
[ServiceTitan developer portal](https://developer.servicetitan.io):

1. Create an app (or open an existing one).
2. **Client ID** and **Client Secret** come from the app's credentials. The
   secret is shown **once** — generate a new one if you lose it.
3. **App Key** is on the app page. It is the same for every tenant your app is
   connected to.
4. **Tenant ID** is per account — Settings → Integrations, or the URL when
   signed in to that tenant.
5. Grant **read** scopes for: Job Planning & Management, Accounting, Sales &
   Estimates, Marketing, Telecom, Inventory, Memberships, CRM, Dispatch and
   Settings. Read-only is all this needs.

#### Naming the keys

Each credential can be set once for everybody, or per location:

```
SERVICETITAN_APP_KEY              shared — every location uses it
SERVICETITAN_APP_KEY_SPRINGFIELD  only the location whose env is SPRINGFIELD
```

A location's own key wins; the unsuffixed key is the fallback. So:

**One location** — fill in the four unsuffixed keys, delete the per-location
section of `.env`, and leave `env` out of `config.json`:

```
SERVICETITAN_APP_KEY=...
SERVICETITAN_CLIENT_ID=...
SERVICETITAN_CLIENT_SECRET=...
SERVICETITAN_TENANT_ID=...
```

**Several locations** — set the app key once, since it belongs to your app
rather than to a tenant, and suffix the three that genuinely differ:

```
SERVICETITAN_APP_KEY=...

SERVICETITAN_CLIENT_ID_SPRINGFIELD=...
SERVICETITAN_CLIENT_SECRET_SPRINGFIELD=...
SERVICETITAN_TENANT_ID_SPRINGFIELD=...

SERVICETITAN_CLIENT_ID_LOCATION_2=...
SERVICETITAN_CLIENT_SECRET_LOCATION_2=...
SERVICETITAN_TENANT_ID_LOCATION_2=...
```

Growing from one location to two is then: add the suffixed keys for both, and
add `env` to both entries in `config.json`. The shared app key stays put.

Then check it:

```bash
python test_connection.py
```

This authenticates each location, reads your business units, and shows how each
one will be classified. Fix any classification surprises in `config.json` now —
they are much harder to spot later as odd totals.

### Step 3: pull and render

```bash
python fetch_dashboard_data.py     # writes live_data.json  (a few minutes)
python render_dashboard.py         # writes dashboard.html
```

Open `dashboard.html`.

### Step 4: make it yours

See **[BRANDING.md](BRANDING.md)** for logos, colours and type. The default
theme is deliberately neutral so it looks considered before you touch it.

---

## Keeping it current

`dashboard.html` is a snapshot. To refresh, re-run:

```bash
python fetch_dashboard_data.py && python render_dashboard.py
```

Schedule that pair however you like — `cron`, Task Scheduler, a CI job. Three
times on a working day is a reasonable cadence.

`render_dashboard.py` **validates before it writes**: record-count floors, every
configured location present, no cross-location id mixing, all names resolvable,
and data freshness. If a check fails it writes nothing and exits non-zero, so a
half-failed pull leaves your last good dashboard in place instead of replacing
it with something authoritative-looking and wrong.

That matters for unattended runs: **check the exit code**, or a broken fetch
will fail silently and you will be reading stale numbers without knowing.

### Using Claude Code (optional)

If you use Claude Code, you can have it run that pair on a schedule and publish
the result as a shareable artifact, so the link always shows current numbers.
Ask it to set up a scheduled task around the two commands above. Nothing in this
package requires it.

---

## Reading the numbers

Several figures are derived rather than read straight from a field, because the
obvious approach gives a confidently wrong answer. The full reasoning is in the
docstring at the top of `fetch_dashboard_data.py`. The ones most likely to
prompt a question:

- **Avg ticket is measured on calls that billed.** A large share of completed
  jobs bill nothing — warranty and callback work is no-charge by design, and a
  sales visit produces an estimate rather than job revenue. Folding those zeros
  in drags the average far below what anyone actually sold, so the tile shows
  the billed average with revenue-per-dispatch beside it.

- **Close rate counts opportunities, not estimates.** A Good/Better/Best
  presentation writes one estimate per option for a single chance to sell.
  Counting estimates inflates the denominator; on a real tenant this was the
  difference between 35% and 54%.

- **Booked revenue** reads the price on the job, dated to its next *future*
  appointment. Jobs stuck open with no future appointment are excluded rather
  than counted as pipeline — the fetch prints how many it skipped, and a large
  number is a dispatch-hygiene signal.

- **AR excludes undated invoices.** ServiceTitan pre-creates an invoice when a
  job is booked; those carry a balance but no invoice date. They are unbilled
  future work, and counting them both double-counts booked revenue and dumps
  them into the oldest aging bucket as if severely overdue.

- **"Committed on Open POs" is not accounts payable.** ServiceTitan exposes no
  vendor-bills endpoint on standard plans, so this is unreceived-PO spend,
  labeled as such.

- **Membership attach is approximate** — same customer, same day. The obvious
  membership→job link in the API is not a Jobs-table id.

If one person appears twice, run `python check_duplicates.py`.

---

## Files

| File | What it is |
|---|---|
| `config.example.json` | Copy to `config.json`. All company-specific settings. |
| `.env.example` | Copy to `.env`. Credentials only. Never commit. |
| `test_connection.py` | Verifies each location and shows department mapping. |
| `fetch_dashboard_data.py` | Read-only pull → `live_data.json`. |
| `generate_demo_data.py` | Synthetic data → `demo_data.json`. |
| `render_dashboard.py` | Validates, then builds `dashboard.html`. |
| `check_duplicates.py` | Finds people holding two user records. |
| `dashboard_template.html` | The dashboard itself. Theme tokens at the top. |
| `st_config.py` | Shared config/env loading. |

---

## Privacy

`live_data.json` and `dashboard.html` contain **real customer names, employee
names and revenue figures**. Both are gitignored by default. Think before you
share the rendered file — the Sales drill-down and the AR table name customers
and what they owe.

`.env` holds credentials that can read your entire ServiceTitan account. Never
commit it, and rotate the secret if it is ever exposed.

## Requirements

Python 3.9+ and `requests` (`pip install requests`). Nothing else.
