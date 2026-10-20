#!/usr/bin/env python3
"""Regenerate the packaged catalogue src/servicetitan_cli/data/endpoints.json.

    python scripts/build_endpoint_catalogue.py                 # download from the developer portal
    python scripts/build_endpoint_catalogue.py --specs-dir DIR # reuse downloaded specs (tenant-*.json + apis.json)

Users refresh their own copy at runtime with `servicetitan api catalogue refresh`;
this script updates the baseline that ships in the package.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from servicetitan_cli import catalogue  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "src", "servicetitan_cli", "data", "endpoints.json")


def main():
    log = lambda m: sys.stderr.write(m + "\n")
    if "--specs-dir" in sys.argv:
        doc = catalogue.build_from_dir(sys.argv[sys.argv.index("--specs-dir") + 1], progress=log)
    else:
        doc = catalogue.build_from_portal(progress=log)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=0, separators=(",", ":"), ensure_ascii=False)
    print("wrote %s (%d operations, %d GET, generated %s)" % (os.path.normpath(OUT), doc["count"], doc["readCount"], doc["generatedAt"]))


if __name__ == "__main__":
    main()
