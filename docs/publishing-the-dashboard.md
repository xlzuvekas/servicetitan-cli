# Publishing the dashboard as an app

`servicetitan render` produces one self-contained HTML file. "Making it an
app" almost never means a native app; it means putting that file somewhere
people (and a TV in the office) can open it from a bookmark or a home-screen
icon, and refreshing it on a schedule. A static host does that.

## What you need

1. **Somewhere to run the refresh.** A machine or CI job that has the CLI,
   credentials, and a schedule. It runs `servicetitan fetch` then
   `servicetitan render`, and only publishes when both succeed.
2. **A static host with access control.** The page names customers and shows
   revenue, so it must not be public. Netlify (site password / Netlify
   Identity), Vercel (password protection or Vercel Authentication),
   Cloudflare Pages (Cloudflare Access) all work. Plain GitHub Pages does not,
   because it has no access control on a free plan.
3. **Optionally, "install" it.** Add a web manifest and an icon and browsers
   offer *Add to Home Screen* / *Install app*. The result opens full screen
   without browser chrome, which is what most people mean by "app form".

## Netlify

Netlify suits this well: it serves a single file, deploys from a CLI or by
drag-and-drop, and can protect the site.

One-time:

```bash
npm install -g netlify-cli
netlify login
mkdir -p site && netlify sites:create --name acme-ops-dashboard
```

Each refresh (from a scheduled job):

```bash
servicetitan fetch --max-age 2 \
  && servicetitan render --out site/index.html \
  && netlify deploy --prod --dir site --site <site-id>
```

`render` exits non-zero and writes nothing when the pull failed its integrity
checks, so the `&&` chain leaves the last good dashboard live. **Check the
exit code**; do not deploy unconditionally.

Then in the Netlify dashboard: *Site configuration > Access & security* and
turn on site protection (password or Netlify Identity; requires a paid plan for
password protection). Share the URL and the password with the team.

## Vercel and Cloudflare Pages

Same shape: `vercel deploy --prod site` or `wrangler pages deploy site`, then
enable the host's password protection or access product. Vercel's *Deployment
Protection* covers preview and production URLs.

## Where to run the schedule

- **A computer that stays on** (an office machine, a small VPS): cron on
  macOS/Linux, Task Scheduler on Windows. Credentials come from the CLI's own
  store (`servicetitan tenant add`), so nothing is written to a file.
- **GitHub Actions**: `examples/refresh-and-deploy.yml` is a ready workflow.
  The four ServiceTitan values are repository secrets and reach the CLI as
  `SERVICETITAN_*` environment variables, which is the zero-setup credential
  path. Three runs a day on weekdays is a sensible cadence.
- **Claude Code / Cowork routines** work too when they run on a machine that
  has the CLI installed: schedule the same two commands.

## Installable ("Add to Home Screen")

Drop these two files next to `index.html` and add one line to the page's
`<head>`:

`manifest.webmanifest`

```json
{
  "name": "Acme Operations Dashboard",
  "short_name": "Acme Ops",
  "start_url": "./",
  "display": "standalone",
  "background_color": "#f5f6f7",
  "theme_color": "#2996f4",
  "icons": [{ "src": "icon-512.png", "sizes": "512x512", "type": "image/png" }]
}
```

In the template (`servicetitan render --template my_template.html` keeps the
edit out of the package), inside `<head>`:

```html
<link rel="manifest" href="manifest.webmanifest">
```

That is all a phone or a Chromebook needs to offer *Install*. For a TV, open the
URL in kiosk mode and let the page's data-status badge tell you when a refresh
was missed (it turns amber past 12 hours).

## Size

Publishing the page as a Claude artifact has a size limit; a year of data for
two tenants can exceed it. `render` embeds only the fields the dashboard reads
(pass `--full` to embed everything), and a shorter operational window
(`servicetitan profile set acme --window '{"operationalStart": "2026-07-01"}'`)
shrinks it further. Static hosts have no such limit.

## Privacy checklist

- Access control on, before the first deploy.
- The refresh job's credentials are read-only scopes only.
- `dashboard.html`, snapshots and `.env` stay out of git (see `.gitignore`).
- Rotate the client secret if a rendered page or a snapshot ever leaks.
