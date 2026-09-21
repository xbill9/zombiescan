"""Command line entry point."""

from __future__ import annotations

import sys

import boto3
import click
from rich.console import Console

from zombiescan import __version__, report
from zombiescan.engine import (
    CredentialError,
    resolve_regions,
    scan,
    select_checks,
    verify_credentials,
)
from zombiescan.pricing import PriceTable
from zombiescan.registry import CHECKS


@click.group()
@click.version_option(__version__, prog_name="zombiescan")
def main() -> None:
    """Find the AWS resources nobody is using, and what they cost you.

    Read-only: zombiescan makes Describe/List/Get calls only and never deletes
    anything.
    """


@main.command("checks")
def list_checks() -> None:
    """List the available checks."""
    console = Console()
    for name, spec in sorted(CHECKS.items()):
        console.print(f"  [bold]{name}[/bold]  {spec.title}")


@main.command()
@click.option("--profile", default=None, help="AWS profile to use.")
@click.option("--region", "regions", multiple=True, help="Region to scan (repeatable).")
@click.option("--all-regions", is_flag=True, help="Scan every region this account has enabled.")
@click.option("--check", "checks", multiple=True, help="Run only this check (repeatable).")
@click.option("--json", "json_path", default=None, help="Write findings as JSON to this path.")
@click.option("--script", "script_path", default=None, help="Write the cleanup plan to this path.")
@click.option(
    "--min-cost",
    default=0.0,
    show_default=True,
    help="Hide findings cheaper than this many USD per month.",
)
@click.option(
    "--limit",
    default=25,
    show_default=True,
    help="Rows in the detail table; 0 shows every finding.",
)
def scan_command(
    profile: str | None,
    regions: tuple[str, ...],
    all_regions: bool,
    checks: tuple[str, ...],
    json_path: str | None,
    script_path: str | None,
    min_cost: float,
    limit: int,
) -> None:
    """Scan for unused resources."""
    console = Console()
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()

    try:
        caller_arn = verify_credentials(session)
        selected = select_checks(checks)
        target_regions = list(regions) if regions else resolve_regions(session, all_regions)
    except CredentialError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(2)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(2)

    console.print(f"[dim]Scanning as {caller_arn}[/dim]")
    console.print(
        f"[dim]{len(target_regions)} region(s), {len(selected)} check(s) — read-only[/dim]"
    )

    pricing = PriceTable.load()
    with console.status("Scanning..."):
        result = scan(session, target_regions, selected, pricing)

    if min_cost > 0:
        kept = [f for f in result.findings if f.monthly_cost >= min_cost]
        hidden = len(result.findings) - len(kept)
        result.findings = kept
        if hidden:
            console.print(f"[dim]{hidden} finding(s) below ${min_cost:,.2f}/month hidden[/dim]")

    report.render(result, console, limit=limit)

    if json_path:
        report.write_json(result, json_path, caller_arn)
        console.print(f"[dim]findings written to {json_path}[/dim]")
    if script_path:
        report.write_script(result, script_path)
        console.print(
            f"[dim]cleanup plan written to {script_path} — review it before running it[/dim]"
        )


if __name__ == "__main__":
    main()
