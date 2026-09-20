"""Output: terminal table, JSON, and the cleanup script.

The cleanup script is *written*, never executed. zombiescan has no code path
that runs it.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from rich.console import Console
from rich.table import Table

from zombiescan.engine import ScanResult


def _money(amount: float, approximate: bool = False) -> str:
    return f"{'~' if approximate else ''}${amount:,.2f}"


def render(result: ScanResult, console: Console) -> None:
    region_word = "region" if len(result.regions) == 1 else "regions"
    scope = f"{len(result.regions)} {region_word}"

    if not result.findings:
        console.print(f"\n[green]No waste found[/green] across {scope}. Nothing to clean up.\n")
        _render_errors(result, console)
        return

    table = Table(
        title=(
            f"zombiescan — {len(result.findings)} finding"
            f"{'' if len(result.findings) == 1 else 's'} across {scope}"
        ),
        title_style="bold",
        header_style="bold",
        expand=True,
    )
    table.add_column("Region", no_wrap=True)
    table.add_column("Resource", no_wrap=True)
    table.add_column("Monthly", justify="right", no_wrap=True)
    table.add_column("Why")

    for finding in result.findings:
        table.add_row(
            finding.region,
            finding.resource_id,
            _money(finding.monthly_cost, finding.approximate_cost),
            finding.reason,
        )

    console.print()
    console.print(table)

    total = result.total_monthly_cost
    console.print(
        f"\n[bold]Estimated waste: {_money(total)}/month ({_money(total * 12)}/year)[/bold]"
    )
    if any(f.approximate_cost for f in result.findings):
        # The tilde covers more than one kind of imprecision -- fallback-region
        # pricing, an unknown resource type, and snapshot upper bounds all set
        # it -- so the legend must not claim a single cause.
        console.print(
            "[dim]~ marks an estimate or upper bound; the per-finding 'note' in "
            "--json output says why.[/dim]"
        )
    console.print(
        "[dim]Estimates from list prices, not your bill. zombiescan is read-only "
        "and deleted nothing.[/dim]"
    )
    _render_errors(result, console)


def _render_errors(result: ScanResult, console: Console) -> None:
    if not result.errors:
        return
    console.print(
        f"\n[yellow]{len(result.errors)} region/check pair(s) could not be scanned:[/yellow]"
    )
    for error in result.errors[:10]:
        console.print(f"  [dim]{error.region} {error.check}: {error.message}[/dim]")
    if len(result.errors) > 10:
        console.print(f"  [dim]... and {len(result.errors) - 10} more[/dim]")


def to_json(result: ScanResult, caller_arn: str | None = None) -> dict[str, Any]:
    return {
        "generated": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "caller_arn": caller_arn,
        "regions": result.regions,
        "total_monthly_cost": round(result.total_monthly_cost, 2),
        "total_annual_cost": round(result.total_monthly_cost * 12, 2),
        "finding_count": len(result.findings),
        "findings": [f.to_dict() for f in result.findings],
        "errors": [
            {"region": e.region, "check": e.check, "message": e.message} for e in result.errors
        ],
    }


def write_json(result: ScanResult, path: str, caller_arn: str | None = None) -> None:
    with open(path, "w") as handle:
        json.dump(to_json(result, caller_arn), handle, indent=2)
        handle.write("\n")


def to_script(result: ScanResult) -> str:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "#!/usr/bin/env bash",
        f"# zombiescan cleanup plan — generated {stamp}",
        "#",
        "# READ EVERY LINE BEFORE RUNNING THIS.",
        "# zombiescan generated this file and did not run it. Deleting AWS",
        "# resources is not reversible. Volumes are snapshotted first below,",
        "# but a snapshot is not a substitute for knowing what you are deleting.",
        "#",
        f"# {len(result.findings)} resource(s), about ${result.total_monthly_cost:,.2f}/month.",
        "",
        "set -euo pipefail",
        "",
    ]
    for finding in result.findings:
        lines.append(f"# {finding.region} {finding.resource_id} — {finding.reason}")
        lines.append(f"# saves about ${finding.monthly_cost:,.2f}/month")
        lines.append(finding.remediation)
        lines.append("")
    return "\n".join(lines)


def write_script(result: ScanResult, path: str) -> None:
    with open(path, "w") as handle:
        handle.write(to_script(result))
