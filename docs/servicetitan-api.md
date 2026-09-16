# ServiceTitan API Reference for servicetitan-cli

**Audience:** engineers and AI agents extending this GET-only ServiceTitan CLI.
**Scope:** what ServiceTitan's own OpenAPI specs, Developer Portal documentation
and (for the reference/planning material in Sections 6-8) this repo's existing
code establish about the API surface the CLI touches. It does not cover write
(`POST`/`PUT`/`PATCH`/`DELETE`) operations except where needed to explain why a
GET-only tool cannot reach them (Section 7) or to describe how a piece of
reference data gets created (e.g. credit memo splits, Section 6.6).

**How to read the citations.** Every material fact is followed by a compact
bracketed source tag. Full paths and URLs are listed in Section 9. Facts
labelled `[confirmed]` come from primary ServiceTitan sources (the 25 official
OpenAPI specs, or the scraped Developer Portal). Facts drawn from secondary,
non-ServiceTitan-authored sources are marked **(secondary)** inline and are
also summarized in the Unverified section. Where research verified that an
assumption in the original dashboard-template scripts was **wrong**, that is
stated explicitly as a correction ("the docs say X, not Y") rather than
repeated as if it were true — see Section 8.

Citation legend:

| Tag | Source |
|---|---|
| `[PD]` | ServiceTitan Developer Portal narrative docs (OAuth/setup guide, FAQ, API-family overview, Invoices/Bills/Memberships walkthroughs, Webhooks) — `portal_docs_full.md` |
| `[TIMEOUTS]` | ServiceTitan's **Partner (supply-chain) API portal** page "Timeouts and Pagination" (`partnerapis.servicetitan.io/docs/timeouts-and-pagination/`). A different ServiceTitan API family from the tenant V2 APIs the 25 specs describe; its figures are general ServiceTitan platform guidance, not confirmed for the tenant endpoints (see Unverified). |
| `[BJS]` | Developer Portal's compiled frontend JavaScript (exact UI copy/enums) — `main_bundle.js` |
| `[BDC]` | Developer Portal frontend doc-string context (client-secret UI copy) — `bundle_doc_context.txt` |
| `[APIS]` | Developer Portal per-module catalogue — `apis.json` |
| `[CAT]` | This research's own flattened catalogue of every GET operation across all 25 specs — `get_catalogue.json` |
| `specs/tenant-<module>.json` | The official ServiceTitan OpenAPI 3 document for that module (`paths`, `components.schemas`, `components.securitySchemes`) |
| `src/servicetitan_cli/<file>` | This repo's own source, cited for cross-reference to what is already implemented |
| Full URL | External, non-ServiceTitan-authored (or ServiceTitan help-center, i.e. product docs rather than developer/API docs) source, always flagged **(secondary)** |

All facts below were re-verified directly against the 25 spec files and the
narrative doc snapshot while writing this document (path counts, enum values,
security schemes, status-code tallies, and parameter lists were re-checked
with `python3`/`grep`, not merely copied from prior notes).


## In one page

The facts that shape this CLI, each detailed and cited below:

| Topic | Fact |
|---|---|
| Environments | Two, fully separate: production (`auth.servicetitan.io`, `api.servicetitan.io`) and integration (`auth-integration...`, `api-integration...`). Client id/secret are per tenant **and** per environment; the app key is global to the app (Sections 1, 2.6). |
| Auth | OAuth 2.0 client credentials only; tokens last 15 minutes, no refresh tokens, and token requests are throttled, so cache the token (Sections 2.1 to 2.4). Every call also needs `ST-App-Key` (2.5). |
| Scopes | `tn.<module>.<resource>:r|w`; read and write are separate grants; the token response lists what was granted (2.7). |
| Setup | Register the app in the developer portal, add tenant ids, pick read scopes; the tenant admin connects it under Settings > Integrations > API Application Access; a secret is shown once; two secrets can coexist for rotation (Section 3). |
| Limits | 600 calls per 10 seconds per app **per tenant**; reporting: one run of the same report per minute per tenant. No 429 body, header or retry guidance is documented (Section 4). |
| Errors | RFC 7807-style `{type,title,status,traceId,errors,data}`; 403 is the documented authentication failure, 401 is never documented (4.3). |
| Lists | `page` (1-based) / `pageSize` (default 50, ceiling undocumented for most entities) / `includeTotal`; envelope `{page,pageSize,hasMore,totalCount,data}`; only `hasMore` is authoritative (5.1). `sort=+Field` single field (5.2). `active` takes `True|Any|False` and defaults to active only (5.3). |
| Export feeds | `/export/<entity>` with `from`/`continueFrom`/`hasMore`, no filters, includes inactive and deleted rows, ~15 minute lag unless `includeRecentChanges=true` (5.5). |
| Versioning | Modules are versioned in the URL path (`/jpm/v2`); telecom carries `/v2` and `/v3` side by side with the v2 list tagged deprecated but still served. No written compatibility or sunset policy exists (Section 1a). |
| Reporting | Report data is POST-only, so a GET-only tool can discover reports but never run them (Section 7). |
| Corrections | The template's invoice filter name was wrong (`invoicedOnOrAfter` is correct), and vendor bills do exist (`ap-bills`, `inventory-bills`); revenue from `job.total` is gross of credit memos (Section 8). |

## 1a. Versioning and backward compatibility

What ServiceTitan documents, and what it does not:

- **Versions live in the URL path, per module.** Every module's server URL
  carries its version (`/jpm/v2`, `/accounting/v2`, ...) except telecom, whose
  host is unversioned and whose paths are versioned individually
  (`/v2/tenant/{tenant}/calls`, `/v3/tenant/{tenant}/calls`) `[specs/*.json]`.
- **Breaking changes ship as new versions next to the old.** The telecom v3
  calls list standardises parameters that v2 handled inconsistently; the v2
  list is tagged "Calls (Deprecated)" in the spec yet remains served and
  documented, and `GET /v2/.../calls/{id}` is not deprecated (Section 5.4).
  No removal date is published for it.
- **Changes within a version have been additive.** Newer parameters sit
  beside legacy ones (`sort` next to `orderBy`/`orderByDirection` on
  Invoices; both `createdAfter` and `createdOnOrAfter` on the v2 calls list),
  which is how a client written against the older shape keeps working
  (Section 5.2).
- **"App versions" are a different thing.** Changing an app's scopes creates
  a new app version that each tenant admin must approve; existing connections
  keep working on the version they approved (Section 3.6). This is about
  permissions, not endpoint compatibility.
- **No written guarantee.** The developer portal publishes release notes but
  no compatibility promise, deprecation window or sunset schedule for
  endpoints; the OpenAPI documents all declare `info.version: 1.0` regardless
  of path version, so that field carries no information `[PD; specs/*.json]`.
  Webhooks are the one place a deprecation is announced in prose (V1
  webhooks "will be deprecated in the future", timelines to follow) `[PD]`.

How this CLI handles it:

- The command tree is generated from the OpenAPI documents, and each
  operation records its path version. Where a resource exists in several
  versions the newest gets the plain name and older ones a suffix
  (`servicetitan api telecom calls list` is v3, `list-v2` is the deprecated
  v2 list); `--describe` shows `deprecated` and `replacedBy`.
- The packaged catalogue is a dated snapshot of the specs.
  `servicetitan api catalogue status` shows its date and per-module hashes;
  `servicetitan api catalogue refresh` downloads the current documents into
  the config directory, which then takes precedence, and reports added,
  removed and changed operations, parameters, enum values, response fields
  and scopes; `reset` returns to the packaged copy. A CI job can run
  `refresh --dry-run` to detect drift.
- Snapshots written by older CLI versions still load: every reader treats
  fields as optional and the snapshot carries `cli.version`. Snapshots and
  dashboards produced by the original template scripts also load unchanged.

---

## 1. Environments and base URLs

ServiceTitan has exactly two environments, **integration** (sandbox/testing)
and **production**, and they are fully separate end to end: separate OAuth
token hosts, separate resource-API hosts, and separate Client ID/Secret pairs
per tenant (Section 2.6) `[PD]`.

| Environment | OAuth token endpoint | Resource API host |
|---|---|---|
| Integration | `https://auth-integration.servicetitan.io/connect/token` | `https://api-integration.servicetitan.io` |
| Production | `https://auth.servicetitan.io/connect/token` | `https://api.servicetitan.io` |

Confirmed identical in every one of the 25 specs' `securitySchemes.oauth.flows.clientCredentials.tokenUrl`
(production) and in the narrative doc's own environment table for both token
URLs `[PD; specs/*.json]`.

Every resource API module publishes its own `{host}/{module-path}` pair as an
OpenAPI `servers` entry, and — with one exception — that path bakes in a
version segment (`v2`). Re-verified directly against all 25 spec files:

