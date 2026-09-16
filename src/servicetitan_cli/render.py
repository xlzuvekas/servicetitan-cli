"""Build the self-contained dashboard HTML from the bundled template."""
import base64
import json
import os
import pkgutil

from .errors import ValidationError, DataError
from .snapshot import check_snapshot, problems

TEMPLATE_NAME = "data/dashboard_template.html"
PLACEHOLDERS = ("__LIVE_DATA_JSON__", "__CONFIG_JSON__", "__LOGO_LIGHT__", "__LOGO_DARK__")


def shrink(value):
    """Round money to cents and drop nulls, which the front end treats as absent."""
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, dict):
        return {k: shrink(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [shrink(v) for v in value]
    return value


def inline_png(path):
    if not path or not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def load_template(path=None):
    if path:
        with open(path, encoding="utf-8") as f:
            return f.read()
    raw = pkgutil.get_data("servicetitan_cli", TEMPLATE_NAME)
    if raw is None:
        raise DataError("The dashboard template is missing from the installed package")
    return raw.decode("utf-8")


def render_dashboard(snapshot, settings, out_path, brand_dir="brand", template_path=None, skip_checks=False,
                     max_age_hours=6.0, as_of=None, expected_codes=None):
    """Validate, then write ``out_path``. Returns a report dict."""
    data = snapshot.data
    codes = expected_codes if expected_codes is not None else [l["code"] for l in settings.get("locations", [])]
    results = check_snapshot(data, codes, demo=snapshot.is_demo, max_age_hours=max_age_hours, as_of=as_of)
    bad = problems(results)
    if bad and not skip_checks:
        raise ValidationError("Snapshot failed %d check(s); %s was not written" % (len(bad), out_path),
                              hint="Fix the pull (see details) or pass --skip-checks to render anyway.",
                              details={"failed": bad})
    html = load_template(template_path)
    for token in PLACEHOLDERS:
        if token not in html:
            raise DataError("The template is missing the %s placeholder; has it been edited?" % token)
    payload = json.dumps(shrink(data), separators=(",", ":")).replace("</", "<\\/")
    config = json.dumps(shrink(settings), separators=(",", ":")).replace("</", "<\\/")
    light = inline_png(os.path.join(brand_dir, "logo-light.png")) if brand_dir else ""
    dark = inline_png(os.path.join(brand_dir, "logo-dark.png")) if brand_dir else ""
    for token, value in (("__LIVE_DATA_JSON__", payload), ("__CONFIG_JSON__", config),
                         ("__LOGO_LIGHT__", light), ("__LOGO_DARK__", dark)):
        html = html.replace(token, value)
    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return {
        "output": os.path.abspath(out_path),
        "bytes": len(html.encode("utf-8")),
        "kb": round(len(html.encode("utf-8")) / 1024.0),
        "logos": "custom" if light else "wordmark",
        "isDemo": snapshot.is_demo,
        "checks": results,
        "checksSkipped": bool(bad and skip_checks),
    }
