# Setting up ServiceTitan API access

Done once per company, by someone with admin rights in that company's
ServiceTitan account. About ten minutes. You end up with four values per tenant:

| Value | Where it comes from |
|---|---|
| **App key** | The developer portal, on your app's page after it is saved. Belongs to the app, so it is the same for every tenant the app is connected to. |
| **Client id** | ServiceTitan itself, under API Application Access, after connecting the app. |
| **Client secret** | Same place, shown **once**. If lost, generate a new one. |
| **Tenant id** | Same page; also visible in the developer portal app's tenant list. |

Client ids and secrets are environment-specific: production credentials only
work against production, integration credentials only against the integration
environment. The app key is not; one app key serves both. Each tenant needs its
own client id and secret pair (unless the customer runs an Enterprise Hub tenant
network), which is why the CLI stores them per tenant.

Two ServiceTitan permissions are involved, both granted under *Settings > People
> Employees > (employee) > Permissions*: **Generate API Application Key** lets a
user sign in to the developer portal and register apps; **Manage API Application
Access** lets a user connect apps and generate client credentials inside
ServiceTitan. Do not hand either permission to an outside party; the developer
portal docs call that out as a security violation.

## 1. Register an app in the developer portal

1. Go to <https://developer.servicetitan.io> and sign in with your ServiceTitan
   login. When asked, choose the **production** environment (integration is a
   sandbox with its own credentials).
2. Open **Register and manage applications** and choose **Register a new app**.
3. Fill in your organization name, website and email, then an app name (for
   example `Claude`) and a short description (`internal reporting`).
4. **API scopes**: pick *Tenant APIs* and tick the **read** scope of every
   area you want to see. Read and write scopes are separate; a write scope does
   not include read. There are many boxes; the tool reads by default, so leave
   every write scope unticked unless you mean to let it run specific write
   operations. In that case tick only the `:w` scopes those operations need
   (`servicetitan api <module> <resource> <operation> --describe` names the
   scope) and allow them per profile with `servicetitan profile set NAME
   --allow-writes ...`. The snapshot pull needs exactly these read scopes (`servicetitan auth check` reports any that are missing):
   `tn.acc.invoices:r`, `tn.crm.customers:r`, `tn.dis.appointmentassignments:r`, `tn.inv.purchaseorders:r`, `tn.inv.vendors:r`, `tn.jpm.appointments:r`, `tn.jpm.jobs:r`, `tn.jpm.jobtypes:r`, `tn.mem.memberships:r`, `tn.mem.membershiptypes:r`, `tn.mrk.campaigns:r`, `tn.sal.estimates:r`, `tn.stt.businessunits:r`, `tn.stt.employees:r`, `tn.stt.technicians:r`, `tn.tlc.calls:r`. Anything you want to reach through `servicetitan api get`
   needs its own read scope too; `servicetitan api endpoints --search ...` shows
   the scope next to each endpoint. Changing scopes later creates a new app
   version that the tenant admin must approve again ("Upgrade to New Version");
   the keys do not change.
5. Add your **tenant id(s)**. A company with two ServiceTitan accounts adds both.
6. **Client credential management**: two modes.
   - *Tenant-managed* (the default): when the tenant admin allows the app
     inside ServiceTitan, ServiceTitan generates that tenant's client id and
     secret and shows them right there. This is the path the walkthrough
     below assumes and the natural one when you are setting up your own
     company.
   - *Developer-managed* ("I, the app developer, will configure the
     credentials on behalf of each tenant"): the admin's Allow only grants
     permission; the developer then opens the app's **App Connections** page
     in the developer portal, sees the tenant's client id and clicks
     **Generate** for the secret. Use this when an agency runs the app for
     several customers.
7. Save. The app page now shows the **Application Key**. Copy it.

## 2. Connect the app inside ServiceTitan

1. In ServiceTitan go to **Settings > Integrations > API Application Access**.
   You need the *Manage API Application Access* permission.
2. **Connect New App**, select the app you just registered.
3. The form insists on a few dropdowns even for a read-only app (GPS provider,
   report category and the like). Pick anything sensible; they do not affect
   reads.
4. **Allow Access**.
5. Back on the API Application Access page, open the app and **generate the
   Client ID and Client Secret**. Copy both now; the secret is not shown again.
   The **Tenant ID** is on the same page.

If the app does not appear in step 2, go back to the developer portal, make
sure the tenant id was added to the app, and save again.

## 3. Store the credentials

```bash
servicetitan profile add acme --company "Acme Home Services" --timezone America/Denver --use
servicetitan tenant add acme --code MAIN --label "Main Office" --tenant-id 123456789 --client-id cid.xxxx
```

At a terminal, `tenant add` prompts for the client secret and app key. From a
script or an agent, pass them on stdin (client secret first, then app key) or
through environment variables:

```bash
printf '%s\n%s\n' "$CLIENT_SECRET" "$APP_KEY" | servicetitan tenant add acme ... --client-secret-stdin --app-key-stdin
servicetitan tenant add acme ... --client-secret-env ST_SECRET --app-key-env ST_APP_KEY
```

The command test-authenticates immediately and shows how your business units
will be classified into Service / Install / Sales. Fix the mapping now if a unit
lands somewhere odd (`servicetitan profile set acme --department-mapping '...'`).

Secrets go to the operating system's credential store. On a headless Linux box
without one they go to an owner-only file in the config directory, and the CLI
says so on stderr.

## 4. Second tenant, second company

- Another ServiceTitan account for the same company (a second physical location
  with its own tenant): `servicetitan tenant add acme --code TUC --label Tucson
  --tenant-id ... --client-id ...`. The app key is reused. Each tenant needs its
  own client id and secret, generated from inside that tenant.
- Another company entirely: `servicetitan profile add othercompany ...` and
  repeat steps 1 to 3 in *their* developer portal and ServiceTitan account.
  Switch with `--profile othercompany` or `servicetitan profile use othercompany`.

## 5. Check and pull

```bash
servicetitan auth check
servicetitan fetch
servicetitan metrics all
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| Token request rejected (400/401) | Client id or secret wrong, or generated in the other environment. Generate a new secret in ServiceTitan. |
| 401 on a data call after a good token | App key wrong, or the app is not connected to this tenant in ServiceTitan. |
| 403 on one endpoint | The app lacks that read scope (`servicetitan auth check` lists the ones fetch needs; `api endpoints` shows each endpoint's). Add it in the developer portal, then approve the new app version in ServiceTitan. |
| 404 on every call | Tenant id does not belong to these credentials. |
| `Two tenants resolve to the same tenant id` | A shared/unsuffixed tenant id was inherited by a second location. Give each its own. |

Rotate the client secret if it is ever exposed. A client id can hold two
secrets at once, so generate the new one first, store it with
`servicetitan tenant set-secret acme --code MAIN --client-secret-stdin`, confirm
with `servicetitan auth check`, then deactivate the old secret in ServiceTitan.
Secrets are shown once when generated; there is no way to view one again.
