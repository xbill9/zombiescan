#!/usr/bin/env python3
"""Regenerate the evidence for this article.

    uv run python articles/zombiescan-mcp/gather-evidence.py

Read-only against AWS: Describe/List/Get plus Cost Explorer. Writes one file
per artifact into evidence/, each with a captured_utc stamp.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import subprocess
import sys

import boto3

HERE = pathlib.Path(__file__).parent
EVIDENCE = HERE / "evidence"
REPO = HERE.parent.parent
NOW = dt.datetime.now(dt.UTC)
STAMP = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")


def header(what: str) -> str:
    return f"# {what}\n# captured_utc: {STAMP}\n# cwd: {REPO}\n\n"


def run(*args: str) -> str:
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, timeout=600).stdout


def scan_and_total() -> None:
    """One scan, written three ways: the terminal view, the JSON, both sums."""
    from zombiescan import engine, report
    from zombiescan.pricing import PriceTable

    engine.load_packs()
    session = boto3.Session()
    arn = engine.verify_credentials(session)
    regions = engine.resolve_regions(session, all_regions=True)
    pricing = PriceTable.load()
    result = engine.scan(session, regions, engine.select_checks(()), pricing)

    document = report.to_json(result, caller_arn=arn, pricing_generated=pricing.generated)
    (EVIDENCE / "scan.json").write_text(json.dumps(document, indent=2, default=str) + "\n")

    # The two summation orders over the same findings. Rounding each finding
    # first is what the report publishes; rounding the sum once is truer and
    # does not match the rows.
    rows_first = round(sum(round(f.monthly_cost, 2) for f in result.findings), 2)
    exact_first = round(sum(f.monthly_cost for f in result.findings), 2)
    published = round(sum(f["monthly_cost"] for f in document["findings"]), 2)
    by_check = round(sum(r["monthly_cost"] for r in document["totals"]["by_check"].values()), 2)

    (EVIDENCE / "rounding.txt").write_text(
        header("two summation orders over one scan")
        + f"findings                              {len(result.findings)}\n"
        + f"regions                               {len(regions)}\n"
        + f"sum of per-finding costs rounded first {rows_first:.2f}\n"
        + f"sum of exact costs, rounded once       {exact_first:.2f}\n"
        + f"sum of the rows the report publishes   {published:.2f}\n"
        + f"sum of the by_check sections           {by_check:.2f}\n"
        + f"headline totals.monthly_cost           {document['totals']['monthly_cost']:.2f}\n"
        + f"annual                                 {document['totals']['annual_cost']:.2f}\n"
    )


def cli_output() -> None:
    (EVIDENCE / "scan.txt").write_text(
        header("uv run zombiescan scan --all-regions --limit 8")
        + run("uv", "run", "zombiescan", "scan", "--all-regions", "--limit", "8")
    )
    (EVIDENCE / "checks.txt").write_text(
        header("uv run zombiescan checks") + run("uv", "run", "zombiescan", "checks")
    )


def mcp_session() -> None:
    """A real stdio conversation with the server, recorded verbatim."""
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "evidence"}},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "estimate_savings",
                "arguments": {"report_path": str(EVIDENCE / "scan.json")},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "estimate_savings",
                "arguments": {
                    "report_path": str(EVIDENCE / "scan.json"),
                    "checks": ["unattached-ebs-volumes"],
                },
            },
        },
    ]
    proc = subprocess.run(
        ["uv", "run", "zombiescan-mcp"],
        cwd=REPO,
        input="\n".join(json.dumps(r) for r in requests) + "\n",
        capture_output=True,
        text=True,
        timeout=300,
    )
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    out = [header("one stdio session with zombiescan-mcp")]
    for message in lines:
        result = message.get("result", {})
        if "tools" in result:
            out.append(f"tools/list -> {[t['name'] for t in result['tools']]}\n\n")
        elif "content" in result:
            out.append(f"tools/call isError={result['isError']}\n")
            out.append(result["content"][0]["text"] + "\n\n")
        else:
            out.append(json.dumps(message, indent=2) + "\n\n")
    (EVIDENCE / "mcp-session.txt").write_text("".join(out))


def ecr_against_the_bill() -> None:
    """What ECR storage actually billed, beside what the check estimates."""
    ce = boto3.Session().client("ce", region_name="us-east-1")
    today = dt.date.today()
    start = today.replace(day=1)
    response = ce.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": today.isoformat()},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost", "UsageQuantity"],
        GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
        Filter={
            "And": [
                {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Usage"]}},
                {
                    "Dimensions": {
                        "Key": "SERVICE",
                        "Values": ["Amazon EC2 Container Registry (ECR)"],
                    }
                },
            ]
        },
    )
    (EVIDENCE / "cost-explorer-ecr.json").write_text(
        json.dumps({"captured_utc": STAMP, "response": response}, indent=2, default=str) + "\n"
    )

    billed_gb_month = 0.0
    for period in response["ResultsByTime"]:
        for group in period["Groups"]:
            billed_gb_month += float(group["Metrics"]["UsageQuantity"]["Amount"])

    document = json.loads((EVIDENCE / "scan.json").read_text())
    ecr = document["totals"]["by_check"].get("ecr-stale-images", {})
    estimated = ecr.get("monthly_cost", 0.0)
    days = (today - start).days or 1
    projected = billed_gb_month / days * 30
    rate = 0.10

    (EVIDENCE / "derived-figures.txt").write_text(
        header("arithmetic on the artifacts beside this file, not separate measurements")
        + "## ECR: what the check estimates against what the account was billed\n"
        + f"billed GB-Month, {start} to {today} ({days} days)   {billed_gb_month:.2f}\n"
        + f"projected over 30 days                              {projected:.2f} GB-Month\n"
        + f"at the ECR storage rate ${rate:.2f}/GB-month         ${projected * rate:.2f}/month\n"
        + f"ecr-stale-images estimate, {ecr.get('count', 0)} repos       "
        + f"${estimated:.2f}/month\n"
        + f"implied GB at the same rate                         {estimated / rate:.1f} GB\n"
        + "ratio, estimate over billed                         "
        + f"{estimated / (projected * rate):.1f}x\n"
    )


def rounding_example() -> None:
    """The worked example in the article. Arithmetic, no AWS call.

    Four findings priced at the same half-cent, published and totalled both
    ways, so the two-cent gap on the real account has a case small enough to
    check by hand beside it.
    """
    costs = [0.125] * 4
    published = [round(c, 2) for c in costs]
    (EVIDENCE / "rounding-example.txt").write_text(
        header("arithmetic: four findings at one half-cent, totalled both ways")
        + f"per-finding cost                      {costs[0]}\n"
        + f"as published, rounded to the cent     {published[0]}  (x{len(costs)})\n"
        + f"sum of the published figures          {round(sum(published), 2):.2f}\n"
        + f"sum of the exact costs, rounded once  {round(sum(costs), 2):.2f}\n"
        + "\nPython rounds a half to even, so 0.125 publishes as 0.12.\n"
    )


def tests() -> None:
    (EVIDENCE / "pytest.txt").write_text(
        header("uv run pytest -q") + run("uv", "run", "pytest", "-q")
    )


if __name__ == "__main__":
    sys.path.insert(0, str(REPO / "src"))
    EVIDENCE.mkdir(exist_ok=True)
    scan_and_total()
    cli_output()
    mcp_session()
    rounding_example()
    ecr_against_the_bill()
    tests()
    print(f"wrote {len(list(EVIDENCE.iterdir()))} files to {EVIDENCE}")