| Area (this CLI's target entities) | Module (spec file) | Path segment | Example full production URL |
|---|---|---|---|
| Jobs, Appointments, job types | `tenant-jpm-v2` | `/jpm/v2` | `https://api.servicetitan.io/jpm/v2` |
| Appointment Assignments (dispatch) | `tenant-dispatch-v2` | `/dispatch/v2` | `https://api.servicetitan.io/dispatch/v2` |
| Estimates | `tenant-salestech-v2` | `/sales/v2` | `https://api.servicetitan.io/sales/v2` |
| Invoices, Payments, Credit Memos, AP/Inventory Bills, Payment Terms | `tenant-accounting-v2` | `/accounting/v2` | `https://api.servicetitan.io/accounting/v2` |
| Purchase Orders, Vendors | `tenant-inventory-v2` | `/inventory/v2` | `https://api.servicetitan.io/inventory/v2` |
| Campaigns, Campaign Costs | `tenant-marketing-v2` | `/marketing/v2` | `https://api.servicetitan.io/marketing/v2` |
| Customer Memberships, Membership Types | `tenant-memberships-v2` | `/memberships/v2` | `https://api.servicetitan.io/memberships/v2` |
| Technicians, Employees, Business Units | `tenant-settings-v2` | `/settings/v2` | `https://api.servicetitan.io/settings/v2` |
| Customers | `tenant-crm-v2` | `/crm/v2` | `https://api.servicetitan.io/crm/v2` |
| Calls | `tenant-telecom` | `/telecom` **(no version segment)** | `https://api.servicetitan.io/telecom` |
| Call Reasons (lookup only) | `tenant-jbce-v2` | `/jbce/v2` | `https://api.servicetitan.io/jbce/v2` |
| Reports (Section 7) | `tenant-reporting-v2` | `/reporting/v2` | `https://api.servicetitan.io/reporting/v2` |

`[specs/tenant-jpm-v2.json, tenant-dispatch-v2.json, tenant-salestech-v2.json, tenant-accounting-v2.json, tenant-inventory-v2.json, tenant-marketing-v2.json, tenant-memberships-v2.json, tenant-settings-v2.json, tenant-crm-v2.json, tenant-telecom.json, tenant-jbce-v2.json, tenant-reporting-v2.json — each file's `servers` array, re-verified directly]`

**Telecom is the one structural exception**, and the exception is worth
internalizing precisely: its *server* URL carries no version at all
(`.../telecom`), but the *paths* underneath it are individually versioned —
`/v2/tenant/{tenant}/calls` and `/v3/tenant/{tenant}/calls` both exist side by
side under the same host `[specs/tenant-telecom.json]`. Every other module
bakes its version into the server URL once, so a base-URL-per-module map that
assumes "host = one fixed version" will silently mis-template Calls URLs. See
Section 5.4 for why the CLI should use `/v3/.../calls` for transactional
listing but `/v2/.../export/calls` for bulk export.

**Accounting and Inventory are two different API modules, not sub-paths of
one "AP" API.** Accounting's own module description is "AP bills, AP credits,
AP payments, bank deposits, credit memos, expense transactions, GL accounts,
inventory bills, invoices, journal entries, payments, payment terms, payment
types, remittance vendors, tax zones"; Inventory's is "Adjustments, inventory
templates, purchase orders, purchase order markups, purchase order types,
receipts, returns, return types, transfers, trucks, vendors, warehouses"
`[specs/tenant-accounting-v2.json, tenant-inventory-v2.json — info.description, re-verified]`.
A complete AP/vendor-spend picture requires calling both hosts: **Vendors and
Purchase Orders live under `/inventory/v2`; AP Bills, Inventory Bills, Credit
Memos and Vendor/AP Payments live under `/accounting/v2`.**

**Tenant ID** is a plain numeric path parameter (OpenAPI `type: integer,
format: int64`) required on every resource path, in the form
`/tenant/{tenant}/<resource>` — e.g. `/jpm/v2/tenant/{tenant}/jobs`
`[specs/tenant-jpm-v2.json — path parameter "Tenant ID"]`. Store/validate it as
an integer and build every request path the same way across modules.

**HTTPS is mandatory** for every call; there is no HTTP fallback: "Any call
you make to a ServiceTitan API resource must use the HTTPS scheme in the URL"
`[PD]`. Reject or normalize an accidental `http://` base URL at config-load
time rather than letting it fail late as an ambiguous connection error.

---

## 2. Authentication

### 2.1 Grant type

ServiceTitan supports **only the OAuth 2.0 Client Credentials grant**
(machine-to-machine): "Currently ServiceTitan supports only Client
Credentials Grant Type and its related use cases only" `[PD]`. There is no
authorization-code/user-login step, and no refresh tokens are ever issued:
"the ServiceTitan API employs only access tokens and does not issue refresh
tokens as we are supporting only machine-to-machine calls under OAuth 2.0
client credentials grant type" `[PD]`. A GET-only client never needs
redirect/callback handling or a `refresh_token` field.

### 2.2 Token request

`POST` to the environment's token endpoint (Section 1), `Content-Type:
application/x-www-form-urlencoded`, exactly three form fields — no other
headers or body format are documented `[PD]`:

```
curl --request POST \
  --url https://auth-integration.servicetitan.io/connect/token \
  --header 'Content-Type: application/x-www-form-urlencoded' \
  --data grant_type=client_credentials \
  --data client_id=%client_id% \
  --data client_secret=%client_secret%
```

### 2.3 Token response and lifetime

JSON body with `access_token`, `expires_in`, `token_type`, and `scope` (a
string listing the granted scope(s), e.g. `"tn.acc.invoices:r"`) `[PD]`:

```json
{"access_token": "eyJhbGciOiJSUzI1NiIs...", "expires_in": 900, "token_type": "Bearer", "scope": "tn.acc.invoices:r"}
```

`expires_in` is **900 seconds (15 minutes) by design, always**, and there is
no refresh token — once expired, the only recovery path is re-POSTing
`client_id`/`client_secret` to the token endpoint again: "the ServiceTitan
authorization server issues access tokens which expire in 15 mins. Once the
initial access token expires, your machine will need to call the ServiceTitan
endpoint again using the ClientID and Client Secret to obtain a new access
token" `[PD]`. Re-authentication is identical in shape to the first
authentication — there is no separate "refresh" flow to implement.

The CLI can read the returned `scope` string to know exactly which requested
read scopes were actually granted for that tenant's token, before attempting
a resource call that would otherwise fail on a missing scope `[PD]`.

### 2.4 Caching and throttling

ServiceTitan explicitly recommends caching the token and warns that it
throttles *token* requests separately from the resource-API rate limit: "We
recommend you cache the access token instead of requesting a new token for
every transaction, since ServiceTitan employs throttling of API requests to
new access tokens" `[PD]`. A design that mints a fresh token before every HTTP
call risks being throttled at the token endpoint itself, independent of the
600-calls/10s resource-API limit (Section 4.1). **Cache one token per (tenant,
environment) pair for up to ~15 minutes**, refreshing a little early (this
repo's own client refreshes with a margin and stores tokens on disk, keyed by
environment + client id — `src/servicetitan_cli/api.py: TOKEN_LIFETIME_S = 15*60`,
`TOKEN_REFRESH_MARGIN_S = 60`).

### 2.5 Headers required on every resource call

Two headers, always, with zero documented exceptions:

1. **`Authorization`** — the bearer token from Section 2.3.
2. **`ST-App-Key`** — the application's App Key (Section 2.6), sent as a
   plain header value, formally declared in every one of the 25 specs as an
   `apiKey`-type security scheme:
   `{"type": "apiKey", "name": "ST-App-Key", "in": "header", "description": "ServiceTitan App Key Header"}`
   `[specs/tenant-jpm-v2.json — components.securitySchemes.apiKeyHeader, identical in every spec, re-verified]`.

Every one of the 600 documented operations across all 25 specs declares the
identical two-part security requirement — `apiKeyHeader` **and** `oauth` (with
a specific scope) — confirmed by re-tallying `security` blocks directly: 600
operations, exactly one distinct pattern, zero operations missing a security
block `[specs/*.json]`. Example, the Jobs list endpoint:
`"security": [{"apiKeyHeader": [], "oauth": ["tn.jpm.jobs:r"]}]`
`[specs/tenant-jpm-v2.json]`. **The CLI can safely hard-code "send both
headers on every call" with no per-endpoint exceptions.**

The Developer Portal's own worked curl example sends the raw token without a
visible `Bearer ` prefix (`--header 'Authorization: %access_token%'`) even
though the token response's `token_type` is `"Bearer"` `[PD]`. Neither the
narrative docs nor the OpenAPI security-scheme definitions state the exact
Authorization header format either way — **this is not confirmed in the
primary sources** (see Unverified, below). Standard OAuth 2.0 (RFC 6750)
practice for `token_type: Bearer` is `Authorization: Bearer <token>`, and this
repo's own client sends it that way (`src/servicetitan_cli/api.py: "Authorization": "Bearer %s" % self.token()`)
— treat that as the working convention, but a live smoke test is the only
real confirmation.

### 2.6 What is global vs. per-tenant vs. per-environment

Five distinct values fully describe how the CLI authenticates one tenant
against one environment; nothing else (no username/password) is needed for
machine-to-machine calls `[PD; specs/tenant-jpm-v2.json]`:

| Value | Scope | Notes |
|---|---|---|
| **App Key** | Global to the whole registered application; works for **both** environments | "The app key is not environment specific. Your app key generated in the integration environment works for production calls and vice versa." Generated once, automatically, when an app's tenant IDs and scopes are first saved during registration `[PD]`. |
| **Client ID** | One per (tenant, environment) | "Each tenant ID requires its own unique client ID and client secret pair. Additionally, the client ID, client secret, and access token are specific to their respective environments." `[PD]` |
| **Client Secret** | One per (tenant, environment), paired with that Client ID | Shown once at generation time only (Section 3). |
| **Tenant ID** | Numeric identifier of the customer's ServiceTitan account | Path parameter on every call (Section 1). |
| **Environment** | Explicit selector: `integration` or `production` | Silently determines the token host, the resource-API host, *and* which of the tenant's Client ID/Secret pairs applies. Never infer it. |

Config schemas should key `client_id`/`client_secret` by **(tenant_id,
environment)**, not tenant_id alone, and store exactly one `app_key`
globally. This matches this repo's own README, which independently states
the same four collectible values ("You need four values per tenant: app key,
client id, client secret and tenant id" `[README.md]`) plus environment as
the implicit fifth selector in its config model (`src/servicetitan_cli/config.py: Credentials.environment`).

**Exception — Enterprise Hub:** a single Client ID/Secret pair can cover
*multiple* tenants, but only if the ServiceTitan customer has configured an
"Enterprise Hub" tenant network via their own API & Integrations module, and
"Enterprise Hub is only offered to ServiceTitan customers and not to
third-party partners and developers" `[PD]`. Otherwise each tenant needs its
own credential pair even under one parent company — but a credential schema
should still *allow* (not require) an N:1 tenant-IDs-to-one-credential-pair
mapping for Enterprise Hub customers.

Since the App Key represents the *application*, not a tenant, one App Key
covers unlimited customer tenants that connect to that app: "if you have an
application that will be used by 10 ServiceTitan customers/tenants, you need
to obtain 10 client IDs and client secrets but can use the same app key"
`[PD]`.

### 2.7 OAuth scopes

Scope names follow the pattern **`tn.<module-code>.<resource>:<r|w>`** (e.g.
`tn.acc.invoices:r`, `tn.jpm.jobs:w`). Aggregating `securitySchemes.oauth.flows.clientCredentials.scopes`
across all 25 specs yields 213 distinct scope strings `[PD; specs/*.json]`.
A `tn\.[a-z]+\.[a-z]+:[rw]` regex reliably parses any granted scope back into
module/resource/access.

**46 of 135 distinct scope "resource" bases exist only as Read (`:r`), with no
matching Write (`:w`) scope at all** — meaning those resource types cannot be
written via the API by *any* app, regardless of requested scopes (11 bases
are write-only; 78 have both) `[specs/*.json]`. Examples of read-only-by-design
resources: `tn.acc.taxzones:r`, `tn.acc.paymentterms:r`, `tn.jpm.jobhistory:r`,
`tn.jpm.jobnotes:r`, `tn.jpm.jobcancelreasons:r`, `tn.mem.membershiptypes:r`,
`tn.jpm.projectstatuses:r`. This corroborates narrative statements that some
reference data (tax zones, payment terms, etc.) must be created in the
ServiceTitan UI only — confirmed at the platform level, not merely a
self-imposed restriction of this CLI.

The minimal read-scope set for this CLI's 12 target entities:

| Entity | Scope | Description (from spec) |
|---|---|---|
| Jobs | `tn.jpm.jobs:r` | Job Planning and Management → Jobs (Read) |
| Appointments | `tn.jpm.appointments:r` | Job Planning and Management → Appointments (Read) |
| Estimates | `tn.sal.estimates:r` | Sales & Estimates → Estimates (Read) |
| Invoices | `tn.acc.invoices:r` | Accounting → Invoices (Read) |
| Purchase Orders | `tn.inv.purchaseorders:r` | Inventory → Purchase Orders (Read) |
| Calls | `tn.tlc.calls:r` | Telecom → Calls (Read) |
| Campaigns | `tn.mrk.campaigns:r` | Marketing → Campaigns (Read) |
| Customer Memberships | `tn.mem.memberships:r` | Memberships → Customer Memberships (Read) |
| Technicians | `tn.stt.technicians:r` | Settings → Technicians (Read) |
| Employees | `tn.stt.employees:r` | Settings → Employees (Read) |
| Business Units | `tn.stt.businessunits:r` | Settings → Business Units (Read) |
| Customers | `tn.crm.customers:r` | CRM → Customers (Read) |

`[specs/tenant-jpm-v2.json, tenant-salestech-v2.json, tenant-accounting-v2.json, tenant-inventory-v2.json, tenant-telecom.json, tenant-marketing-v2.json, tenant-memberships-v2.json, tenant-settings-v2.json, tenant-crm-v2.json]`

Requesting only these `:r` scopes (never the paired `:w` ones) both matches a
GET-only design and shows the tenant admin the smallest, easiest-to-approve
permission list at connect time (Section 3.3).

### 2.8 Which error means "authentication failed"?

Short answer for this section: **403, not 401**, is what ServiceTitan
documents — see Section 4.3 for the full error-handling picture, because in
practice a real client must treat both codes (plus a bare, undocumented 429)
as potential auth/permission failures.

---

## 3. Setting up API access, step by step

### 3.1 Prerequisite ServiceTitan permissions

Two distinct ServiceTitan permissions govern the whole setup flow, and a
normal office-staff login has neither by default:

| Permission | Unlocks | Scope |
|---|---|---|
| **Generate API Application Key** | Signing into Developer Portal and registering apps/app keys | "shared among all users in this tenant who have this permission" `[PD]` |
| **Manage API Application Access** | The `Settings > Integrations > API Application Access` screen in the main ServiceTitan app, where client IDs/secrets are created to connect an app | Per the account, used to "authenticate apps that call V2 APIs" `[PD]` |

Both are granted the same way, **inside the main ServiceTitan app** — there is
no separate "developer signup": `Settings > People > Employees > Add (or
edit) an employee > Permissions tab`, check one or both boxes `[PD]`.
Developer Portal login then uses that **same** integration- or
production-environment employee username/password; sign-in is at
`https://integration.servicetitan.com/integration` or
`https://go.servicetitan.com/production` depending on which environment's
credentials are needed `[PD]`. When instructing a new customer, be explicit
that whoever connects the app needs **Manage API Application Access** —
typically an account admin, not general staff.

### 3.2 Register the app in Developer Portal (one-time, per application)

`My Apps > Register New App`, then provide `[PD]`:

| Input | Notes |
|---|---|
| Application name | |
| Organization name | |
| Organization home page URL | |
| Tenant ID(s) | The customers this app should be visible to. **Addable/removable at any time** after registration — onboarding a new customer later never requires re-registering the app, just adding their tenant ID `[PD]`. |
| API scopes (Read and/or Write) | Choose the minimal read-only set from Section 2.7. |

**Client Credentials Management mode** (a one-time, per-application choice,
not per-tenant) — ServiceTitan's own frontend defines this as an enum
`SecretManagementOption` with two values:

| Mode | Who generates the Client ID/Secret | UI label |
|---|---|---|
| `ResourceOwner` (= 0, **default**) | The tenant admin, at Connect/Allow time | (implicit — no explicit selection needed) |
| `Developer` (= 1) | The app developer, from Developer Portal, after the tenant admin connects | "I, the app developer, will configure the credentials on behalf of each tenant" |

`[BJS — SecretManagementOption["Developer"]=1; PD — exact UI label]`

The App Key is generated **automatically**, once, when the app's tenant IDs
and scopes are first saved — there is nothing separate to "generate" for the
key itself `[PD]`.

**Public/marketplace ("3P") apps only:** ServiceTitan performs a review
"once a week... a few business days" before a newly registered app becomes
visible to *any* customer's Connect New App list at all. This is a one-time
gate — "additional approval is not needed every time you add a tenant ID or
change the API scopes and create a new version" `[PD]` — **except** that for
apps registered under a *certified* 3P/Marketplace developer account
specifically, every *future* scope change re-enters a separate governance
review ("Review in Progress" → Approved/Rejected, one pending version at a
time) before customers can even see the new version; ordinary customer-built
(private) apps auto-approve scope changes with no such review `[PD]`. **If
this CLI's app will only ever serve a fixed, known set of tenants, register
it as a private/customer app**, not a certified 3P app, to avoid that
recurring review on every future scope addition.

**Third-party developers only ever receive integration-environment
credentials for themselves** — to call production on behalf of a real
customer, that customer must generate and hand over their own production
Client ID/Secret; a third party cannot self-generate production credentials
`[PD]`. Relatedly, **"customer tunneling" is contractually prohibited**:
asking a ServiceTitan customer to create a user or register an app *inside
their own* integration/production environment is a documented policy
violation. The app must be registered once, inside the *operator's own*
ServiceTitan-provided account; each customer then separately connects
(Allows) that already-registered app from their own environment `[PD]`.
Because this CLI is designed to hold many different customers' (tenants')
credentials, its operator is functionally exactly this kind of third-party
integrator. ServiceTitan also explicitly warns: never grant a third party
"Admin" access or the "Generate API Application Key" permission, and never
register an app on a third party's behalf and hand them the App Key — both
are called out as security violations `[PD]`. Customer-facing onboarding
material should never ask a customer to create a login for, or hand an App
Key to, the CLI operator's counterparty.

### 3.3 Connect the app inside each customer's ServiceTitan account

Customer-side sequence, exact and verbatim from the UI `[PD; BJS]`:

1. Main nav: `Settings > Integrations > API Application Access`.
2. Click **Connect New App**.
3. Choose the app from the list.
4. Review the requested scopes, shown split into **Read:** / **Write:**
   lists ("Has Access To").
5. Click **Allow** or **Decline**.

What happens next depends on the Client Credentials Management mode chosen
in Section 3.2:

| | ResourceOwner (default) | Developer (self-service) |
|---|---|---|
| On Allow | Client ID + Secret are generated **immediately** and shown right there on the Connect screen | Only permission is granted; **no secret is shown** to the tenant admin |
| Who relays credentials | Tenant admin must securely share Client ID, Secret, **and Tenant ID** with the developer `[PD]` | Developer goes to `Developer Portal > My Apps > App Connections` for that app, where the tenant's Client ID is now visible, and clicks **Generate** under Client Secret `[PD]` |
| Before Allow happens | N/A | Tenant shows status **"Not Connected Yet"** on the developer's screen, with no Generate option `[PD]` |
| Onboarding instructions to give a customer | "Your admin clicks Allow, then copies the Client ID/Secret shown right there, plus your Tenant ID, back to us." | "Your admin clicks Allow first; only then can we generate your secret from our side." Order matters — generating before Allow is not possible. |

A generated Client Secret is shown **exactly once**, in a one-time reveal
dialog, with no way to view it again afterward: "Once a client secret is
generated, it's visible only once... If you lose it, you'll need to generate
a new secret" `[BDC]`. Capture it into the credential store the instant it is
generated.

**Zero-downtime rotation** is supported natively: up to **two** client
secrets can exist concurrently per Client ID. ServiceTitan's own UI disables
the "Generate" control once two already exist, and a separate "Deactivate"
action invalidates a chosen secret `[BDC]`. Recommended rotation order:
generate the replacement secret first, switch the stored config over, verify
it authenticates, *then* deactivate the old one.

If a tenant admin can't find the app on their Connect New App list, either
(a) the developer hasn't added that tenant's ID in Developer Portal yet, or
(b) — public/3P apps only — ServiceTitan's weekly review hasn't completed
`[PD]`.

### 3.4 The four (really five) values, recap

| # | Value | Collected from |
|---|---|---|
| 1 | App Key | Developer Portal, once, global |
| 2 | Client ID | Tenant admin (ResourceOwner mode) or Developer Portal App Connections (Developer mode) — per (tenant, environment) |
| 3 | Client Secret | Same as Client ID — per (tenant, environment), one-time reveal |
| 4 | Tenant ID | Tenant admin (visible in their own ServiceTitan account) |
| (5) | Environment | Not "collected" — an explicit choice the operator must record alongside 2-4 |

This matches the repo's own onboarding copy verbatim: "You need four values
per tenant: **app key, client id, client secret** and **tenant id**"
`[README.md]`, with environment tracked as an implicit fifth field per
credential record (`src/servicetitan_cli/config.py`).

### 3.5 Pausing and offboarding

| Action | Where | Effect | Reversible? |
|---|---|---|---|
| **Disable** | Customer: `Settings > Integrations > API Application Access > select app > Disable > Confirm` | Pauses that tenant's access; credentials are *not* deleted | Yes — "Re-enable Access" button `[PD; BJS]` |
| **Delete the app** | Developer: Developer Portal | Permanently deletes the **App Key** — ServiceTitan can no longer identify the application at all, for *any* tenant | No — full re-onboarding with a brand-new App Key |

A tenant that suddenly starts failing auth may simply have disabled the app
(recoverable) rather than the app having been deleted (unrecoverable,
requires re-registration) — worth distinguishing in troubleshooting output,
though the API gives no direct signal to tell the two apart (Section 4.3).

### 3.6 Adding a new resource/scope to an already-registered app

Changing an app's scopes creates a new **incremental app version** (v1, v2,
v3, ...). Existing tenants keep working unchanged, but the *new* scope's
calls will keep failing for any tenant whose admin has not separately gone to
`Upgrade to New Version` and clicked Allow on the diff (added scopes shown in
green, removed ones struck through) — no new App Key, Client ID or Client
Secret is required for a scope change, only this per-tenant re-approval step
`[PD]`. **Recommendation:** give the CLI a specific error message along the
lines of "this scope is not yet granted — ask your ServiceTitan admin to
upgrade the app" distinct from a generic auth failure, since the fix is
"click Upgrade," not "reconnect."

---

## 4. Rate limits and error handling

### 4.1 Rate limits

> "Are you implementing any rate limiting on API calls? Yes. Our current
> default is **600 calls per 10 seconds per application per tenant** for all
> APIs except reporting APIs. For reporting APIs, the limit is **1 of the
> same report per minute per tenant**. Please contact us if you have any
> concerns about this. We will evaluate on a case-by-case basis." `[PD]`

The throttle key is the **(application, tenant) pair** — not global-per-app
and not global-per-tenant. A multi-tenant CLI gets an independent budget per
tenant it holds credentials for; a single worker pool per tenant is fine as
long as its aggregate call rate respects ~60 req/s. This repo's own client
already implements exactly this: `RATE_LIMIT_CALLS, RATE_LIMIT_WINDOW_S = 600, 10.0`
in `src/servicetitan_cli/api.py`, throttling per client instance.

This number is independently corroborated by ServiceTitan's public help
center, in different-but-equivalent units, on a page dated 2026-05-14: "the
standard rate limit is 60 calls per second per application per tenant" (=
600/10s) and "the limit is 1 of the same report per minute per tenant" —
verbatim match **(secondary — help.servicetitan.com/docs/default-api-rate-limitsfor-regular-apis-and-reporting-apis)**.

**Correction:** two AI-generated web-search summaries, and a third-party
Zapier help article, state the reporting limit as "**5** of the same report
per minute per tenant." Both ServiceTitan-authored sources found (the
Developer Portal FAQ and the help-center page above) say **1**, not 5 — trust
**1 call/minute/tenant/report** and disregard the "5" figure
**(the "5" claim: secondary, contradicted by two ServiceTitan-authored sources)**.

The Reporting API additionally caps **concurrent long-running async queries**
per tenant via a "per-tenant slot" — cancelling one (`DELETE .../data-queries/{token}`)
"frees the per-tenant slot" `[specs/tenant-reporting-v2.json]` — a separate axis
from the 1/minute-same-report throttle. See Section 7.

No general maximum-concurrent-connections limit (distinct from the
requests-per-10-seconds throughput cap) is documented anywhere in the
Developer Portal docs or any of the 25 specs `[PD; specs/*.json]`.

### 4.2 What ServiceTitan does *not* document: 401, 429, 5xx

Re-tallying every operation's declared `responses` object across all 25
specs (600 operations total) gives exactly this distribution, with **zero**
occurrences of `401`, `429`, or any `5xx` code anywhere:

| Status | Count | Status | Count |
|---|---|---|---|
| 200 | 582 | 302 | 3 |
| 400 | 594 | 201 | 12 |
| 404 | 215 | 204 | 9 |
| 409 | 65 | 202 | 2 |
| 403 | 29 | 422 | 1 |
| **401** | **0** | **429 / 5xx** | **0** |

`[specs/*.json — re-verified directly, all 25 files, "responses" key on every path-method object]`

The narrative Developer Portal docs are equally silent: they never use the
words "401", "403", or "forbidden" anywhere, and "unauthorized" appears
exactly once, in an unrelated sentence about webhook HMAC signature
verification — not about API auth errors `[PD]`. **No Retry-After header,
example 429 response body, or ServiceTitan-specific 429-handling guidance for
resource-API calls exists in any primary source**; a direct fetch of
ServiceTitan's own public rate-limit help page independently confirms it too
"contains no information provided regarding status codes, headers, or retry
guidance when limits are exceeded" **(secondary corroboration, help.servicetitan.com)**.

**Practical consequence:** the API's official documentation cannot tell a
client how to distinguish "bad App Key" from "missing scope" from "tenant
never connected" by status code alone, nor whether a 429 will ever carry a
usable header. Treat any 400/401/403/429/5xx generically and defensively
rather than branching on a documented shape the way 400/404/409 can be
(Section 4.3) — this repo's client already does exactly that: on a live 401
it forces one token refresh and retries once before surfacing `AuthError`; on
429 it reads `Retry-After` if present but falls back to capped exponential
backoff if absent; on 5xx it retries with backoff (`src/servicetitan_cli/api.py`).

### 4.3 The one documented error shape: `ApiErrorResponse`

Every documented error response (400, 403, 404, 409, 422) across every spec
uses one shared schema, described as RFC 7807-flavored but **not** the
canonical RFC 7807 field set — no `detail`/`instance`, instead:

```json
{"type": "...", "title": "...", "status": 400, "traceId": "...", "errors": {"fieldName": ["message"]}, "data": {}}
```

Required fields: `type`, `title`, `status` (the HTTP status, duplicated
inside the body), `traceId`, `errors` (map of field name → message strings),
`data` `[specs/tenant-accounting-v2.json and identical in all 25 files — components.schemas.ApiErrorResponse, required list re-verified: `["type","title","status","traceId","errors","data"]`]`.
Build **one** shared error parser for this shape across every module: surface
`title` (+ `errors` entries) for a human-readable cause, and always log
`traceId` for any ServiceTitan support ticket.

| Code | Count | What it means here |
|---|---|---|
| **400** | 594 | Generic validation failure; the platform's default "something about this request is wrong" response. Body follows `ApiErrorResponse`. |
| **403** | 29 | **ServiceTitan's de-facto stand-in for 401.** Every occurrence carries the identical text: *"Provided authentication is insufficient to grant access."* In the *spec text* these 29 are confined to CRM contacts/relationships and Equipment Systems endpoints only — but since no operation anywhere documents a real 401, and this repo's own client comment notes the same conclusion, **treat any 403 on any endpoint as potentially meaning:** expired/missing bearer token, missing/wrong `ST-App-Key`, or a valid token whose granted scope excludes that resource — not only "insufficient scope." A `status`/setup-check command should validate token mint, `ST-App-Key` acceptance, and per-resource scopes as three separate checks rather than trusting the status code to disambiguate. |
| **404** | 215 | Very common; "entity not found" on by-ID lookups, with a resource-specific message (e.g. "Customer not found", "Job appointment was not found"). For a read-only snapshot tool this is an expected, non-fatal condition (record deleted/renumbered between a list call and a detail call) — surface it as "not found," don't retry it, don't treat it as auth/rate-limit related. |
| **409** | 65 | Mostly genuine state conflicts (e.g. "Appointment is already confirmed"), but a few messages are really not-found-style ("Unknown customer", "Unknown job ID was specified") reused on 409 instead of 404. Almost exclusively a write-path code; non-retryable if ever seen. |
| **422** | 1 | Essentially unused — appears exactly once in the entire platform (an Accounting expense/custom-field validation case). No special handling needed beyond folding it into the 400 bucket. |

### 4.4 Timeouts

ServiceTitan's Partner-API portal documents a server-side request timeout and
recommends matching it: "All of our APIs have timeouts to ensure ServiceTitan
fails as soon as possible and allow you to retry the request... Our APIs
support a timeout of **100 secs**" `[TIMEOUTS]` (stated for the partner API
family; the tenant V2 specs state no timeout at all, so treat 100 s as the
best available guidance rather than a contract). Set the HTTP client's own timeout to
~100 seconds so a hung call is detected and retried rather than waited on
indefinitely. Anything that could legitimately run longer than that (large
custom reports) uses the async 202-plus-poll pattern instead of one long
request (Section 7), not a longer timeout.

### 4.5 Recommended retry/backoff policy

Because ServiceTitan documents no numeric backoff schedule for resource-API
retries, the closest concrete cadence it states anywhere is for its own
**outbound webhook** delivery retries: failed webhook deliveries (non-2xx)
are retried "at intervals of 10, 30, 60, and 300 seconds," after which manual
replay is needed `[PD]`. This is a reasonable model to mirror for the CLI's
own capped exponential backoff with jitter on 429/5xx/timeouts — start small
(~1-2s), double, cap in the 60-300s range — since ServiceTitan itself treats
"a few minutes, capped" as an acceptable retry horizon elsewhere in the same
platform.

### 4.6 A documented counter-example: hard caps do exist elsewhere

None of the CLI's 12 target entities document a request-timeout or
total-record-cap of their own. The clearest hard caps found anywhere in the
corpus belong to unrelated APIs: Fleet Pro (GPS/vehicle telemetry) documents
a max 24-hour query window, vehicles must be filtered ≤50 VINs, and results
are "capped at 10,000 records" regardless of how many really exist
`[specs/tenant-fleet-pro-v2.json]`; separately, a 50-item cap on `ids` filters
appears on 56 other GET operations (Section 5.3), and explicit `pageSize`
schema maximums of 500 (one Settings sub-resource) and 25,000 (Reporting)
also exist (Section 5.1). **Read "undocumented" for the 12 target entities as
"undocumented," not "unlimited"** — stay defensive (checkpoint/resume
support) for very large tenants regardless.

---

## 5. Pagination, filtering, sorting, and the export endpoints

### 5.1 Standard transactional list envelope

Every non-export paginated GET list endpoint returns the identical envelope:

```json
{"page": 1, "pageSize": 50, "hasMore": true, "totalCount": 1234, "data": [ ... ]}
```

`page`, `pageSize`, `hasMore`, `data` are **required**; `totalCount` is
**optional/nullable** ("can optionally be populated") `[specs/tenant-accounting-v2.json — schema PaginatedResponse_Of_Accounting.V2.ApBillResponse, identical shape independently confirmed in tenant-crm-v2.json, tenant-memberships-v2.json, tenant-settings-v2.json, tenant-telecom.json, tenant-marketing-v2.json]`.
A generic list-fetch loop can hard-code this envelope for all ~226 "paged" GET
endpoints across all 25 modules.

| Parameter | Behavior |
|---|---|
| `page` | **1-based page NUMBER**, not a byte/record offset: "The logical number of page to return, starting from 1" (167 occurrences, verbatim) `[CAT; specs/tenant-accounting-v2.json]`. Start at 1, increment by 1. |
| `pageSize` | Documented default **50** in prose on 139/330 GET ops (25 more have a blank description) — **never** encoded as a machine-readable JSON-Schema `default` anywhere in any spec `[specs/*.json — zero `default` keywords found on any pageSize schema]`. The CLI cannot introspect a default at runtime; hard-code 50. |
| `includeTotal` | Boolean, "Whether total count should be returned." Present on 164/330 GET ops; **absent** from a meaningful minority, e.g. the legacy Telecom v2 Calls list `[CAT; specs/tenant-telecom.json]`. A "get total count" helper must check per-endpoint and fall back to counting via a `hasMore` loop where absent. |

**pageSize maximums — a genuinely inconsistent picture, re-verified directly:**

| Where a maximum is stated | How | Value |
|---|---|---|
| Prose only, 3 GET ops, all in Accounting's journal-entries family (`JournalEntries_GetList/GetSummary/GetDetails`) | Free text: "(50 by default). Maximum value is 500." | 500 (**not** schema-enforced) |
| JSON-Schema `maximum` keyword, exactly one place platform-wide outside Reporting | Settings' `/business-units/intacct` sub-resource | 500 |
| JSON-Schema `maximum` keyword, Reporting's two data-fetch endpoints (Section 7) | — | 25,000 |
| Every other `pageSize` schema (166+ parameters, including **all 12 of this CLI's target entities**) | No `default` and no `maximum` keyword at all | — |
| ServiceTitan's Partner-API portal "Timeouts and Pagination" page (a different API family) | Narrative only | "PageSize: ... between 1 and 5000... Defaults to 50" `[TIMEOUTS]` |

`[specs/tenant-accounting-v2.json, tenant-settings-v2.json, tenant-reporting-v2.json — re-verified via direct schema scan]`

**Do not assume 500, or 5000, is a safe ceiling for jobs/invoices/estimates/
customers/etc.** — only journal-entries documents 500 in its own text, and
only two Settings/Reporting endpoints enforce a maximum at the schema level
at all. The 1-5000 figure is a plausible general Developer Portal policy
statement, not a confirmed guarantee for any of the CLI's 12 target entities.
Default to 50-500 and always branch pagination continuation on the
response's own `hasMore`/`page`/`pageSize`, never on whether a requested size
was honored.

**Correction on `page` semantics:** the same Partner-API "Timeouts and Pagination" page
also describes `Page` as "*Number of resources to skip*. Defaults to 1 when
no value is provided" `[TIMEOUTS]` — internally inconsistent wording (a
skip-count conventionally defaults to 0) that conflicts with every
per-endpoint OpenAPI parameter description ("the logical number of page to
return, starting from 1"). Real example responses from both sources show
`"page": 1` on the very first call, consistent with a 1-based page **number**,
not a literal skip-count. **Trust the per-endpoint OpenAPI wording**: build
pagination as a page number incremented by 1, never as `page * pageSize`
offset math.

Two Fleet Pro endpoints (Events, GPS — unrelated to the 12 target entities)
independently confirm `totalCount` can be silently capped well below the real
row count ("the reported total item count is capped at 10,000 records")
`[specs/tenant-fleet-pro-v2.json]`. **Never use `totalCount` to decide "is
there more data" — only `hasMore` is authoritative.**

### 5.2 Sorting

`?sort=+FieldName` (ascending) / `?sort=-FieldName` (descending) — **a single
field only**; no comma-separated multi-field sort syntax appears anywhere
across the 330 GET operations `[CAT]`. The sortable-field whitelist is small
and endpoint-specific — most commonly just `Id, ModifiedOn, CreatedOn` (33
endpoints share this exact phrasing); Jobs additionally allows `Priority`;
22 endpoints only say the generic, unenumerated "Applies sorting by specified
fields" with no field list at all `[CAT; specs/tenant-jpm-v2.json]`. Validate/
whitelist sort field names per entity rather than accepting arbitrary names,
and never attempt a multi-key sort expression.

**`sort` is structurally absent** (not merely undocumented) from
`Technicians_GetList` and `Employees_GetList` — two of the CLI's 12 target
entities cannot be server-side sorted via the transactional list endpoint at
all; sort client-side after fetching all pages `[specs/tenant-settings-v2.json — full parameter list for both paths has no "sort" key]`.

Legacy `orderBy`/`orderByDirection` coexist with `sort` on some endpoints,
with **inconsistent deprecation labeling**: Estimates explicitly marks them
"Deprecated: Use Sort parameter instead" (fields `Id, CreatedOn, ModifiedOn, SoldOn`),
but Invoices exposes **both** `orderBy`/`orderByDirection` *and* `sort`
side-by-side, undeprecated `[specs/tenant-salestech-v2.json; specs/tenant-accounting-v2.json]`.
Standardize on `sort=+/-Field` wherever it exists; fall back to
`orderBy`/`orderByDirection` only on endpoints that lack `sort` entirely
(e.g. Telecom v2 Calls, Section 5.4).

### 5.3 Filtering conventions

**Date filters.** `createdOnOrAfter` / `createdBefore` / `modifiedOnOrAfter` /
`modifiedBefore` are typed `{type: string, format: date-time}` on **100%** of
their 133/126 occurrences, and almost every description explicitly says
"(in UTC)" `[specs/*.json — direct schema scan]`. Always send RFC3339/ISO-8601
UTC timestamps (`Z` suffix) for these; convert local-time user input to UTC
first. **Actual response timestamps** use .NET round-trip formatting with a
**variable** number of fractional-second digits (0 to 7) — the portal's own
worked example shows both `"invoiceDate": "2021-07-19T00:00:00Z"` and
`"modifiedOn": "2021-07-19T20:35:38.2876004Z"` in the same response `[PD]`.
Use a tolerant ISO-8601/RFC3339 parser (e.g. `dateutil.parser.isoparse`), not
a fixed-width format string.

**`active` filter.** A shared enum (`ActiveRequestArg`) whose only legal
values are the **capitalized strings** `"True"`, `"Any"`, `"False"` — **not
JSON booleans** — defaulting to active-only, present on 79/330 GET ops
`[specs/tenant-jpm-v2.json — components.schemas.ActiveRequestArg, re-verified: `{"enum": ["True","Any","False"]}`]`.
Sending JSON `true`/`false` is a type mismatch on this parameter.

Two documented exceptions to the `active` convention:

- **Jobs_GetList has no `active` parameter at all** — Job has no `active`
  field (Section 6.1); use `jobStatus` instead `[CAT]`.
- **Invoices_GetList uses a differently-named, differently-valued parameter**:
  `activeState` = `ActiveOnly` / `InactiveOnly` / `All`, not `active` =
  `True`/`Any`/`False` `[specs/tenant-accounting-v2.json]`. A generic
  "exclude inactive records" helper needs a per-entity mapping table, not one
  hardcoded parameter name/value pair.

**`ids` filter.** Comma-separated string on nearly every endpoint that has
one. A **maximum of 50** is stated explicitly in the description on 56/73
occurrences; 17 more (including `ap-bills`, `credit-memos`,
`expense-transactions`, `inventory-bills`, `invoices`, and CRM's `customers`)
expose `ids` with **no** stated numeric limit at all, but 50 is the only
documented cap found anywhere `[specs/*.json — regex scan of every `ids` description for "maximum \d+"]`.
**Chunk any fetch-by-ids call into batches of 50 for every entity**, even
where the docs don't restate the limit. A small number of endpoints —
Telecom v2 Calls, plus Remittance Vendors, Vendors, and Settings'
`business-units/intacct` — declare `ids` as a **JSON array** instead of a
comma-separated string `[specs/tenant-telecom.json + others]`; a generic
query serializer must special-case array-style `ids` wherever the schema says
`type: array`, rather than assuming comma-joining works everywhere.

### 5.4 Calls: a worked example of an inconsistent module (v2 vs v3, export)

Telecom's Calls resource is the clearest illustration of why "one convention
fits all modules" fails:

| Path | Tag | Notes |
|---|---|---|
| `GET /v2/tenant/{tenant}/calls` | **"Calls (Deprecated)"** | Mixes old and new filters (both `createdAfter` *and* `createdOnOrAfter`; both `modifiedAfter` *and* `modifiedOnOrAfter`), `orderBy`/`orderByDirection` instead of `sort`, boolean `activeOnly` instead of the `active` enum, **no** `includeTotal` `[CAT]` |
| `GET /v2/tenant/{tenant}/calls/{id}` | "Calls" (**not** deprecated) | Single-call lookup by ID; safe to keep using |
| `GET /v3/tenant/{tenant}/calls` | "Calls" (**not** deprecated) | Standardized `ids`/`active`(tri-state)/`includeTotal`/`sort`/`createdOnOrAfter`-`Before`/`modifiedOnOrAfter`-`Before`, plus `callerPhoneNumber`, `sid`, `tag` — none of which exist on the deprecated v2 list `[CAT]` |
| `GET /v2/tenant/{tenant}/export/calls` | (export) | The **only** calls export path — **no v3 export exists** `[specs/tenant-telecom.json — full path list]` |

**Recommendation:** use **v3** for transactional/filtered listing, **v2
export** for bulk/incremental sync, and **v2 `GetDetails`** for single-call-by-ID
lookups (not deprecated) — the transactional and export code paths cannot
share one "calls" version.

v3 also trades away detail for standardization: `leadCall.customer` and
`leadCall.campaign` on v3 are reduced to bare `{id, name}` references, while
the deprecated v2 endpoints' equivalents are a **full** CRM `CustomerModel`
(address, email, balance, memberships, contacts...) and a **full** Marketing
`CampaignModel` (category, source, medium, businessUnit, dnis...)
`[specs/tenant-telecom.json]`. If inline campaign category/source/medium is
needed per call, either use the deprecated v2 endpoint or make a second call
to the Marketing API by campaign ID. Field details for Calls are in Section
6.8.

### 5.5 Export ("Data Export") APIs

ServiceTitan frames two families of endpoint by use case `[PD]`:

| | Transactional APIs | Data Export APIs |
|---|---|---|
| HTTP methods | GET, PUT, POST, DELETE, PATCH | **GET only** |
| URL shape | Ordinary resource paths | Path contains `/export/` |
| Intended use | "Small amounts of data... for a limited time interval or when you want to apply filters" | "A full snapshot of the data... refresh it as frequently as you want" |
| Coverage | Active records only (per each list's own `active` filter) | **Active, inactive, AND deleted** records, no filters |
| Best for | Targeted/filtered/ad-hoc queries, writes | Data warehousing / bulk sync |

Export endpoints use a **structurally different response envelope** — no
`page`/`pageSize`/`totalCount` at all:

```json
{"hasMore": true, "continueFrom": "opaque-token-string", "data": [ ... ]}
```

`hasMore`, `continueFrom`, `data` are all **required** (never optional),
identical across all 53 checked `ExportResponse_Of_*` schemas found across
every module `[specs/tenant-jpm-v2.json — ExportResponse_Of_Jpm.V2.ExportJobsResponse, and 52 more]`.
The CLI needs **two distinct response models** — transactional
(page/pageSize/hasMore/totalCount/data) and export (hasMore/continueFrom/data)
— they are not interchangeable.

**Export parameter set is minimal and fixed.** Across every GET operation
whose path contains `/export/` in all 25 specs, the full union of parameter
names is exactly `{tenant, from, includeRecentChanges, active}` — and `active`
appears on only 2 of the 53 export ops (`Export_Appointments`,
`Export_AppointmentAssignments`) `[specs/*.json — full parameter-name union scan]`.
**There is no way to filter an export pull by customerId/status/businessUnitId/etc.**
— pull everything (or everything since `from`) and filter client-side.

| Parameter | Meaning |
|---|---|
| `from` (optional, plain **untyped string** — no `format` constraint) | Omitted = full history from the beginning. A date string (e.g. `"2020-01-01"`) = start from that point. The opaque `continueFrom` token from a prior response = resume. **There is no `to`/`until`** — export is always open-ended from a start point to "now." Treat this value as an opaque blob per (tenant, entity); never parse, reformat, or share it across entities/tenants `[specs/tenant-jpm-v2.json; PD]`. |
| `includeRecentChanges` (optional boolean) | See freshness note below. |

**Batch size is neither configurable nor documented anywhere as any fixed
number** — no size/limit parameter exists on any export endpoint, and a
regex scan for a number near "records/items/rows/batch" across every export
operation in all 25 specs returns zero hits `[specs/*.json]`. Loop
"call → append `data` → if `hasMore`, set `from = continueFrom` and call
again" until `hasMore` is false, without assuming a fixed page size for
progress estimation.

**Ordering is a soft guarantee.** The `data` field's description on 51 of 53
checked export schemas reads verbatim: *"The collection of exported items,
usually sorted chronologically based on modification date"* — note "usually"
(the 2 Equipment Systems export endpoints carry no description text on
`data` at all) `[specs/*.json]`. Do not depend on exact ordering for
correctness.

**Polling cadence is baked into `hasMore`'s own schema description**, on 51
of 53 checked schemas: *"True means the consumer can invoke another request
right away using new continuation token value. False means there is now new
data for the export, and the consumer should wait for some time (5-10
minutes) before making another request"* `[specs/tenant-jpm-v2.json and 15+ more]`.
Once a loop reaches `hasMore=false`, sleep **at least 5-10 minutes** before
re-polling the same (tenant, entity) with the saved `continueFrom`.

**Freshness lag.** New/updated records normally surface in export feeds
**~15 minutes** after the change, unless `includeRecentChanges=true` is
passed — which makes them appear instantly but "may cause the same results
to appear multiple times on consecutive requests" `[PD]`. **Default:** leave
`includeRecentChanges` off for scheduled syncs (accept ~15 min staleness, no
dedup needed); only turn it on, with client-side de-duplication by record ID,
when a caller explicitly needs lower latency.

**Export coverage is partial by design** — "Only a few highly adopted APIs
have these data export endpoints, not all" `[PD]`. Of this CLI's 12 target
entities:

| Entity | Export endpoint? |
|---|---|
| Jobs, Appointments, Estimates, Invoices, Purchase Orders, Calls (v2 only), Customer Memberships, Technicians, Employees, Business Units, Customers | **Yes** — `/export/<entity>` |
| **Campaigns** | **No** — `tenant-marketing-v2.json` defines only `Campaigns_GetList` (plain paginated list); no `/export/campaigns` path exists anywhere `[specs/tenant-marketing-v2.json — full path listing re-verified]` |

Campaigns must be fully synced — initial *and* incremental — via the
standard `page`/`pageSize` + `modifiedOnOrAfter` transactional loop; there is
no export shortcut for this one entity.

**Estimates has two export paths** — use the modern one:

| Path | operationId | Note |
|---|---|---|
| `/tenant/{tenant}/estimates/export` | `EstimatesExport_EstimatesAsyncLegacy` | Summary literally says "**(legacy endpoint)**" `[specs/tenant-salestech-v2.json]` |
| `/tenant/{tenant}/export/estimates` | `EstimatesExport_Estimates` | **Use this one.** |

### 5.6 Recommended initial-sync / incremental strategy

1. **Initial full sync**, for each of the 11 exportable entities: call
   `GET .../export/<entity>` with no `from` (or a very old date); loop,
   appending `data` and resending `continueFrom`, until `hasMore=false`;
   persist the final `continueFrom` per **(tenant, entity)** as an opaque
   string.
2. **Campaigns** (no export endpoint): initial sync = plain paginated
   `GET /campaigns`, `page=1..`, `pageSize<=500`, `active=Any`, `sort=+Id`
   (or `ModifiedOn`), looping on `hasMore`.
3. **Incremental refresh (11 exportable entities):** resend the saved
   `continueFrom`. When `hasMore` flips to `false`, stop and sleep at least
   5-10 minutes before polling that (tenant, entity) pair again. Leave
   `includeRecentChanges` off by default (~15 min staleness, no dedup cost);
   opt in with ID-based de-duplication only when lower latency is explicitly
   needed.
4. **Incremental refresh (Campaigns):** re-page with
   `modifiedOnOrAfter=<last sync time, UTC>`, `sort=+ModifiedOn`.
5. **Throttle** every tenant's aggregate call rate to comfortably under
   600 calls/10s (this repo's client already targets exactly that, Section
   4.1), and apply capped exponential backoff on 429/5xx since no documented
   `Retry-After` guarantee exists (Section 4.2, 4.5).
6. **Never use `totalCount`** to decide a sync is complete — only `hasMore`
   is authoritative (Section 5.1).
7. For entities without a server-side sort that matters (Technicians,
   Employees, Section 5.2), sort client-side after the full pull.

This is a direct extension of what this repo's `fetch.py`/`api.py` already
implement for the transactional path (`FETCH_ENDPOINTS`, `pages()` helper,
per-tenant token cache and throttle) — none of the 12 target entities are
currently pulled via `/export/...`; the export path is a documented
opportunity for a lower-call-count, more-complete (inactive/deleted-inclusive)
snapshot strategy, not something already wired in.

---

## 6. Data model notes per area

### 6.1 Jobs (`jpm-v2`)

| Field | Type | Notes |
|---|---|---|
| `jobStatus` | plain string enum | `Scheduled, Dispatched, InProgress, Hold, Completed, Canceled` `[specs/tenant-jpm-v2.json — Jpm.V2.JobStatus, re-verified]` |
| `total` | nullable decimal | "Total amount of the job." Can be null even on a booked job. |
| `invoiceId` | **required, non-null** integer | Every job carries an invoice ID from booking, even when `total` is still null — "A job invoice is generated the moment a job is booked" `[PD]`. Do not use `total == null` to infer "no invoice exists"; resolve `invoiceId` separately. |
| `noCharge` | boolean | "Whether the job is a no-charge job." Exclude/bucket separately for revenue/avg-ticket metrics — by design, not a data gap. |
| `completedOn` | nullable UTC date-time | Use this (not `createdOn`/`modifiedOn`) for "completed in period" metrics. |
| `estimateIds` | array of int64 | *All* estimates ever attached to this job — a different question than `createdFromEstimateId`. |
| `createdFromEstimateId` | nullable int64 | The *one* sold estimate this job was created from. |
| `soldById` | nullable int64 | References a **Technician**, not an Employee — resolve against `/settings/v2/.../technicians`. |
| `campaignId`, `businessUnitId` | **required, non-nullable** int64 | The only two FK fields on Job that are never null — contrast `projectId`, `recallForId`, `warrantyId`, `membershipId`, `bookingId`, `leadCallId`, `soldById`, `createdFromEstimateId`, which are all nullable. |
| `firstAppointmentId`, `lastAppointmentId`, `appointmentCount` | **required, non-null** | "A job always has at least one appointment, which is automatically created when the job is booked" `[PD]` — safe to dereference without a null check. |
| `membershipId` | nullable int64 | "ID of the Membership associated with this job" — a direct Job→Membership link (semantics not otherwise documented; see 6.11). |
| `priority` | **unconstrained string, no enum schema** | Valid values (`Low, Normal, High, Urgent`) appear only in a filter parameter's free-text description, not a machine-checkable enum `[specs/tenant-jpm-v2.json]` — hardcode from docs text. |

`[specs/tenant-jpm-v2.json — Jpm.V2.JobResponse, re-verified field-by-field]`

**No `active`/soft-delete field on the transactional `JobResponse` at all** —
consistent with the narrative: "Jobs cannot be deleted, only canceled if no
longer necessary" `[PD]`. Use `jobStatus=Canceled`, not an `active` flag, to
exclude dead jobs. The **export** flavor (`Jpm.V2.ExportJobsResponse`, used
by `/export/jobs`) *does* add a required `active` boolean absent from the
transactional model — a snapshot built from `/export/jobs` must handle an
`active=false` state the regular `/jobs` endpoint never exposes; the two
response shapes are not identical despite sharing most field names
`[specs/tenant-jpm-v2.json]`.

**Jobs_GetList filters, worth knowing precisely:**

- `jobStatus` vs `appointmentStatus` are **two different enums** —
  `appointmentStatus` ("return a job if it has any appointment with the
  specified status") uses `Scheduled, Dispatched, Working, Hold, Done, Canceled`
  (note `Working`/`Done`, not `InProgress`/`Completed`) `[CAT]`. Don't
  cross-validate one against the other's value list.
- Four **independent** date-filter families: `created*` / `modified*` /
  `completedOnOrAfter`+`completedBefore` / `firstAppointmentStarts*` and the
  broader `appointmentStarts*` ("if *any* appointment starts...") `[CAT]`.
  Pick the filter matching "booked" vs "worked" vs "completed" semantics
  rather than approximating everything with `modifiedOnOrAfter`.
- **Correction (documentation bug, not a behavior claim):** `appointmentStartsBefore`'s
  own description text reads *"Return jobs if any appointment starts after
  date/time (in UTC)"* — byte-identical to its sibling
  `appointmentStartsOnOrAfter`'s text, i.e. the "Before" filter's own doc
  string says "starts after," not "starts before" `[specs/tenant-jpm-v2.json]`.
  Don't trust this description literally in UI/help text; the parameter name
  itself implies an upper bound, matching every other `*Before` parameter in
  the API — verify empirically.
- `technicianId` = "assigned to **any** appointment" (not "primary
  technician"); `tagTypeIds`/`equipmentIds` = **OR** semantics ("at least one
  of provided"), not AND `[CAT]`.
- `JobType.soldThreshold`: *"A job is considered as converted if subtotal
  meets or exceeds this threshold"* — a real, per-job-type dollar amount
  `[specs/tenant-jpm-v2.json — Jpm.V2.JobTypeResponse.soldThreshold]`. This is
  the mechanic behind ServiceTitan's own native Close/Conversion Rate
  formulas (Section 6.11).

### 6.2 Appointments (`jpm-v2`)

| Field | Type | Notes |
|---|---|---|
| `id`, `jobId`, `start`, `end`, `status` | required, non-null | Always safe to read. |
| `arrivalWindowStart`/`End` | nullable | Present only "if configured" — many tenants never set this up `[specs/tenant-jpm-v2.json]`. |
| `unused` | boolean | "Returns true if the appointment is unused" — an orphaned/extra appointment that was never scheduled/dispatched. **Separate concept from `active`** (soft-delete). |
| `active` | boolean | "Whether this appointment is active." |
| `status` | plain string enum | `Scheduled, Dispatched, Working, Hold, Done, Canceled` — note **`Done`, not `Completed`** `[specs/tenant-jpm-v2.json — Jpm.V2.Base.AppointmentStatus, identical as Dispatch.V2.AppointmentStatus]`. A generic status-mapping table cannot be shared between Job and Appointment — different vocabularies. |

Appointment status is **system-computed, never settable via API**: "You
cannot transition appointment statuses via API... Appointment statuses are
transitioned automatically when you dispatch or arrive technicians. They
also transition as technicians leave appointments, and when the job is
completed" `[PD]` — trust it as a real-time, non-user-editable state.

### 6.3 Appointment Assignments (`dispatch-v2`)

| Field | Notes |
|---|---|
| `id, technicianId, technicianName, assignedById, assignedOn, status, isPaused, jobId, appointmentId, createdOn, modifiedOn, active` | All **12 required/non-null**. `technicianName` is denormalized directly onto the assignment — a dispatch snapshot can display technician names with no second lookup `[specs/tenant-dispatch-v2.json]`. |
| `status` | Narrower plain-string enum than Appointment's: **`Scheduled, Dispatched, Working, Done` only** — no `Hold`, no `Canceled` `[specs/tenant-dispatch-v2.json — Dispatch.V2.JobAppointmentAssignmentStatus, re-verified]`. |
| `isPaused` | Boolean, distinct from both `status` and `active`. A paused/held assignment is represented via `isPaused=true`, not a `Hold` status value. |

**"Who is actively working right now" = `status=Working AND isPaused=false`**,
not `status` alone. A status-mapping table cannot be reused between
Appointment and AppointmentAssignment either — different value sets again.

### 6.4 Estimates (`salestech-v2`)

| Field | Type | Notes |
|---|---|---|
| `status` | **object** `{value: int32, name: string}` (`EstimateStatusModel`), nullable | `name` ∈ `Open, Sold, Dismissed` `[specs/tenant-salestech-v2.json — Estimates.Types.EstimateStatus, Estimates.V2.EstimateStatusModel, re-verified: `status` schema is `oneOf [EstimateStatusModel, null]`]`. **Not a bare string** like Job/Appointment status — generic JSON flattening must special-case `estimate.status.name`. |
| `reviewStatus` | **required, non-nullable** plain-string enum | `None, NeedsApproval, Approved, NotApproved` `[specs/tenant-salestech-v2.json — Estimates.Types.EstimateReviewStatus, re-verified]`. Two "status-like" concepts on one record, two different shapes. |
| `soldBy` | nullable int64, **undocumented in the spec itself** (no description text) | Understood by analogy to `Job.soldById` + narrative — "Sold By keeps track of which managed technician sold the estimate. Unmanaged technicians cannot sell estimates" `[PD]` — to reference a **Technician** ID. |
| *(no `total` field)* | — | `EstimateResponse` has **no top-level `total` at all** — only `subtotal`, `tax`, `vendorTaxCost` (all required, non-null decimals) `[specs/tenant-salestech-v2.json — required list re-verified: no "total" key]`. Compute "estimate value" client-side as `subtotal + tax`. |
| `isRecommended`, `isChangeOrder` | required, non-nullable booleans | Likely exclude `isChangeOrder=true` from a "new estimate" close-rate denominator (it's a change on an already-sold job, not a fresh chance to sell). |

The estimate **status-change history** endpoint
(`Estimates.V2.EstimateStatusChangesResponse`, from `EstimatesStatus_GetEstimateStatusChanges`)
represents `oldStatus`/`newStatus` as **plain nullable strings** — a *third*
shape for the same "status" concept within one module. Status representation
is endpoint-specific here, not resource-wide; always check per-endpoint, not
per-API `[specs/tenant-salestech-v2.json]`.

Estimates_GetList exposes **both** `soldById` and `soldByEmployeeId` as
separate filters, but `EstimateResponse` only ever returns one field,
`soldBy` — the two ID spaces (Employee vs. Technician) are not proven
interchangeable when joining results back `[specs/tenant-salestech-v2.json; CAT]`.
Most of Estimates_GetList's filters (`jobId`, `totalGreater`/`Less`,
`soldById`, `soldByEmployeeId`, `status`, `createdBefore`, `locationId`)
carry **no description text at all** in the spec — sharply unlike Jobs, where
nearly every filter has a full sentence `[specs/tenant-salestech-v2.json]`.
The `status` filter specifically is a bare, untyped string with no enum
`$ref` — valid values (`Open`/`Sold`/`Dismissed`) must be hardcoded from the
separate `EstimateStatus` enum, not schema-validated.

**Line items** (`EstimateItemResponse`) carry both revenue-side (`qty`,
`unitRate`, `total`, `salesTax`) and cost-side (`unitCost`, `totalCost`,
`vendorTaxCost`) numbers per item, a nested `sku` object
(`id, name, displayName, type, soldHours, generalLedgerAccountId/Name`), and
optional membership fields (`membershipTypeId`,
`membershipBillingFrequency` ∈ `OneTime, Monthly, EveryOtherMonth, Quarterly, BiAnnual, Annual`,
`membershipDuration` in months) `[specs/tenant-salestech-v2.json]`. Item-level
cost data supports margin (`total - totalCost`) without a pricebook lookup;
`membershipBillingFrequency` flags membership sale/renewal line items.

Estimates has two export paths (Section 5.5) — use `/tenant/{tenant}/export/estimates`,
not the legacy `/tenant/{tenant}/estimates/export`.

### 6.5 Business Units, Technicians, Employees (`settings-v2`)

| Entity | Field | Notes |
|---|---|---|
| Business Unit | `name` vs `officialName` | `name` = "The internal name of the business unit"; `officialName` (nullable) = "The official name which customers see" — use `officialName` for any customer-visible output, `name` for internal reporting `[specs/tenant-settings-v2.json]`. |
| Business Unit | `trade`, `division` | **{id, name}** reference objects (`Services.NamedModel`) — tenant-configurable master data, **not** a closed enum; don't validate against a fixed list. |
| Technician & Employee | `userId` | Present on **both**, described in parallel language ("ID of the user that belongs to the technician"/"...employee"). **Technician.id ≠ Employee.id for the same person** — reconcile via the shared `userId` (also exposed as a `userIds` filter on both list endpoints), **never** via `id` `[specs/tenant-settings-v2.json]`. |
| Technician | `businessUnitId` | Nullable, **single-valued** "home" BU only. |
| Employee | `businessUnitAccessIds` | Array, multi-BU access — "Returns null when the 'Business units filtering for employees' feature flag is disabled." An asymmetry: a technician has one home BU; an office employee can have several `[specs/tenant-settings-v2.json]`. |
| Technician | `status` | Real-time dispatch-state enum, only 4 values: **`Idle, Dispatched, Working, Meal`** `[specs/tenant-settings-v2.json — TenantSettings.V2.TechnicianStatus, re-verified]` — a **fourth**, independent status vocabulary (see the comparison table below). |
| Technician | `isManagedTech`, `licenseType` | `licenseType` ∈ `NonManagedTech, ManagedTech, ManagedInstaller`. "Unmanaged technicians cannot sell estimates" `[PD]` — `soldById`/`soldBy` values on Job/Estimate should, in practice, only ever reference `isManagedTech=true` technicians (a useful data-quality check). |

**Four independent "status" vocabularies exist in this data model — do not
share a mapping table between them:**

| Entity.field | Values |
|---|---|
| `Job.jobStatus` | Scheduled, Dispatched, InProgress, Hold, **Completed**, Canceled |
| `Appointment.status` | Scheduled, Dispatched, Working, Hold, **Done**, Canceled |
| `AppointmentAssignment.status` | Scheduled, Dispatched, Working, **Done** *(no Hold, no Canceled)* |
| `Technician.status` | **Idle**, Dispatched, Working, **Meal** *(no Scheduled/Hold/Done/Canceled)* |

Narrative distinction, confirming these are genuinely separate entities, not
a role flag on one table: "Employees are office personnel, such as CSR,
dispatchers, and bookkeepers... Technicians are field personnel who leverage
ServiceTitan... using either the web-based interface or, more commonly, the
ServiceTitan mobile app" `[PD]`.

The `active` filter (`True`/`Any`/`False`) exists on Technicians, Employees,
Business Units, Appointments, AppointmentAssignments and Estimates lists —
**but not on Jobs_GetList** (Section 5.3). `sort` is structurally **absent**
from Technicians_GetList and Employees_GetList (Section 5.2) — full parameter
list for both: `[tenant, ids, userIds, name, active, page, pageSize, includeTotal, createdBefore, createdOnOrAfter, modifiedBefore, modifiedOnOrAfter]` (Employees adds `email`) — no `sort` key `[specs/tenant-settings-v2.json]`.

Data-export endpoints exist for Jobs, Appointments, AppointmentAssignments,
Business Units, Employees, Technicians and Estimates (Section 5.5) — same
hasMore/continueFrom cursor and 15-minute freshness lag as everywhere else.

---

### 6.6 Invoices, Payments, Payment Terms, Credit Memos, Vendor Bills (`accounting-v2`)

#### 6.6.1 Invoices

There is **no `GET /invoices/{id}`** single-record endpoint — only
`GET /invoices` (list, supports an `ids` filter) and `PATCH /invoices/{id}`
(update, irrelevant to a GET-only tool). Full path re-verified: `/tenant/{tenant}/invoices/{id}`
supports only `["patch"]` `[specs/tenant-accounting-v2.json]`. To fetch one
invoice by ID, call the list endpoint with `ids=<id>`.

`InvoiceResponse` has **no `status` field at all.** The status-like concepts
are four separate fields:

| Field | Shape | Notes |
|---|---|---|
| `syncStatus` | plain string | Accounting-sync state (Pending/Posted/Exported-style, mirrors `TransactionStatus`). |
| `sentStatus` | enum | `NotSent, Sent, Opened` `[specs/tenant-accounting-v2.json — Accounting.V2.InvoiceSentStatus, re-verified]` |
| `reviewStatus` | **required, non-null** enum | `NeedsReview, OnHold, Reviewed, PendingApproval, Rejected, ReadyForBilling, Invoiced` `[specs/tenant-accounting-v2.json — Accounting.V2.InvoiceReviewStatus, re-verified]` |
| `active` | boolean | Soft-delete/void flag. |

"Paid" is **not** a status value anywhere — derive it from `balance == 0`
relative to `total`, not from any enum.

The **`statuses` list filter** is *not* an open/paid/void workflow status —
it filters on `Accounting.V2.TransactionStatus`, an accounting-sync status
with only **3 values: `Pending, Posted, Exported`** `[specs/tenant-accounting-v2.json — re-verified]`.
It must be **repeated as a separate query key per value**, not comma-joined:
"add a new `statuses` query parameter for each status you want to include.
For example: `&statuses=Pending&statuses=Posted`" `[specs/tenant-accounting-v2.json — parameter description]`.
Use `balanceFilter`/`dueDate*` filters for AR-style "open/paid" questions
instead (below).

| Filter | Notes |
|---|---|
| `invoicedOnOrAfter` / `invoicedOnBefore` | The invoice-date range filter. |
| `dueDateOnOrAfter` / `dueDateBefore` | Due-date range (note: no "OnOr" on the *Before* variant). |
| `balanceFilter.Balance` (decimal) + `balanceFilter.Comparer` | `Comparer` ∈ `Equals, NotEquals, Greater, Less` `[specs/tenant-accounting-v2.json — Accounting.V2.BalanceComparer, re-verified]`. Build "balance ≥/< X" AR queries with this pair. |
| `activeState` | `ActiveOnly, InactiveOnly, All` — **not** `active=True/Any/False` (Section 5.3). |
| `invoiceConfigurations` | See below — filter by invoice *type*, since not every row is ordinary sales AR. |

Money fields — **all nullable STRINGS** (formatted decimals), not JSON
numbers: `subTotal, salesTax, total, balance, discountTotal`
`[specs/tenant-accounting-v2.json — re-verified: `"total": {"type": ["string","null"]}` etc.]`.
`invoiceDate` and `dueDate` are nullable date-time **strings** (not money).
Cast money fields to Decimal/float explicitly; don't assume JSON numeric
types.

`invoice.job` is a **minimal reference** `{id, number, type}`
(`Accounting.V2.JobResponse`, `additionalProperties: false`) — not a full job
record. For business unit/status/location, join to the JPM Jobs API
separately `[specs/tenant-accounting-v2.json]`.

`invoiceType` (tenant-configured `{id, name}` lookup) and
`invoiceConfiguration` (fixed enum) are **two different fields**:

`InvoiceConfiguration` ∈ **`JobInvoice, ProjectInvoice, CounterSales, MembershipInvoice, InterestChargeInvoice, ARImportInvoice`**
`[specs/tenant-accounting-v2.json — re-verified]`. Group/filter on
`invoiceConfiguration`, not the free-form `invoiceType`, to distinguish job
vs. project vs. POS vs. membership invoices. Note `InterestChargeInvoice`
(late fee) and `ARImportInvoice` (imported/opening-balance record) — **not
every invoice row is ordinary sales AR.**

#### 6.6.2 Payments (customer payments)

Fields: `active, appliedTo[], authCode, batch, businessUnit, checkNumber,
createdBy, createdOn, customFields, customer, date, deposit,
generalLedgerAccount, id, memo, modifiedOn, referenceNumber,
refundedPaymentId, syncStatus, total, type, typeId, unappliedAmount`
`[specs/tenant-accounting-v2.json — Accounting.V2.DetailedPaymentResponse]`.

`appliedTo[]` (`PaymentAppliedResponse`: `appliedId, appliedTo` [invoice id],
`appliedAmount, appliedBy, appliedOn, appliedToReferenceNumber`) confirms
**one payment can apply to multiple invoices** — read the array, don't
assume a 1:1 payment-to-invoice relationship. `unappliedAmount` is the
portion of the payment not yet allocated to any invoice.

**Naming quirks vs. Invoices**, worth flagging so a generic filter-name
helper doesn't silently misfire:

- Date filter is `paidOnAfter`/`paidOnBefore` (**no "OnOr" prefix**) —
  different from the `*OnOrAfter` convention used on `createdOnOrAfter`/
  `modifiedOnOrAfter` on this *same* endpoint `[CAT]`.
- `statuses` here is an **undocumented plain string** (no description, no
  enum) — unlike Invoices' well-documented `TransactionStatus` array. Do not
  share one "statuses filter" helper between the two endpoints
  `[specs/tenant-accounting-v2.json]`.

#### 6.6.3 Payment Terms

A single shared lookup used by **both** customer invoices and vendor bills,
flagged with `isCustomerDefault`/`isVendorDefault` booleans
`[specs/tenant-accounting-v2.json — Accounting.V2.PaymentTermAPIModel]`. This
is also where `dueDate` actually comes from: `dueDayType` ∈
**`NumberOfDays, CertainDayOfCurrentMonth, CertainDayOfFollowingMonth`**
`[specs/tenant-accounting-v2.json — Accounting.V2.PaymentTermDueDayType, re-verified]`
plus a `dueDay` number — so `dueDate` diverges from `invoiceDate` for any
term other than due-on-receipt. Resolve `termName` on both invoices and
vendor bills against the one `GET /accounting/v2/tenant/{tenant}/payment-terms`
list.

#### 6.6.4 Credit Memos — a real entity the narrative docs never mention

Credit Memos are a fully-fledged Accounting resource per the module's own
one-line description ("AP bills, AP credits, AP payments, bank deposits,
**credit memos**, expense transactions...") `[specs/tenant-accounting-v2.json — info.description]`,
but the ~100KB narrative walkthrough covers only "Invoices," "Payments,"
"Payment Terms," "Bills" and "Tax Zones" as its Accounting sections — **zero**
mentions of "credit memo" anywhere in the narrative doc `[PD]`. Easy to miss
unless the spec/catalogue is cross-checked directly.

`GET /tenant/{tenant}/credit-memos` — parameters (re-verified) include
`active, ids, businessUnitIds, appliedToInvoiceIds, appliedToReferenceNumber,
dateBefore/OnOrAfter, appliedBefore/OnOrAfter, createdBefore/OnOrAfter,
modifiedBefore/OnOrAfter, batchId, batchNumber, statuses,
totalFilter.Amount/Comparer, balanceFilter.Amount/Comparer, sort, page,
pageSize, includeTotal` `[specs/tenant-accounting-v2.json]`. Sibling
endpoints: `/credit-memos/{id}/items` and `/credit-memos/credit-memo-items`.

- **`CreditMemoPublicResponse`** required fields: `id, date, subTotal,
  salesTax, total, balance, createdOn, modifiedOn, active`
  `[specs/tenant-accounting-v2.json — re-verified]`. **No `dueDate` field at
  all** — only a plain issuance `date`. If listing unapplied credit memos as
  their own aging rows, bucket by `date`, not a nonexistent due date.
- **`balance`** on a Credit Memo is the memo's own **remaining UNAPPLIED**
  amount still available for some future invoice — **not** "how much of this
  credit is currently offsetting a given invoice."
- Applying a credit memo to an invoice is `POST /credit-memos/{id}/splits`,
  body `{invoiceId, amount}`, producing a dated `appliedTo[]` entry
  (`appliedId, appliedTo` [invoice id], `appliedAmount, appliedOn, appliedBy,
  appliedToReferenceNumber`) — structurally identical to how a Payment is
  applied `[specs/tenant-accounting-v2.json — Accounting.V2.CreditMemos.CreditMemoAddSplitPublicRequest, CreditMemoAppliedToCodeResponse]`.
- Credit memo line items carry both `expensesGeneralLedgerAccount` and
  `revenueGeneralLedgerAccount` — direct schema-level evidence that credit
  memos are meant to net against **recognized revenue**, not just reduce a
  cash/AR balance.
- **Invoice itself carries no reverse reference to any credit memo applied
  against it.** Netting is only possible by pulling `/credit-memos`
  separately and joining client-side on `appliedTo.appliedTo == invoice.id`.

**No arithmetic or timing relationship between Invoice and Credit Memo is
stated anywhere** — a case-insensitive scan of the full accounting-v2 spec
text for "net of," "reduce," "applied against," "write off," "write-off," and
"offset" returns **zero** hits for every phrase; the Reporting API spec
contains zero occurrences of the word "credit" at all `[specs/tenant-accounting-v2.json; specs/tenant-reporting-v2.json]`.
**This netting logic must be designed by the integrator; ServiceTitan
supplies only the raw, joinable pieces.**

#### 6.6.5 Revenue: gross, not net of Credit Memos — a live gap in this CLI

**`Invoice.total` and `Job.total` are fixed, gross, point-in-time billed
amounts. There is no `netTotal` field anywhere** — a search for `netTotal`
across all 25 specs returns zero matches `[specs/*.json]`. A rollup that sums
`Invoice.total`/`Job.total` alone is reading gross billed value, permanently,
even after later credits are issued against it.

Two further mechanisms adjust an already-issued invoice's economic value
without mutating `Invoice.total`, and both should be considered in a full
netting design: (1) `POST /invoices` is documented "create adjustment
invoice," producing a **new** invoice row linked back via `adjustmentToId`;
(2) the product defines **Refund invoice** ("Write off revenue and return
the payment that was previously recorded by another invoice") and **Balance
write-off invoice** ("Clear outstanding balances on an existing invoice
where it's no longer expected for the customer to pay") types `[PD]`.

**This exact gap already exists in this repo.** `fetch.py` computes
`"revenue": num(j.get("total"))` directly off the Job resource
(`src/servicetitan_cli/fetch.py:194,217`), and `metrics.py`'s own
`completedRevenue` definition is "Sum of job.total over jobs completed in
the range" (`src/servicetitan_cli/metrics.py` — `DEFINITIONS["revenue"]`) —
neither nets Credit Memos. `fetch.py`'s own module docstring already flags
the adjacent AP-side gap explicitly: *"Vendor bills do exist in the API
(accounting ap-bills and inventory-bills); they are not part of the snapshot
yet"* (`src/servicetitan_cli/fetch.py:36-38`) — Credit Memos are an
analogous, currently-unaddressed gap on the revenue side. **Any tenant that
issues even one Credit Memo against a job counted in `completedRevenue` is
already overstating that KPI today**, with no caveat currently surfaced in
`metrics.py`'s own documented definitions. See Section 8 for the concrete
fix.

#### 6.6.6 Vendor Bills (AP): `ap-bills` and `inventory-bills`

Both exist, both GET-only-relevant, both live under `/accounting/v2`:

| | `ap-bills` | `inventory-bills` |
|---|---|---|
| Paths | `GET /ap-bills`, `GET /ap-bills/{billId}`, `POST /ap-bills/markasexported` | `GET /inventory-bills`, `GET /inventory-bills/paginated`, `GET /export/inventory-bills`, `POST /inventory-bills/markasexported` |
| Shared core fields | `billAmount, dueDate, vendor, purchaseOrderId, billDate, jobId, jobNumber, referenceNumber, termName, taxZone, businessUnit, items, syncStatus, batch, vendorNumber` | same |
| `status` | **Present** — reconciliation enum: `Unreconciled, Discrepancy, Reconciled, Canceled` `[specs/tenant-accounting-v2.json — Accounting.V2.ApBillStatus, re-verified]` | **Absent** — only `syncStatus` (Pending/Posted/Exported-style) exists |
| `billType` filter | `NotSet, Procurement, ApBill` (defaults to `Procurement`) | `NotSet, Procurement, ApBill, RecurringBill` — **one extra value** |
| Writes | Only `markasexported` (irrelevant to a GET-only tool) — "Bills cannot be updated through API. The corresponding purchase order can be updated, which will result in a new bill reflecting the changes" `[PD]` | same |

Prefer `ap-bills` when a bill's reconciliation `status` is needed; use
`inventory-bills` (with `billType=RecurringBill`) to include recurring
vendor bills, which `ap-bills` cannot return. "Bills are commonly tied
directly to a purchase order and are generated when items on the purchase
order are received" `[PD]` — join via `bill.purchaseOrderId`, and expect a
bill only after (partial or full) PO receipt.

**`RemittanceVendors`** (accounting-v2: `id, name, active, address,
contactInfo, isApproved, isVerified, customerId, createdOn, modifiedOn`) is
a **distinct** entity from Inventory's `Vendors` — it's the "pay-to" party
for a bill/payment (e.g. a factoring company), referenced from
`ApBillResponse.remittanceVendorInfo` and `ApPaymentResponse.remittanceVendor`
`[CAT]`. Don't conflate "vendor" (who sold the goods, `inventory-v2`) with
"remittance vendor" (who gets paid, `accounting-v2`).

**Vendor Payments** (`ApPaymentResponse`) carry **both** a workflow `status`
(`Unpaid, ReadyForApproval, Initiated, Paid, Cancelled, InTransit, Processing, Rejected`
— 8 values `[specs/tenant-accounting-v2.json — Accounting.V2.ApPaymentStatus, re-verified]`)
**and** a separate `syncStatus` (`Pending, Posted, Exported`) — unlike
customer Payments, which has only `syncStatus`. Key "AP paid vs. outstanding"
off `status` (workflow), not `syncStatus` (accounting-export state only).

#### 6.6.7 Computing AR aging: no as-of API, dueDate not invoiceDate, don't double-net credits

**There is no as-of / point-in-time parameter anywhere on this platform.** A
global search across all 25 specs for `asOf`/`as-of` returns zero matches; a
literal "as of" phrase returns exactly **one** hit total, in `tenant-jpm-v2.json`,
about a project's daily-log health status — unrelated to accounting
`[specs/*.json]`. `Invoice.balance`, `invoiceDate`, `dueDate` are independent,
flat, nullable fields on `InvoiceResponse` with no companion
"measured-as-of" timestamp and no historical/versioned variant
`[specs/tenant-accounting-v2.json]`. `balanceFilter` on the list endpoint
filters against whatever the balance is **at call time** — it takes no date
argument. Even the bulk export feed for invoices is a change-data-capture
feed keyed by `modifiedOn` (its `from` parameter "refer[s] to the
`modifiedOn` field"), returning each record's **current** field values as of
extraction time — not the values as they stood on the `from` date `[PD]`.
**Periodic snapshotting inside the CLI is the only path to a true historical
aging report** — persist at minimum `{invoiceId, balance, invoiceDate,
dueDate, adjustmentToId, invoiceConfiguration, snapshot_date}` on a recurring
cadence; "aging as of last month" can only be answered for dates the tool
actually captured going forward.

**This repo's own AR-aging metric already has this exact limitation, and
already documents it precisely:** `metrics.py`'s `totalAR` definition reads
*"...as of the snapshot's generatedAt: ServiceTitan only exposes the current
balance, so `--as-of` moves the aging clock but cannot rewind balances"*
(`src/servicetitan_cli/metrics.py` — `DEFINITIONS["accounting"]["totalAR"]`).
In other words, `--as-of` changes which **reference date** buckets are
computed against, but it is still bucketing **today's live balances** — it
is not (and, absent ServiceTitan exposing balance history, cannot be) a true
historical reconstruction. A best-effort, clearly-labeled approximation is
possible for periods the tool has already ingested payments/credit-memos
for: `balance(as of X) ≈ total − Σ(appliedAmount from Payments.appliedTo and
CreditMemo.appliedTo where appliedOn ≤ X)` — but this assumes
`total`/`subTotal`/`salesTax` never change retroactively (unverified) and
ignores adjustment/write-off invoices, so treat it as a fallback, not a
substitute for real snapshots.

**Bucket basis: `dueDate`, not `invoiceDate`.** ServiceTitan's own product
documentation of its native AR Transactions report is explicit and
unambiguous here **(secondary — help.servicetitan.com, product docs, not the
developer/API docs)**: "Payment Terms due dates are used for aging
calculations on customer statements and AR (summary) reports. The aging
calculation uses the due date field to bucket the invoices due into the
appropriate buckets," with bucket columns literally defined as "Balance on
the invoice past due by 1-30 days **as of the date of the report**" (and
similarly for 31-60/61-90/91-120/121+)
**(help.servicetitan.com/docs/use-view-edit-and-deactivate-payment-terms; help.servicetitan.com/how-to/ar-transactions)**.
This independently corroborates the point above — even ServiceTitan's *own*
canned report recomputes buckets at report-run time; it is not a stored
historical fact either.

`dueDate` is nullable, and ServiceTitan's own report **excludes** invoices
with no assigned payment term (hence no due date) from the aging-bucket
columns entirely, showing them instead in a separate "Net Amount" column
**(secondary — help.servicetitan.com/how-to/ar-transactions)**. Falling back
to `invoiceDate` for term-less invoices, as this repo's `arAging` metric
currently does ("Days past the due date (falling back to the invoice date),
bucketed 0-30/31-60/61-90/91-120/120+" — `src/servicetitan_cli/metrics.py`),
is a **deliberate, documented deviation** from ServiceTitan's own report
behavior, not a bug — worth keeping the code comment that says so, or adding
a distinct "no term / unbucketed" total to match ServiceTitan's convention
exactly.

**Do not separately subtract `CreditMemo.balance` when summing open
`Invoice.balance` for total AR.** Applying a credit memo to an invoice (the
`/credit-memos/{id}/splits` action) is exactly what lowers that invoice's
own `balance` field — so `Invoice.balance` **already** nets out any credit
that has actually been applied. Subtracting the *applied* portion again
would double-count; subtracting the *unapplied* portion (which is all
`CreditMemo.balance` represents, Section 6.6.4) would incorrectly reduce AR
by credit that was never netted against any specific invoice. **`Σ(open
Invoice.balance)` is already correct AR.** If a distinct "net AR exposure"
(open AR minus available-but-unapplied customer credit) metric is wanted,
compute it as its own clearly-labeled figure via
`GET /credit-memos?balanceFilter.Comparer=GreaterThan&balanceFilter.Amount=0`
— never folded into the primary bucket math. ServiceTitan's own AR
Transactions report reinforces this: it lists credit memos and
adjustment-linked invoices as their own separate signed rows ("CM: Invoice
with a negative balance, credit memo"; "for invoices with adjustment
invoices, the Net Amount may be different from the Balance")
**(secondary — help.servicetitan.com/how-to/ar-transactions)**, rather than
pre-merging them into one number.

Whether a canned "AR Aging"/"AR Transactions"-equivalent report is reachable
per-tenant via the Reporting API is genuinely undiscoverable from the static
spec (Section 7 explains why — the schema is fully generic, no report names
are hardcoded anywhere) and, in any case, is POST-only and therefore out of
reach for this CLI regardless.

---

### 6.7 Purchase Orders and Vendors (`inventory-v2`)

| Field | Notes |
|---|---|
| `status` | **Plain, un-enumerated `string`** in the schema itself — no `$ref` to any formal enum `[specs/tenant-inventory-v2.json — Inventory.V2.PurchaseOrderResponse.status has no enum schema]`. The 6 valid values exist only as free text in the *query-parameter* description: **`Pending, Sent, PartiallyReceived, Received, Exported, Canceled`** `[specs/tenant-inventory-v2.json — re-verified verbatim]`. The CLI cannot validate PO status against a formal enum from the spec; hardcode these 6 values and treat unrecognized strings defensively (future values may appear without a spec update). |
| Most fields | **Flat foreign-key IDs**, not nested `{id,name}` objects: `vendorId, businessUnitId, jobId, technicianId, projectId, inventoryLocationId, batchId, budgetCodeId, typeId` — unlike Invoice/Bill, which embed named objects `[specs/tenant-inventory-v2.json]`. To display a vendor or business-unit **name** on a PO, call `GET /vendors/{vendorId}` and the Settings business-units endpoint separately — the PO payload alone is insufficient. |
| `total`, `tax`, `shipping` | Typed as **JSON `number`** (decimal), not string — **inconsistent** with Invoice/Bill money fields (`subTotal`, `total`, `balance`, `billAmount`), which are nullable strings `[specs/tenant-inventory-v2.json — re-verified: `"total": {"type": "number", "format": "decimal"}`]`. Don't write one universal "parse money as string" helper across all snapshot models — type-check or cast defensively per entity. |

PO status cannot be set via a generic field update, but *can* be transitioned
to `Canceled` via a dedicated `PATCH /purchase-orders/{id}/cancellation`
action endpoint — refining the narrative's blanket claim that "a purchase
order status cannot be updated through API" `[PD; specs/tenant-inventory-v2.json]`.
Not relevant to a GET-only tool's own operation, but useful context if a
future version adds writes.

A **separate** PO *approval-request* workflow exists at
`GET /purchase-orders/requests` with its own 3-value status enum —
`PendingApproval, Approved, Rejected` (`Inventory.V2.PurchaseOrderRequestStatus`,
re-verified) — distinct from the PO fulfillment status above. Use this
endpoint/enum for a "POs awaiting spend-approval" metric, not the main PO
`status` field.

**Vendor** fields: `id, name, active, isTruckReplenishment,
isMobileCreationRestricted, memo, deliveryOption, defaultTaxRate,
contactInfo {firstName, lastName, phone, email, fax}, address {street, unit,
city, state, zip, country}, createdOn, modifiedOn, externalData[],
customFields[]` `[specs/tenant-inventory-v2.json — Inventory.V2.VendorResponse]`.
`deliveryOption` and `defaultTaxRate` are vendor-level defaults inherited by
new POs for that vendor.

### 6.8 Calls (`telecom`)

All three transactional call endpoints (v2 list, v2 by-id, v3 list) return
the **identical outer-wrapper shape**: `{id, jobNumber, projectId,
businessUnit, type, leadCall}` — the real call record is nested one level
down under a property literally named `leadCall`
`[specs/tenant-telecom.json — re-verified: all three endpoints' `fields` are exactly `["businessUnit","id","jobNumber","leadCall","projectId","type"]`]`.
Parsing code must read `call.leadCall.<field>` for almost everything
(direction, callType, duration, agent, customer, campaign, reason) — the
top-level object only carries `id`/`jobNumber`/`projectId`/`businessUnit`/
`type` (job type).

| `leadCall` field | Type | Notes |
|---|---|---|
| `callType` | enum `Abandoned, Unbooked, Excused, Booked, NotLead` `[specs/tenant-telecom.json — Telecom.V3.CallType/Telecom.CallType, re-verified]` | "If null, the call was not classified yet... outbound calls don't have classification and CallType is always null for outbound calls." Booking-rate metric = `Booked / (Booked+Abandoned+Unbooked+Excused)`, excluding `NotLead` and null (outbound/unclassified) from the denominator. |
| `direction` | enum `Inbound, Outbound` `[specs/tenant-telecom.json — Telecom.V3.CallDirection, re-verified: exactly 2 values]` | |
| `duration` | **ISO-8601 duration STRING** (schema `format: duration`, e.g. `"PT3M45S"`) | The `minDuration` **query filter**, by contrast, is documented as an **integer number of seconds** — a type/unit mismatch between filter input and response output for the same concept. Parse the ISO-8601 string for average-handle-time math; pass plain integer seconds to `minDuration`. |
| jobNumber only, **no `jobId`** | — | The transactional wrapper (v2 and v3 alike) exposes only a **string** `jobNumber` — no numeric job ID anywhere on `Telecom.V3.CallResponse` `[specs/tenant-telecom.json — re-verified]`. To join a call to a Job by numeric ID, either resolve `jobNumber` against the Jobs API (job numbers may not be globally unique across all account configurations) or use the export feed below, which does include a numeric job ID. |

`GET /v2/tenant/{tenant}/export/calls` returns a **completely different,
flat (non-`leadCall`-wrapped) shape**: `active, agent{id,name},
campaign{id,name}, createdBy{id,name}, createdOn, customer{id,name},
direction, duration, excuseMemo, from, id, job{id,number}, lead{id},
location{id,name}, modifiedOn, reason{id,name}, recordingUrl, status, tag,
to, type, voiceMailPath` `[CAT; specs/tenant-telecom.json — Telecom.V2.ExportCallResponse]`.
This is the **only** calls endpoint that gives a numeric `job.id` — use
export/calls whenever a call needs to be reliably joined to a Job by ID, or
`location` is needed directly on the call record. Its `type` field carries
the **same** 5-value classification enum as the transactional endpoints'
`callType` (just renamed); it **additionally** has its own `status` field
(`Ringing, Answered, Completed, Initiated` — telephony call-*progress* state,
`[specs/tenant-telecom.json — Telecom.V2.CallStatus, re-verified]`), which has
**no equivalent at all** in the v2/v3 transactional endpoints — don't confuse
export's `status` (call-progress) with the booking-classification concept
(`callType`/`type`). `excuseMemo` (the free-text note behind an Excused call)
likewise exists **only** on the export feed, not on the transactional
`leadCall` object.

**Call reasons** (referenced by `leadCall.reason.id`) resolve via a
completely separate module, **`jbce-v2`** ("Job Booking"), *not* telecom:
`GET /jbce/v2/tenant/{tenant}/call-reasons` → `{id, name, active, isLead}`
`[specs/tenant-jbce-v2.json — info.description "Call reasons"]`. To label
calls with human-readable reason names, or identify which reasons count as
lead-generating (`isLead`), fetch this lookup from a base URL most people
wouldn't guess exists for "call reasons."

See Section 5.4 for the v2/v3/export version matrix and why the export
endpoint has no v3 equivalent.

### 6.9 Campaigns (`marketing-v2`)

| Field | Notes |
|---|---|
| `source`, `medium` | **Free strings**, not closed enums (`otherSource`/`otherMedium` exist as "Other, please specify" fallbacks) — do not hardcode a fixed dropdown list; they are tenant-configurable `[specs/tenant-marketing-v2.json — Marketing.V2.CampaignResponse, re-verified: both `{"type": ["string","null"]}`]`. |
| `businessUnit` | **Plain string**, not a nested object. |
| `campaignPhoneNumbers` | Array of strings — **a campaign can track multiple phone numbers.** |
| `category.type` (`CampaignCategoryType`) | Narrow **2-value enum**: `Regular, GoogleHomeServices` `[specs/tenant-marketing-v2.json — re-verified]` — flags Google Local Services Ads integration specifically, **unrelated** to the free-text category *names* tenants create (don't treat this as a Digital/Referral/Print channel-grouping enum). |

Campaign **costs**: `GET /costs` returns one `dailyCost` per campaign per
(`year`, `month`) (`Marketing.V2.CampaignCostResponse`, required
`[id, year, month, dailyCost, campaignId]`); `GET /campaign-cost-summary?from&to`
(both required) returns a pre-aggregated rollup
`[{campaignId, campaignName, totalCost}]` instead of raw monthly records
`[specs/tenant-marketing-v2.json]`. For a marketing-ROI metric, use
`campaign-cost-summary` for a fast total; use `/costs` only if
month-by-month granularity is required. **No `/export/campaigns` path exists
anywhere** in `tenant-marketing-v2.json`'s 10 paths — confirmed by listing
every path in the file (`/categories`, `/categories/{id}`, `/costs`,
`/costs/{id}`, `/campaign-cost-summary`, `/campaigns`, `/campaigns/{id}`,
`/campaigns/{id}/costs`, `/email-channel-cost`, `/sms/optin/import`) — see
Section 5.5.

### 6.10 Customer Memberships (`memberships-v2`)

| Field | Notes |
|---|---|
| `status` | enum, **5 values**: `Active, Suspended, Expired, Canceled, Deleted` `[specs/tenant-memberships-v2.json — Memberships.V2.MembershipStatus, re-verified]` |
| `billingFrequency` (`MembershipRecurrenceType`) | enum, **6 values**: `OneTime, Monthly, EveryOtherMonth, Quarterly, BiAnnual, Annual` `[specs/tenant-memberships-v2.json — re-verified]` — `OneTime` = "upfront" in narrative language. |
| `activatedFromId` | nullable int64 — **"ID of the invoice item that is the sale task of this membership"** `[specs/tenant-memberships-v2.json — re-verified verbatim]`. **This is an invoice-line-item ID, not a Job ID** — a direct lookup against the Jobs API 404s. To find the job a membership was sold on, resolve the invoice item's parent invoice (and that invoice's job), not `activatedFromId` directly. |
| `soldById` | "ID of the user that was credited for the sale of this membership." |
| `customerId` | Flat integer — **not** a nested `{id,name}` object (unlike `Invoice.customer`). |
| `cancellationDate` | Nullable — "When customer membership **was or will be** cancelled." Can be a **future, already-scheduled** date on a membership whose `status` is still `Active`. Don't equate "`cancellationDate` is not null" with "currently cancelled" — check `status=Canceled` too. |
| `from`, `to` | Membership start/end dates. |

`MembershipLocationTarget` ∈ `AllLocations, SingleLocation`
`[specs/tenant-memberships-v2.json — re-verified]`, confirming the narrative:
"A customer membership has to be for one or all locations attached to a
customer" `[PD]`.

**MembershipTypes** list/get responses **omit** `durationBilling`,
`discounts`, `recurringServices`, and `tagTypeIds` (return null) **unless**
the caller explicitly passes `includeDurationBilling`/`includeDiscounts`/
`includeRecurringServices`/`includeTags=true` — the default call silently
returns null for these, which could be mistaken for "no discounts
configured" `[specs/tenant-memberships-v2.json]`.

Data-export endpoints exist for Customer Memberships and Membership Types
(same hasMore/continueFrom/15-min-lag pattern as Section 5.5).

---

### 6.11 How ServiceTitan's *own* native dashboard defines these metrics (secondary source)

**None of this subsection comes from the developer/API docs.** An exhaustive
grep of the entire Developer Portal snapshot (narrative doc, all 25 specs,
the endpoint catalogue, the module list) for "close rate," "average ticket,"
"AR aging," "call booking rate," or "membership attach" returns **nothing**
`[PD; specs/*.json; CAT; APIS]` — ServiceTitan's API documentation asserts no
formula for any of these. Everything below is from ServiceTitan's **product**
help center (`help.servicetitan.com`, describing the built-in ServiceTitan
dashboard) — always **secondary** relative to the developer docs used
everywhere else in this document, cited here because it explains fields this
API does expose and is useful context for anyone trying to match
ServiceTitan's own numbers.

| Native metric | Formula (help center) | API fields it depends on |
|---|---|---|
| **Close Rate** | `count(closed opportunities) / count(sales opportunities) * 100%`. *Opportunity* = a job that is not No-charge or Unconvertible. *Closed* = a completed job where the sold-estimate subtotal (sales jobs) or invoice subtotal (service/install jobs) **meets or exceeds** the job type's Sold Threshold. | `Job.noCharge`, `Job.recallForId`, `Job.warrantyId` (the narrower "opportunity for conversion" test is not-no-charge/not-recall/not-warranty), `JobType.soldThreshold` (real API field, Section 6.1), `Estimate.subtotal`/`Invoice.subTotal` — **all real, joinable fields**, even though the *formula itself* is nowhere in the API docs. |
| **Average Ticket** (3 native variants) | *Total Job Average* = avg revenue, all completed jobs incl. no-charge. *Opportunity Job Average* = avg revenue, opportunity jobs only — **"the ServiceTitan primary metric for average ticket"**, i.e. no-charge jobs excluded from the *primary* number. *Converted Job Average* = opportunity jobs that also crossed the sold threshold. | `Job.total`, `Job.noCharge`. |
| **Call Booking Rate** | `% of "call leads" booked into jobs`. A **call lead** = an inbound call lasting ≥60 seconds **or** marked `Is Lead`. | `leadCall.duration`, `leadCall.callType` (Section 6.8); the `isLead` concept is API-visible on the separate Call Reasons lookup (`jbce-v2`, Section 6.8). |
| **Membership Attach** | **Not defined anywhere** — not in the API docs, not in the product help center, not in any ServiceTitan source found. The closest defined concept, *"Memberships Converted,"* is narrower: completed jobs tagged Potential Member/Potential Membership Renewal (or an untagged completed job where a membership is sold/renewed) that resulted in a sale or renewal. | — |
| **AR Aging buckets** | Bucketed by `dueDate` (Section 6.6.7); no ServiceTitan-specific standard was found for bucket *width* (0-30/31-60/etc.) beyond the dueDate-based, report-run-time bucketing already covered there — bucket boundaries are a pure implementation decision anywhere in ServiceTitan's own materials. | `Invoice.dueDate`, `PaymentTerm.dueDayType`/`dueDay`. |

**(All rows: secondary — help.servicetitan.com/docs/whats-a-sold-threshold;
help.servicetitan.com/problem-solution/technicians-close-or-conversion-rate-does-not-look-correct;
help.servicetitan.com/how-to/modular-dashboard-overall-metrics;
help.servicetitan.com/docs/memberships-concepts)**

**This repo's `metrics.py` already makes its own explicit, documented choices
for all five metrics** (`DEFINITIONS` dict, `src/servicetitan_cli/metrics.py`),
generally *different from* — but consciously reasoned against — the native
formulas above (e.g. close rate here is cohort-based opportunity **count**,
grouped one-per-job to avoid Good/Better/Best inflation, rather than
ServiceTitan's sold-threshold-gated opportunity test; average ticket differs
by department; membership attach is an explicit, labeled approximation).
Section 8.4 tabulates what the CLI does today against the native formulas
above.

---

## 7. Reporting API, and why report data is architecturally out of reach for a GET-only tool

**Report data cannot be fetched with a GET request, full stop — not a design
choice, a fact about the API.** Both ways to run a report are `POST`-only and
require a JSON request body of parameters:

| Path | Method | operationId |
|---|---|---|
| `.../reports/{reportId}/data` | **POST** | `ReportCategoryReports_GetData` — "Get report data" |
| `.../reports/{reportId}/data/query` | **POST** | `ReportCategoryReports_StartGetData` — "Start a long-running report data query" |

`[specs/tenant-reporting-v2.json — re-verified: both operations are `post`, requestBody schema `Reporting.V2.ReportDataRequest`, required]`

The **only** `GET` anywhere on the data-fetch path is polling an async job's
result by token — `GET /tenant/{tenant}/data-queries/{token}` — and that
token only exists because a prior **POST** minted it (`202` response schema
`Reporting.V2.ReportDataPendingResponse`, required `[token, status]`)
`[specs/tenant-reporting-v2.json]`. There is no code path to report data that
starts with a GET; a POST is unavoidable somewhere in the sequence.

This repo's own generated endpoint catalogue independently confirms the gap
from the other direction: filtering `src/servicetitan_cli/data/endpoints.json`
for the Reporting module returns exactly **5** `GET` operations —
`ReportDataQueries_Get`, `DynamicValueSets_GetDynamicSet`,
`ReportCategories_GetCategories`, `ReportCategoryReports_GetReports`,
`ReportCategoryReports_Get` — and **zero** GET entries containing `.../data`
(re-verified directly against the file). The two POST operations
(`ReportCategoryReports_GetData`, `ReportCategoryReports_StartGetData`) and
the DELETE (`ReportDataQueries_Cancel`) are catalogued too, but per this
repo's own design, only GET operations are ever executed
(`README.md`: "the 270 write operations are listed so an agent can see what
exists, but never executed").

### 7.1 What a GET-only client CAN safely do: discovery

| Operation | GET path | Returns |
|---|---|---|
| List report categories | `GET /report-categories` | `{id: string, name: string}` — "ID of report catogory for safe use in report endpoints" `[specs/tenant-reporting-v2.json]` |
| List reports in a category | `GET /report-category/{report_category}/reports` | `{id: int64, name, description}`. **`id` is stable; `name` is a mutable display label** — "Once the report is created the ID cannot be changed" but "Do not use the name as the report identifier because it can be modified" `[specs/tenant-reporting-v2.json]`. Cache by `id`, refresh `name` periodically if cached at all. |
| Get one report's description | `GET /report-category/{report_category}/reports/{reportId}` | Full input-`parameters[]` and output-`fields[]` schema, plus `modifiedOn`. |
| Enumerate a dynamic filter's valid values | `GET /dynamic-value-sets/{dynamicSetId}` | `dynamicSetId` comes **only** from a report's own parameter description — there is no independent catalogue of value-set IDs; a generic "list dynamic value sets" command cannot exist independent of a specific report. |

**There is no flat "list all reports" endpoint.** Every report-level path
requires **both** `{report_category}` **and** `{reportId}` — the full path
list for `tenant-reporting-v2.json` is exactly 7 paths (re-verified), none a
category-less `/reports` route. Full enumeration of every report requires
walking every category first: `GET report-categories`, then `GET reports`
per category ID — an **N+1 call pattern**, not one call.

Report **descriptions are explicitly not fixed**: "Take a note that the
report description isn't fixed and may be changed by the report owner"
`[specs/tenant-reporting-v2.json — ReportCategoryReports_Get summary]`, and
the data POST's own summary repeats the warning: "Beware that report columns
may be changed. Result field names are listed alongside the data in the
response to validate that all the requested columns are there." **Unlike
transactional entities** (Invoice, Job, etc., whose field sets are fixed by a
versioned OpenAPI contract), a "report" is a user-authored object inside
ServiceTitan's Reporting UI that any report owner can edit at any time with
no API version bump. This is a strong, independent argument (beyond the pure
HTTP-verb problem) for computing operations metrics from the fixed
transactional/export entity endpoints in Sections 5-6 rather than from a
named report, even in a hypothetical tool that *could* POST.

### 7.2 Shape of report data, for completeness (unreachable by this CLI)

Report rows come back as **unlabeled arrays-of-arrays**, not objects with
named keys; a separate `fields[]` array (`{name, label}` — **no data type**)
must be zipped positionally against each row
`[specs/tenant-reporting-v2.json — Reporting.V2.ReportDataResponse, Reporting.V2.HeaderField]`.
To know whether a column is a Date/Number/Boolean/etc. (needed to format it),
a client must separately retain the earlier report-*description* call's
`ReportParameter`/field `dataType` (`String, Number, Boolean, Date, Time`) —
the live data response's own header doesn't repeat it.

Report parameters that only accept certain values declare `acceptValues`,
which is *either* a static inline list (`values`) *or* a reference to a
dynamic set (`dynamicSetId`) — a client must branch on which is populated
`[specs/tenant-reporting-v2.json — Reporting.V2.ParameterPossibleValues]`. The
request body's own `value` field is typed **`type: null`** in the OpenAPI
document itself — a spec bug, since the example request body shows
`{"name": "string", "value": {}}` and the field's own description says its
real type follows the parameter's declared `dataType`
`[specs/tenant-reporting-v2.json — Reporting.V2.ReportDataParameter]`. A
strictly spec-generated client would be unable to send a real value; a
hand-built client must ignore the literal schema type.

**A third, distinct pagination regime.** Report-data pagination uses
`page`/`pageSize` on the **POST call itself**, default **1000**, hard
`maximum` **25,000** `[specs/tenant-reporting-v2.json — re-verified: `pageSize: {"maximum": 25000, "minimum": 1}`]`
— far larger than the ~50-default/no-declared-max transactional convention
(Section 5.1) and structurally different from the export APIs'
`hasMore`/`continueFrom` cursor (Section 5.5): **there is no continuation
token** here — page 2 means re-POSTing with `page=2`, and for the async
variant, each page is its own independent job/token, not one token that
streams every page. `includeTotal` works the same way as everywhere else
(Section 5.1) — opt-in, or `totalCount` stays null.

**Async execution model:** `POST .../data/query` returns `200` inline if the
query finishes within roughly 10 seconds; otherwise `202` with a pollable
`token`. The async status enum has **only** `NotStarted`/`InProgress` — a
completed run is **never** reported as `202`; success is signaled purely by
the poll flipping to `200` with the payload (a gzip-compressed JSON blob,
transparently decompressed by most HTTP clients)
`[specs/tenant-reporting-v2.json — Reporting.V2.ReportDataAsyncStatus, re-verified: `{"enum": ["NotStarted","InProgress"]}`]`.
`DELETE /data-queries/{token}` cancels a running query and "frees the
per-tenant slot," always returning `204` whether or not the job had already
finished — the phrase "the per-tenant slot" (singular) implies a **cap on
concurrent long-running report queries per tenant**, separate from the
1-call/minute-same-report throttle (Section 4.1).

### 7.3 A permission subtlety worth stating explicitly in this project's docs

Fetching report data (both POST variants) needs only the **read** scope
`tn.rpr.reports:r`; only *cancelling* an async query (`DELETE`) needs the
**write** scope `tn.rpr.reports:w`
`[specs/tenant-reporting-v2.json — re-verified: `.../data` POST security is `[{"apiKeyHeader": [], "oauth": ["tn.rpr.reports:r"]}]`; `DELETE .../data-queries/{token}` security is `["tn.rpr.reports:w"]`]`.
**ServiceTitan's own OAuth-scope model treats running/reading a report as a
READ operation, even though the HTTP verb is POST.** A tenant granting this
CLI only `:r` scopes would still, from ServiceTitan's authorization
standpoint, permit report-data POSTs — the GET-only restriction in this CLI
is a **strictly self-imposed client-side rule about HTTP verbs**, not
something ServiceTitan's scope model forces. Keep "read-only" (OAuth scopes)
and "GET-only" (HTTP verb, this CLI's own design rule) distinct in any
documentation that talks about this tool's safety guarantees.

*Project note.* The CLI now runs non-GET operations behind explicit gates
(`--write` on the call, a per-profile allowlist, the token's granted scope)
instead of refusing them outright. The scope gate checks the operation's own
scope, which for report data is `tn.rpr.reports:r`, so report-data POSTs are
reachable through `servicetitan api reporting ... --write` once a profile
allows them.

### 7.4 Everything else about this module matches the rest of the platform

Same environment-specific hosts (`api.servicetitan.io/reporting/v2` /
`api-integration.servicetitan.io/reporting/v2`), same two required headers,
same `ApiErrorResponse` shape, and no `429` documented on any reporting
operation either (bare HTTP 429, detected by status code alone, same as
everywhere else) `[specs/tenant-reporting-v2.json]`. Two path oddities worth
hardcoding rather than deriving generically: (1) the `report_category` path
segment is **snake_case**, unlike every other camelCase parameter in the same
spec (`dynamicSetId`, `reportId`, `page`, `pageSize`, `includeTotal`,
`notifyWhenReady`) — re-verified — so a generic camelCase URL templater will
break on this one segment; (2) per the module's own one-line description,
"Dynamic value sets, reports, report categories" `[APIS]`, there is **no**
report-authoring/editing endpoint at all — this API only *runs* reports
already built in ServiceTitan's Reporting UI, and a `tn.rpr.report-data-ready`
webhook event referenced in one query-parameter's description
(`notifyWhenReady`) is not corroborated anywhere else — the module is flagged
`hasWebhooks: false` in `apis.json` and the event is never named in the
narrative Webhooks FAQ, an open inconsistency, not a confirmed subscribable
event.

**Conclusion.** With writes disabled (the default) the CLI cannot fetch
ServiceTitan report *output* at all — not "chooses not to," but architecturally
cannot, since both data-fetch operations are POST and even the polling GET
requires a POST-minted token; allowing `reporting` writes on a profile opens
that path. It
*can* safely offer read-only report **discovery** (categories, reports, one
report's parameter/field description) since those 5 operations are true
GETs. Because report definitions are also mutable by any report owner at any
time with no version bump, computing operations metrics (revenue, close
rate, AR aging, etc.) from the fixed, versioned transactional/export
endpoints in Sections 5-6 is both the only GET-only-compatible option **and**
the more stable one, independent of the HTTP-verb restriction.

---

## 8. Corrections to assumptions in the original dashboard scripts

The dashboard-template code this CLI was built from (and this CLI's own
early code) carried a handful of assumptions about the API that this
research explicitly checked against the OpenAPI specs. Three were flagged for
verification; two were wrong.

### 8.1 Corrections (assumption was wrong)

| # | Assumption | Verdict | Correct fact | Status in this repo |
|---|---|---|---|---|
| 1 | The invoice date filter is named `invoiceDateOnOrAfter` | **Wrong** — no such parameter exists | Correct name is **`invoicedOnOrAfter`** (paired with `invoicedOnBefore`) `[specs/tenant-accounting-v2.json — full parameter list re-verified, contains no substring "invoiceDate"]` | **Already fixed.** `fetch.py` uses `invoicedOnOrAfter` with an inline comment: *"`invoicedOnOrAfter` is the documented date filter (the template used an undocumented name and pulled everything)"* (`src/servicetitan_cli/fetch.py:277-279`). |
| 2 | No vendor-bill (accounts-payable) endpoint exists in the API | **Wrong** | **Two** exist: `GET /accounting/v2/tenant/{tenant}/ap-bills` (+ `/{billId}`) and `GET /accounting/v2/tenant/{tenant}/inventory-bills` (+ `/paginated`, `/export/inventory-bills`) `[specs/tenant-accounting-v2.json — both paths re-verified present]`. Field-name assumptions the original code *did* make for "InventoryBills" (`billAmount`, `dueDate`, `vendor`, `purchaseOrderId`) are accurate. | **Known, not yet pulled.** `fetch.py`'s own docstring already says so: *"COMMITTED PO SPEND is not accounts payable; it is unreceived purchase-order value... Vendor bills do exist in the API (accounting ap-bills and inventory-bills); they are not part of the snapshot yet"* (`src/servicetitan_cli/fetch.py:36-38`); `metrics.py`'s `committedOnOpenPOs` definition repeats this ("vendor bills are available live via `servicetitan api accounting ap-bills list` and are not in the snapshot"). See 8.3. |

### 8.2 Verified as already correct (no change needed)

| Assumption | Verdict | Source |
|---|---|---|
| `Membership.activatedFromId` is **not** a Job ID (it's something else) | **Confirmed correct** — it is "ID of the invoice item that is the sale task of this membership," an invoice-line-item ID; a direct Jobs-API lookup 404s | `specs/tenant-memberships-v2.json`; matches `fetch.py`'s own comment and `metrics.py`'s `membershipAttachPct` definition, which already treats this as approximate for exactly this reason |
| `dueDateOnOrAfter` (paired with `dueDateBefore`, no "OnOr") is a valid Invoices filter | Confirmed | `specs/tenant-accounting-v2.json` |
| `balanceFilter.Balance` + `balanceFilter.Comparer` (`Equals/NotEquals/Greater/Less`) is a valid Invoices filter | Confirmed | `specs/tenant-accounting-v2.json — Accounting.V2.BalanceComparer` |
| Purchase-order status includes `Pending, Sent, PartiallyReceived, Received, Canceled` | Confirmed, **plus one more value found**: `Exported` (6 total) — a PO pushed to accounting is no longer open commitment | `specs/tenant-inventory-v2.json`; `fetch.py`'s `PO_CLOSED_STATUSES = {"Received", "Exported", "Canceled", "Cancelled"}` already includes it |

### 8.3 Known gaps: confirmed to exist in the API, not yet part of the snapshot

| Gap | What exists | Where it would plug in |
|---|---|---|
| **Vendor bills / true accounts payable** | `ap-bills`, `inventory-bills` (Section 6.6.6) | `FETCH_ENDPOINTS` in `fetch.py` currently pulls Purchase Orders and Vendors, but no bill endpoint — `committedOnOpenPOs` is unreceived PO commitment, not AP. |
| **Credit Memos (revenue netting)** | `GET /accounting/v2/tenant/{tenant}/credit-memos` (+ `/items`) (Section 6.6.4-6.6.5) | `completedRevenue`/`Job.total`-based revenue is **gross**, with no netting anywhere in `fetch.py`/`metrics.py` today. Fix: pull `credit-memos` filtered to the same date range, join on `appliedTo.appliedTo == invoice.id` (via the invoice's `job` back-reference), and report a netted figure alongside — or at minimum, surface total credit-memo value issued in the period as a caveat next to `completedRevenue`. |
| **AR aging is not a true historical reconstruction** | No as-of/point-in-time balance API exists anywhere on the platform (Section 6.6.7) | `metrics.py`'s own `totalAR` definition already documents this precisely ("`--as-of` moves the aging clock but cannot rewind balances"); a true historical AR series requires periodic snapshotting (of `{invoiceId, balance, invoiceDate, dueDate}`) going forward — not retrofittable for past dates. |
| **`invoiceConfigurations` filter unused** | 6-value enum (`JobInvoice, ProjectInvoice, CounterSales, MembershipInvoice, InterestChargeInvoice, ARImportInvoice`) exists on Invoices_GetList (Section 6.6.1) | Current AR pull doesn't exclude `InterestChargeInvoice` (late fees) or `ARImportInvoice` (opening-balance imports) from ordinary sales AR — worth a filter or at least a labeled sub-total. |

### 8.4 Metric-formula design notes: this CLI's choices vs. ServiceTitan's native formulas

This repo's `metrics.py` already makes explicit, written-down, non-arbitrary
choices for the five metrics most likely to be second-guessed (its
`DEFINITIONS` dict, `src/servicetitan_cli/metrics.py`). None of these is a
"correction" — ServiceTitan's API documentation asserts no formula at all
(Section 6.11), so there is nothing to be wrong *relative to* — but engineers
extending this CLI should know where it deliberately diverges from
ServiceTitan's own **product** dashboard (secondary source, Section 6.11) and
why:

| Metric | This CLI (today) | Diverges from native formula by |
|---|---|---|
| Close rate | Cohort logic: share of opportunities *first presented* in the range that have *since* been won; opportunities grouped **one per job** (a Good/Better/Best set counts once), value counted only for the sold option(s) | Uses opportunity **count**, not ServiceTitan's sold-threshold dollar gate (`JobType.soldThreshold`); does not require crossing a per-job-type dollar amount to count as "won." Could be extended to also report a threshold-gated variant using the real `soldThreshold` field. |
| Average ticket | 5 different area-specific formulas (`avgBilledJob`, `avgAllJobs`, Service `avgTicket` = billed-only, Install `avgInstallTicket` = **not** billed-only by design, Sales `avgSoldTicket`) | ServiceTitan's own "primary" metric (Opportunity Job Average) also excludes no-charge jobs, matching this CLI's Service `avgTicket`; Install's intentional inclusion of $0 jobs is a documented, deliberate CLI choice, not an oversight. |
| AR aging | Buckets by due date, falling back to invoice date for term-less invoices; excludes undated (unbilled) invoices entirely | ServiceTitan's own report also buckets by due date, but **excludes** (rather than falls back for) term-less invoices into a separate "Net Amount" column — a documented, intentional deviation (Section 6.6.7). |
| Call booking rate | Three denominators at three granularities: company-wide (booked/all inbound), "bookable calls only" (booked/(booked+unbooked)), per-agent (booked/answered) | ServiceTitan's native definition uses a single "call lead" concept (≥60s duration **or** `Is Lead`) as its one denominator — this CLI's multi-denominator approach is more granular but does not currently replicate the ≥60s/`Is Lead` lead-qualification test. |
| Membership attach | Per-job boolean approximation: same customer + same calendar day, scoped to Service jobs | ServiceTitan defines no equivalent metric at all (Section 6.11); the nearest native concept ("Memberships Converted") is a differently-scoped opportunity metric, not an attach rate — there is nothing more "correct" to converge toward here. `Job.membershipId` (Section 6.1) is a real, unexplored, potentially cleaner signal than the same-day heuristic, but its exact semantics (sold-on-this-job vs. serviced-under-an-existing-membership) are undocumented and would need empirical validation against a live tenant before replacing the heuristic. |

---

## 9. Sources

**Primary (ServiceTitan-authored, developer/API documentation):**

| Tag | Description |
|---|---|
| `[PD]` | Scraped ServiceTitan Developer Portal narrative documentation — OAuth 2.0 setup guide, Developer Portal app-registration and tenant-connection walkthroughs, FAQ (rate limits, token lifetime, Enterprise Hub, customer tunneling), "Transactional APIs" vs. "Data Export APIs" overview, Invoices/Payments/Payment Terms/Bills/Memberships narrative sections, Webhooks section and FAQ. |
| `[TIMEOUTS]` | ServiceTitan's Partner (supply-chain) API portal, `partnerapis.servicetitan.io/docs/timeouts-and-pagination/` — a different API family from the tenant V2 APIs; states a 100-second request timeout and 1-5000/default-50 `pageSize` guidance for that family. Cited as the only ServiceTitan-authored timeout/page-size figures found; not confirmed for tenant endpoints. |
| `[BJS]` | Developer Portal's compiled frontend JavaScript — used to confirm exact UI strings and internal enums (`SecretManagementOption`, button labels, banner text) not present in the narrative doc. |
| `[BDC]` | Developer Portal frontend doc-string/context text — client-secret one-time-reveal and rotation UI copy. |
| `[APIS]` | Developer Portal's per-module catalogue (module id, display name, one-line description, `hasWebhooks`, `isBeta`). |
| `[CAT]` | This research's own flattened catalogue of every `GET` operation's path, operationId, tags, parameters and response fields across all 25 specs. |
| `specs/tenant-<module>.json` (25 files) | The official ServiceTitan OpenAPI 3.0 documents themselves — `paths`, `components.schemas`, `components.securitySchemes` — for `accounting-v2, crm-v2, customer-interactions-v2, dispatch-v2, equipment-systems-v2, fleet-pro-v2, forms-v2, inventory-v2, jbce-v2, jpm-v2, marketing-ads-v2, marketing-reputation-v2, marketing-v2, memberships-v2, payroll-v2, pricebook-v2, reporting-v2, salestech-v2, scheduling-pro-v2, service-agreements-v2, settings-v2, task-management-v2, telecom, timesheets-v2`, plus a `findings-v2` module. Every schema, enum, path list and status-code tally cited in this document was re-verified directly against these files while writing it (not merely copied from prior research notes). |

**This repository (cross-reference for what's already implemented):**

`README.md`, `src/servicetitan_cli/api.py`, `src/servicetitan_cli/fetch.py`,
`src/servicetitan_cli/metrics.py`, `src/servicetitan_cli/config.py`,
`src/servicetitan_cli/snapshot.py`, `src/servicetitan_cli/data/endpoints.json`
(generated by `scripts/build_endpoint_catalogue.py` from the same 25 specs).

**Secondary (not ServiceTitan's developer/API docs — flagged inline
wherever used; treat as lower-confidence corroboration, not a primary
citation):**

| Source | Used for | Reliability note |
|---|---|---|
| `help.servicetitan.com/docs/default-api-rate-limitsfor-regular-apis-and-reporting-apis` | Rate-limit corroboration | ServiceTitan-owned (product help center, not developer docs); independently states the same 600/10s and 1/min-reporting figures as `[PD]`, dated 2026-05-14. |
| `help.servicetitan.com/docs/whats-a-sold-threshold`, `.../technicians-close-or-conversion-rate-does-not-look-correct`, `.../how-to/modular-dashboard-overall-metrics`, `.../docs/memberships-concepts` | Native Close Rate / Average Ticket / Call Booking Rate / Membership definitions (Section 6.11) | ServiceTitan-owned product docs describing the *built-in dashboard*, not the API. Field names referenced (e.g. `soldThreshold`, `noCharge`) were independently cross-checked against the real API schema; the formulas themselves are not part of the API contract. |
| `help.servicetitan.com/docs/use-view-edit-and-deactivate-payment-terms`, `.../how-to/ar-transactions` | AR aging bucket basis (dueDate) and term-less-invoice handling (Section 6.6.7) | Same caveat as above — product documentation of the native AR Transactions report, not an API guarantee. |
| `community.zapier.com/troubleshooting-99/...` | Cross-check on required credential values and a plan-gating claim | Community/support forum, not ServiceTitan-authored — see Unverified below for what it got wrong. |
| `pipelineon.com/blog/servicetitan-api-tutorial/` | Cross-check on 401-on-token-expiry behavior | Third-party tutorial blog — its token-lifetime claim is wrong (see Unverified) and its other claims are treated as unconfirmed. |
| AI web-search summaries (unnamed) | — | Two independent search-summary passes mis-stated the reporting rate limit as "5/minute" — **do not trust search-engine paraphrases** of ServiceTitan's rate-limit page over the primary FAQ text quoted in Section 4.1. |

---

## Unverified (plausible, not confirmed in any primary source)

These items surfaced during research but are **not** confirmed by the
official OpenAPI specs or Developer Portal narrative docs. Treat them as
things to verify empirically (e.g. with a live test call) before relying on
them in the CLI.

- **Whether the `Authorization` header literally requires a `Bearer ` prefix.**
  The Developer Portal's own worked curl example sends the raw token with no
  visible prefix, even though `token_type` in the token response is
  `"Bearer"`; neither the narrative docs nor the OpenAPI security schemes
  state the exact header format either way `[PD]`. Standard RFC 6750 practice
  (and this repo's own working client) uses `Authorization: Bearer <token>`
  — recommended, but confirm with a live call during any client rewrite.
- **A third-party tutorial's claim that an expired token surfaces as a live
  401** (*"When the token expires, you get a 401 and refresh it"*) —
  plausible and consistent with this repo's own defensive 401-handling code,
  but only weakly corroborated by a non-ServiceTitan blog post. **The same
  source's claim that token validity is "an hour or so" is simply wrong**
  (the authoritative figure is 900 seconds / 15 minutes, Section 2.3) — treat
  that source's other claims cautiously **(secondary — pipelineon.com)**.
- **Whether ServiceTitan API access requires a specific subscription tier**
  ("A ServiceTitan account on The Works or Legacy package (API access is
  only included with these service agreements)") — stated verbatim on a
  Zapier community troubleshooting page, but this plan-gating detail does not
  appear anywhere in the official Developer Portal docs or any of the 25
  specs **(secondary — community.zapier.com)**. If a tenant admin reports
  they cannot find the API Application Access screen at all (not just "app
  not listed"), it may be worth asking about their subscription plan in
  addition to the permission-based explanations in Section 3.1, but this is
  not ServiceTitan-confirmed.
- **The exact platform-wide `pageSize` ceiling (1-5000) applied to any of
  this CLI's 12 target entities specifically.** ServiceTitan's Partner-API
  portal's "Timeouts and Pagination" page states this for that API family `[TIMEOUTS]`,
  but no per-endpoint OpenAPI schema for jobs/invoices/estimates/customers/
  etc. encodes a `maximum` above 50's implicit default (Section 5.1) — not
  contradicted, just not independently confirmed at the schema level for
  those entities.
- **Whether the `tn.rpr.report-data-ready` webhook event (referenced in the
  Reporting API's `notifyWhenReady` parameter description) is actually
  subscribable.** The Reporting module is flagged `hasWebhooks: false` in
  `apis.json`, and the event is never named in the narrative Webhooks FAQ
  `[APIS; PD]` — internally inconsistent, not resolved by any source found.
- **The exact `report_category`/`reportId` for a canned AR-aging-equivalent
  report**, if one exists per tenant. ServiceTitan's product ships reports
  named "AR Transactions" and "AR Transactions By Customer"
  **(secondary — help.servicetitan.com page titles only)**, plausibly
  reachable through the dynamic Reporting API's category/report listing, but
  this is unconfirmed against a live tenant and, per Section 7, unreachable
  by a GET-only client regardless.
