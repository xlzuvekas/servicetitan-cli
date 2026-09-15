#!/usr/bin/env python3
"""
Check a categorical chart palette against the measurable accessibility criteria.

    python validate_palette.py "#2996f4,#ed6d28,#2ca67c,#8e2af1,#b39128" --mode light --surface "#ffffff"
    python validate_palette.py "#2996f4,#e86b27,#2ead82,#8e2af1,#af8e27" --mode dark  --surface "#15171a"

Colour is the one part of chart design that can be computed rather than judged,
so compute it. Two failure modes in particular are invisible to normal vision:
hues that look distinct but collapse under red-green colour blindness, and a
"dark mode" built by lightening the light palette instead of re-picking steps
for a dark ground.

Checks, per colour:
  * lightness band   - too light or too dark against its surface and it stops
                       reading as a distinct series
  * chroma floor     - below this a hue reads as grey
  * contrast         - WCAG ratio against the chart surface

Checks, per neighbouring pair:
  * CVD separation   - OKLab deltaE under protanopia and deuteranopia simulation
                       (Machado, Oliveira & Fernandes 2009, severity 1.0)
  * normal-vision    - full-colour readers must be able to tell neighbours apart

Exit code 0 if everything passes (WARNs allowed), 1 on any hard failure.
No dependencies beyond the standard library.
"""
import argparse
import itertools
import math
import sys

BAND = {"light": (0.43, 0.77), "dark": (0.48, 0.67)}   # OKLCH lightness
CHROMA_FLOOR = 0.10
CVD_TARGET, CVD_FLOOR = 8.0, 6.0
NORMAL_FLOOR = 15.0
CONTRAST_MIN = 3.0
DEFAULT_SURFACE = {"light": "#ffffff", "dark": "#15171a"}

MACHADO = {
    "protan": [[0.152286, 1.052583, -0.204868],
               [0.114503, 0.786281, 0.099216],
               [-0.003882, -0.048116, 1.051998]],
    "deutan": [[0.367322, 0.860646, -0.227968],
               [0.280085, 0.672501, 0.047413],
               [-0.011820, 0.042940, 0.968881]],
}


def hex2srgb(h):
    h = h.strip().lstrip("#")
    if len(h) != 6 or any(c not in "0123456789abcdefABCDEF" for c in h):
        raise SystemExit(f"Not a 6-digit hex colour: {h!r}")
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def s2lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lin(h):
    return [s2lin(c) for c in hex2srgb(h)]


def contrast(a, b):
    def lum(h):
        r, g, bl = lin(h)
        return 0.2126 * r + 0.7152 * g + 0.0722 * bl
    hi, lo = sorted([lum(a), lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def oklab_from_lin(rgb):
    r, g, b = rgb
    L_ = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    M_ = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    S_ = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l = L_ ** (1 / 3) if L_ >= 0 else 0
    m = M_ ** (1 / 3) if M_ >= 0 else 0
    s = S_ ** (1 / 3) if S_ >= 0 else 0
    return [0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s]


def oklch(h):
    L, a, b = oklab_from_lin(lin(h))
    return L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def simulate(h, kind):
    r, g, b = lin(h)
    M = MACHADO[kind]
    return [max(0, min(1, M[i][0] * r + M[i][1] * g + M[i][2] * b)) for i in range(3)]


def delta_e(h1, h2, kind=None):
    a = oklab_from_lin(simulate(h1, kind) if kind else lin(h1))
    b = oklab_from_lin(simulate(h2, kind) if kind else lin(h2))
    return 100 * math.dist(a, b)


def main():
    ap = argparse.ArgumentParser(description="Validate a categorical chart palette.")
    ap.add_argument("palette", help="comma-separated hex colours, in the order they are assigned")
    ap.add_argument("--mode", choices=("light", "dark"), default="light")
    ap.add_argument("--surface", help="chart surface colour (defaults per mode)")
    ap.add_argument("--pairs", choices=("adjacent", "all"), default="adjacent",
                    help="'adjacent' suits bar and line charts; 'all' for scatter/maps "
                         "where any two series can end up side by side")
    args = ap.parse_args()

    colours = [c.strip() for c in args.palette.split(",") if c.strip()]
    if len(colours) < 2:
        raise SystemExit("Give at least two colours.")
    surface = args.surface or DEFAULT_SURFACE[args.mode]
    band = BAND[args.mode]
    ok = True

    print(f"\n{args.mode.upper()} palette on surface {surface}  ({len(colours)} colours)\n")
    print(f"{'colour':<9} {'L':>6} {'C':>6} {'hue':>6}  {'band':<6} {'chroma':<7} {'contrast':>9}")
    for h in colours:
        L, C, H = oklch(h)
        cr = contrast(h, surface)
        b_ok, c_ok = band[0] <= L <= band[1], C >= CHROMA_FLOOR
        ok &= b_ok and c_ok
        note = "" if cr >= CONTRAST_MIN else "  WARN low contrast"
        print(f"{h:<9} {L:6.3f} {C:6.3f} {H:6.1f}  "
              f"{'pass' if b_ok else 'FAIL':<6} {'pass' if c_ok else 'FAIL':<7} {cr:8.2f}{note}")

    pairs = (list(itertools.combinations(range(len(colours)), 2)) if args.pairs == "all"
             else [(i, i + 1) for i in range(len(colours) - 1)])
    worst = 999.0
    print(f"\n{'pair':<23} {'normal':>7} {'protan':>7} {'deutan':>7} {'worst':>7}  verdict")
    for i, j in pairs:
        a, b = colours[i], colours[j]
        n = delta_e(a, b)
        p, d = delta_e(a, b, "protan"), delta_e(a, b, "deutan")
        m = min(p, d)
        worst = min(worst, n)
        if n < NORMAL_FLOOR:
            verdict, ok = "FAIL too similar", False
        elif m >= CVD_TARGET:
            verdict = "pass"
        elif m >= CVD_FLOOR:
            verdict = "WARN needs labels"
        else:
            verdict, ok = "FAIL colour-blind", False
        print(f"{a} -> {b:<10} {n:7.1f} {p:7.1f} {d:7.1f} {m:7.1f}  {verdict}")

    print(f"\nworst normal-vision separation: {worst:.1f} (floor {NORMAL_FLOOR})")
    if ok:
        print("PASS - this palette is safe to ship for this mode.\n")
        print("Remember to run the other mode too; a dark palette is its own set of")
        print("steps, not an inversion of the light one.")
        return 0
    print("FAIL - fix the rows marked FAIL above.\n")
    print("Usual fixes: move the offending hue further around the wheel, or change its")
    print("lightness so the pair separates by brightness as well as by hue. If two")
    print("colours simply must stay close, cut one and fold that series into 'Other'.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
