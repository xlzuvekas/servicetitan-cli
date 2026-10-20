"""Categorical chart-palette accessibility checks (OKLCH bands, chroma floor,
WCAG contrast, colour-blind separation under Machado 2009 simulation)."""
import itertools
import math

BAND = {"light": (0.43, 0.77), "dark": (0.48, 0.67)}
CHROMA_FLOOR = 0.10
CVD_TARGET, CVD_FLOOR = 8.0, 6.0
NORMAL_FLOOR = 15.0
CONTRAST_MIN = 3.0
DEFAULT_SURFACE = {"light": "#ffffff", "dark": "#15171a"}
MACHADO = {
    "protan": [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
    "deutan": [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]],
}


def hex2srgb(h):
    h = h.strip().lstrip("#")
    if len(h) != 6 or any(c not in "0123456789abcdefABCDEF" for c in h):
        raise ValueError("Not a 6-digit hex colour: %r" % h)
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


def validate_palette(colours, mode="light", surface=None, pairs="adjacent"):
    """Return a structured report; ``ok`` is False on any hard failure."""
    colours = [c.strip() for c in colours if c and c.strip()]
    if len(colours) < 2:
        raise ValueError("Give at least two colours")
    surface = surface or DEFAULT_SURFACE[mode]
    band = BAND[mode]
    ok = True
    per_colour = []
    for h in colours:
        L, C, H = oklch(h)
        cr = contrast(h, surface)
        b_ok, c_ok = band[0] <= L <= band[1], C >= CHROMA_FLOOR
        ok &= b_ok and c_ok
        per_colour.append({"colour": h, "lightness": round(L, 3), "chroma": round(C, 3), "hue": round(H, 1),
                           "bandOk": b_ok, "chromaOk": c_ok, "contrast": round(cr, 2), "contrastOk": cr >= CONTRAST_MIN})
    combos = (list(itertools.combinations(range(len(colours)), 2)) if pairs == "all"
              else [(i, i + 1) for i in range(len(colours) - 1)])
    per_pair = []
    worst = 999.0
    for i, j in combos:
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
        per_pair.append({"pair": "%s -> %s" % (a, b), "normal": round(n, 1), "protan": round(p, 1),
                         "deutan": round(d, 1), "worst": round(m, 1), "verdict": verdict})
    return {"mode": mode, "surface": surface, "ok": bool(ok), "colours": per_colour, "pairs": per_pair,
            "worstNormalSeparation": round(worst, 1), "thresholds": {"band": band, "chromaFloor": CHROMA_FLOOR,
            "cvdTarget": CVD_TARGET, "cvdFloor": CVD_FLOOR, "normalFloor": NORMAL_FLOOR, "contrastMin": CONTRAST_MIN}}
