"""`brief` is a selection from the metrics areas, so it must agree with them."""
import json

from conftest import FIXED_NOW, run_cli
from servicetitan_cli.brief import build_brief
from servicetitan_cli.dates import get_tz
from servicetitan_cli.metrics import Query, compute
from servicetitan_cli.snapshot import Snapshot


def test_brief_matches_metrics(demo_data):
    snap = Snapshot(demo_data)
    tz = get_tz("America/Denver")
    b = build_brief(snap, FIXED_NOW, tz)
    rev = compute(Query(snap, FIXED_NOW, tz, range_kind="mtd"), "revenue")["kpis"]
    acc = compute(Query(snap, FIXED_NOW, tz, range_kind="mtd"), "accounting")["kpis"]
    assert b["source"] == "snapshot" and b["monthToDate"]["completedRevenue"] == rev["completedRevenue"]
    assert b["monthToDate"]["runRate"] == rev["runRate"]["value"] and b["board"] == rev["board"]
    assert b["receivables"]["totalAR"] == acc["totalAR"] and b["memberships"]["activeMembers"] > 0
    assert set(b["today"]) == {"jobsScheduled", "jobsCompleted", "completedRevenue", "inboundCalls", "booked", "abandoned"}
    assert b["next7Days"]["jobsBooked"] >= 0 and isinstance(b["flags"], list) and "stale" not in " ".join(b["flags"])
    one = build_brief(snap, FIXED_NOW, tz, tenant="SPR")
    assert one["tenant"] == "SPR" and one["monthToDate"]["completedRevenue"] <= b["monthToDate"]["completedRevenue"]


def test_brief_cli_and_csv(capsys, isolated_home, demo_path):
    code, out, err = run_cli(capsys, "brief", "--data", demo_path)
    assert code == 0, err
    assert out["snapshot"]["isDemo"] is True and "monthToDate" in out and "receivables" in out
    code, out, _ = run_cli(capsys, "brief", "--data", demo_path, "--tenant", "LOC2", "--format", "csv")
    assert code == 0 and out.splitlines()[0] == "key,value" and any(line.startswith("monthToDate.completedRevenue,") for line in out.splitlines())
    code, out, _ = run_cli(capsys, "list", "jobs", "--data", demo_path, "--limit", "3", "--format", "csv")
    lines = out.splitlines()
    assert lines[0].startswith("id,jobNumber,date,tenant") and len(lines) == 4
