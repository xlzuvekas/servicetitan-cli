# Brand assets

Drop your logo files here:

```
logo-light.png     used on light backgrounds
logo-dark.png      used on dark backgrounds
```

Both are optional. With neither, the header shows your profile's company name
set as a wordmark, which looks intentional rather than unfinished.

With only `logo-light.png`, that file is used in both themes.

**Sizing** — the header renders the logo 38px tall. Supply roughly 3× that
(~120px tall) so it stays sharp on high-density screens. A wide horizontal
lockup works best; a square icon will look small next to the text beside it.

**Format** — transparent PNG, trimmed of surrounding whitespace.

**Dark mode** — most marks go muddy on a dark ground. If yours is dark, produce
`logo-dark.png` as a genuinely lightened build rather than relying on a CSS
filter, which desaturates brand colours.

These files are inlined into `dashboard.html` by `servicetitan render` (looked
up in `./brand/`, or `--brand-dir`), so the output
stays a single portable file. They are gitignored by default — see `.gitignore`
if you want them version-controlled in a private repo.

Full guidance: [../BRANDING.md](../BRANDING.md)
