# Branding the dashboard

The default theme is deliberately neutral — considered, but not anyone's brand.
Three things make it yours: the wordmark or logo, the colour tokens, and the
type. All three live in the dashboard template, near the top.

Re-run `servicetitan render` after any change. The template lives at
`src/servicetitan_cli/data/dashboard_template.html`; pass a copy with
`servicetitan render --template my_template.html` to keep edits outside the package.

---

## 1. Name and logo

**With no logo files**, the header shows the profile's company name set as a
wordmark. For many companies that is enough.

**To use a logo**, drop one or both of these in:

```
brand/logo-light.png     shown on light backgrounds
brand/logo-dark.png      shown on dark backgrounds
```

(`brand/` is relative to where you run `servicetitan render`; change it with
`--brand-dir`.)

They are inlined into the HTML at render time, so the output stays a single
portable file.

- **Size**: the header renders it 38px tall. Supply roughly 3× that (~120px
  tall) so it stays sharp on high-density screens. A wide horizontal lockup
  works best.
- **Transparent PNG**, trimmed of surrounding whitespace.
- **Only supplying `logo-light.png`** is fine — it will be used in both themes.
- **Dark-mode variant**: most brand marks go muddy on a dark ground. If your
  logo is dark, make `logo-dark.png` a lightened build rather than relying on a
  CSS filter, which washes brand colours out.

---

## 2. Colours

Open the template and find the block marked `THEME TOKENS`. There
are three copies of the palette — the base `:root`, a
`@media (prefers-color-scheme: dark)` block, and a `:root[data-theme="dark"]`
block. **Edit all three**, or one theme will keep the old colours.

Surfaces and text:

```css
--page          /* behind everything */
--surface-1     /* cards and tiles */
--surface-2     /* recessed areas: the YTD band, drill-downs */
--text-primary --text-secondary --text-muted
--gridline --baseline --border
```

Chart series, in fixed order — the first is used for single-series charts:

```css
--series-1 … --series-5
```

Status colours, reserved and never reused as a series:

```css
--good --warning --serious --critical      /* plus the matching *-bg tints */
```

### Validate before you ship a palette

Chart colours are not a matter of taste alone — they have to stay
distinguishable, including for colour-blind readers. The defaults were checked
against five measurable criteria and pass in both themes. **If you change
`--series-*`, re-check rather than trusting your eye.**

Run the built-in check:

```bash
servicetitan palette "#2996f4,#ed6d28,#2ca67c,#8e2af1,#b39128" --mode light --surface "#ffffff"
servicetitan palette "#2996f4,#e86b27,#2ead82,#8e2af1,#af8e27" --mode dark  --surface "#15171a"
```

It checks each colour's lightness band, chroma floor and contrast against its
surface, and each neighbouring pair's separation under protanopia and
deuteranopia simulation. It exits non-zero on a hard failure and tells you which
pair is the problem.

Two things it reliably catches that eyes miss: hues that look distinct to you
but collapse under red-green colour blindness, and a "dark mode" made by
lightening the light palette rather than re-picking steps for a dark ground.

### Using a two-colour brand

Most brands supply two colours, and a dashboard needs four or five distinguishable
series. Use your brand colours for `--series-1` and `--series-2`, then fill the
rest with hues far from both, and validate. Don't stretch one brand hue into five
tints — they will fail separation immediately.

Keep status colours **separate from your brand palette**. If your brand red is
also `--critical`, a normal "Installation" bar starts reading as an alarm.

---

## 3. Type

No web font loads by default — shipping someone else's typeface would be a
branding decision this template shouldn't make, and it keeps the page working
offline. Two tokens control everything:

```css
--font-display   /* headings, stat values, tab labels */
--font-body      /* everything else, including chart labels */
```

To use a hosted font, add the stylesheet link in `<head>` and point the tokens
at it:

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=YOUR+FONT:wght@400;600;700&display=swap" rel="stylesheet">
```

```css
--font-display: "Your Font", system-ui, sans-serif;   /* always keep a fallback */
```

A licensed font you host yourself works the same way via `@font-face`. If the
dashboard must work offline, embed it as a base64 `@font-face` data URI rather
than linking it.

Always keep the system fallback in the stack. If the font fails to load the page
should still look deliberate.

---

## 4. Wording

Two strings are worth a look:

- The footer note, in `buildChrome()` near the bottom of the template.
- The scorecard caption, built by `targetCaption()` from your configured
  targets (`servicetitan profile set <name> --targets '{...}'`).

---

## Checklist

- [ ] company name and subtitle set (`servicetitan profile set <name> --company ... --subtitle ...`)
- [ ] Logos in `brand/`, or wordmark is what you want
- [ ] Colours edited in **all three** token blocks
- [ ] `servicetitan palette` passes for light **and** dark
- [ ] Type tokens set, with a system fallback
- [ ] Viewed in both light and dark mode
- [ ] Viewed at a narrow width — tiles wrap, tables scroll
