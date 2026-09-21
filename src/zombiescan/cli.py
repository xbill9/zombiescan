"""Command line entry point."""

from __future__ import annotations

import json
import sys
import time

import boto3
import botocore.exceptions
import click
from rich import box
from rich.console import Console
from rich.table import Table

from zombiescan import __version__, clean, html, packs, report
from zombiescan.engine import (
    CredentialError,
    filter_us_regions,
    load_packs,
    resolve_regions,
    scan,
    select_checks,
    verify_credentials,
)
from zombiescan.models import Finding
from zombiescan.pricing import PriceTable
from zombiescan.registry import CHECKS


@click.group()
@click.version_option(__version__, prog_name="zombiescan")
def main() -> None:
    """Find the AWS resources nobody is using, and what they cost you.

    Read-only: zombiescan makes Describe/List/Get calls only and never deletes
    anything.
    """


def _load(console: Console, disable_pack: tuple[str, ...] = ()) -> None:
    """Import every enabled pack, and say plainly if one could not be loaded.

    A failed pack is reported rather than raised: it costs the checks it would
    have contributed, not the whole scan. Silence would be worse -- a scan that
    quietly skipped a pack would report less waste and look like good news.
    """
    report_ = load_packs(disabled=frozenset(disable_pack))
    for failure in report_.failed:
        console.print(
            f"[yellow]pack '{failure.name}' ({failure.source}) failed to load:[/yellow] "
            f"{failure.message}"
        )
    for name in report_.skipped:
        console.print(f"[dim]pack '{name}' disabled[/dim]")


@main.command("checks")
def list_checks() -> None:
    """List the available checks."""
    console = Console()
    _load(console)
    for name, spec in sorted(CHECKS.items()):
        console.print(f"  [bold]{name}[/bold]  {spec.title}  [dim]({spec.pack})[/dim]")


@main.command("packs")
def list_packs() -> None:
    """List the installed packs and what each one contributes."""
    console = Console()
    _load(console)
    counts: dict[str, int] = {}
    for spec in CHECKS.values():
        counts[spec.pack] = counts.get(spec.pack, 0) + 1

    table = Table(box=box.SIMPLE, header_style="bold")
    for column in ("Pack", "Version", "Checks", "Source"):
        table.add_column(column)
    for pack in sorted(packs.PACKS.values(), key=lambda p: p.name):
        table.add_row(pack.name, pack.version, str(counts.get(pack.name, 0)), pack.source)
    console.print(table)
    console.print(f"[dim]pack API v{packs.PACK_API_VERSION}[/dim]")


@main.command()
@click.option("--profile", default=None, help="AWS profile to use.")
@click.option("--region", "regions", multiple=True, help="Region to scan (repeatable).")
@click.option("--all-regions", is_flag=True, help="Scan every region this account has enabled.")
@click.option("--us-only", is_flag=True, help="Narrow the regions to scan to the US ones (us-*).")
@click.option("--check", "checks", multiple=True, help="Run only this check (repeatable).")
@click.option(
    "--disable-pack",
    multiple=True,
    help="Do not load this pack (repeatable).",
)
@click.option("--json", "json_path", default=None, help="Write findings as JSON to this path.")
@click.option("--script", "script_path", default=None, help="Write the cleanup plan to this path.")
@click.option(
    "--html", "html_path", default=None, help="Write a shareable HTML report to this path."
)
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
    us_only: bool,
    checks: tuple[str, ...],
    disable_pack: tuple[str, ...],
    json_path: str | None,
    script_path: str | None,
    html_path: str | None,
    min_cost: float,
    limit: int,
) -> None:
    """Scan for unused resources."""
    console = Console()
    _load(console, disable_pack)

    try:
        # Session construction itself raises for an unknown profile, so it
        # belongs inside the handler rather than above it.
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        caller_arn = verify_credentials(session)
        selected = select_checks(checks, frozenset(disable_pack))
        target_regions = list(regions) if regions else resolve_regions(session, all_regions)
        if us_only:
            # Applied after resolution, so the flag narrows an explicit
            # --region list too rather than being silently ignored there.
            target_regions = filter_us_regions(target_regions)
    except botocore.exceptions.ProfileNotFound:
        available = ", ".join(boto3.Session().available_profiles) or "none configured"
        console.print(f"[red]No AWS profile named '{profile}'.[/red] Available: {available}")
        sys.exit(2)
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
    started = time.monotonic()
    with console.status("Scanning..."):
        result = scan(session, target_regions, selected, pricing)
    elapsed = time.monotonic() - started

    hidden = 0
    if min_cost > 0:
        kept = [f for f in result.findings if f.monthly_cost >= min_cost]
        hidden = len(result.findings) - len(kept)
        result.findings = kept
        if hidden and kept:
            console.print(f"[dim]{hidden} finding(s) below ${min_cost:,.2f}/month hidden[/dim]")

    report.render(result, console, limit=limit, hidden_by_filter=hidden)

    if json_path:
        report.write_json(
            result,
            json_path,
            caller_arn=caller_arn,
            duration_seconds=elapsed,
            pricing_generated=pricing.generated,
        )
        console.print(f"[dim]findings written to {json_path}[/dim]")
    if html_path:
        html.write_html(
            result, html_path, caller_arn=caller_arn, pricing_generated=pricing.generated
        )
        console.print(
            f"[dim]HTML report written to {html_path} (print to PDF from a browser)[/dim]"
        )
    if script_path:
        report.write_script(result, script_path)
        console.print(
            f"[dim]cleanup plan written to {script_path} — review it before running it[/dim]"
        )

    # A scan where every single call failed found nothing because it looked at
    # nothing. Exiting 0 would tell a CI job the account is clean.
    if result.completely_failed:
        sys.exit(1)


def _load_findings(path: str) -> tuple[list[Finding], str | None]:
    """Findings from a previously written --json report."""
    with open(path) as handle:
        document = json.load(handle)
    version = document.get("schema_version")
    if version != report.SCHEMA_VERSION:
        raise ValueError(
            f"{path} has schema_version {version}, this build reads "
            f"{report.SCHEMA_VERSION}. Re-run the scan."
        )
    findings = [Finding.from_dict(f) for f in document.get("findings", [])]
    return findings, (document.get("scan") or {}).get("caller_arn")


def _describe(outcome: clean.Outcome, console: Console) -> None:
    cost = f"${outcome.finding.monthly_cost:,.2f}/mo"
    console.print(
        f"\n[bold]{outcome.finding.check}[/bold]  {outcome.finding.resource_id}  "
        f"[dim]{outcome.finding.region} · {cost}[/dim]"
    )
    console.print(f"  [dim]{outcome.finding.reason}[/dim]")
    for step in outcome.steps:
        mark = "[red]IRREVERSIBLE[/red] " if step.irreversible else ""
        console.print(f"    → {mark}{step.description}")
        console.print(f"      [dim]{step.service}.{step.operation}({step.params})[/dim]")


@main.command("clean")
@click.option("--profile", default=None, help="AWS profile to use.")
@click.option("--region", "regions", multiple=True, help="Region to scan (repeatable).")
@click.option("--all-regions", is_flag=True, help="Scan every region this account has enabled.")
@click.option("--check", "checks", multiple=True, help="Only this check (repeatable).")
@click.option(
    "--disable-pack",
    multiple=True,
    help="Do not load this pack (repeatable).",
)
@click.option(
    "--min-cost", default=0.0, show_default=True, help="Ignore findings cheaper than this."
)
@click.option(
    "--from",
    "from_path",
    default=None,
    help="Act on a previously written --json report instead of scanning again.",
)
@click.option(
    "--apply",
    "do_apply",
    is_flag=True,
    help="Actually make the calls. Without this, nothing is changed.",
)
@click.option("--yes", is_flag=True, help="Do not ask before each resource.")
@click.option("--audit", "audit_path", default=None, help="Write a record of what was done.")
def clean_command(
    profile: str | None,
    regions: tuple[str, ...],
    all_regions: bool,
    checks: tuple[str, ...],
    disable_pack: tuple[str, ...],
    min_cost: float,
    from_path: str | None,
    do_apply: bool,
    yes: bool,
    audit_path: str | None,
) -> None:
    """Delete the resources a scan found.

    Dry run by default: it prints the exact calls it would make and changes
    nothing. --apply performs them, asking before each resource unless --yes.
    """
    console = Console()
    _load(console, disable_pack)

    try:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        caller_arn = verify_credentials(session)
    except botocore.exceptions.ProfileNotFound:
        available = ", ".join(boto3.Session().available_profiles) or "none configured"
        console.print(f"[red]No AWS profile named '{profile}'.[/red] Available: {available}")
        sys.exit(2)
    except CredentialError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(2)

    pricing = PriceTable.load()
    home = session.region_name or "us-east-1"

    if from_path:
        try:
            findings, scanned_as = _load_findings(from_path)
        except (OSError, ValueError, KeyError) as exc:
            console.print(f"[red]{exc}[/red]")
            sys.exit(2)
        console.print(f"[dim]{len(findings)} finding(s) from {from_path}[/dim]")
        if scanned_as and scanned_as != caller_arn:
            # Cleaning one account using a report from another would delete
            # resources nobody looked at.
            console.print(
                f"[red]That report was produced as {scanned_as}, but you are "
                f"{caller_arn}.[/red] Re-scan with these credentials."
            )
            sys.exit(2)
    else:
        try:
            selected = select_checks(checks, frozenset(disable_pack))
            target_regions = list(regions) if regions else resolve_regions(session, all_regions)
        except (CredentialError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
            sys.exit(2)
        console.print(f"[dim]Scanning as {caller_arn} — {len(target_regions)} region(s)[/dim]")
        with console.status("Scanning..."):
            result = scan(session, target_regions, selected, pricing)
        if result.completely_failed:
            console.print(
                f"[red]Nothing could be scanned — all {result.attempted} pairs failed. "
                f"Refusing to clean on the basis of no data.[/red]"
            )
            sys.exit(1)
        findings = result.findings

    if checks:
        findings = [f for f in findings if f.check in set(checks)]
    findings = [f for f in findings if f.monthly_cost >= min_cost]

    if not findings:
        console.print("\n[green]Nothing to clean.[/green]")
        return

    outcomes = [clean.plan_for(session, f, pricing, home) for f in findings]
    actionable = [o for o in outcomes if o.status == clean.PLANNED]
    unsupported = [o for o in outcomes if o.status == clean.UNSUPPORTED]

    mode = "[red]APPLY[/red]" if do_apply else "[green]dry run[/green]"
    console.print(
        f"\n{mode} — {len(actionable)} of {len(findings)} finding(s) can be cleaned"
        + (f", {len(unsupported)} cannot" if unsupported else "")
    )

    for outcome in unsupported:
        console.print(
            f"  [yellow]skip[/yellow] {outcome.finding.check} "
            f"{outcome.finding.resource_id}: {outcome.error}"
        )

    if not do_apply:
        for outcome in actionable:
            _describe(outcome, console)
        total = sum(o.finding.monthly_cost for o in actionable)
        irreversible = sum(1 for o in actionable if o.irreversible)
        console.print(
            f"\n[bold]Would free about ${total:,.2f}/month[/bold] across "
            f"{len(actionable)} resource(s); {irreversible} include irreversible steps."
        )
        console.print("[dim]Nothing was changed. Re-run with --apply to perform these.[/dim]")
        if audit_path:
            _write_audit(audit_path, outcomes, False, caller_arn, console)
        return

    all_remaining = yes
    for outcome in actionable:
        _describe(outcome, console)
        if not all_remaining:
            answer = (
                click.prompt(
                    "  apply? [y]es / [n]o / [a]ll remaining / [q]uit",
                    default="n",
                    show_default=False,
                )
                .strip()
                .lower()[:1]
            )
            if answer == "q":
                console.print("[dim]stopped[/dim]")
                break
            if answer == "a":
                all_remaining = True
            elif answer != "y":
                outcome.status = clean.SKIPPED
                console.print("  [dim]skipped[/dim]")
                continue
        clean.apply_outcome(outcome, session, home)
        if outcome.status == clean.APPLIED:
            console.print("  [green]done[/green]")
        else:
            console.print(f"  [red]failed: {outcome.error}[/red]")

    applied = [o for o in outcomes if o.status == clean.APPLIED]
    failed = [o for o in outcomes if o.status == clean.FAILED]
    freed = sum(o.monthly_saving for o in applied)
    console.print(
        f"\n[bold]{len(applied)} cleaned, {len(failed)} failed — "
        f"about ${freed:,.2f}/month freed[/bold]"
    )
    if audit_path:
        _write_audit(audit_path, outcomes, True, caller_arn, console)
    if failed:
        sys.exit(1)


def _write_audit(
    path: str, outcomes: list[clean.Outcome], applied: bool, caller_arn: str, console: Console
) -> None:
    with open(path, "w") as handle:
        json.dump(clean.audit_document(outcomes, applied, caller_arn), handle, indent=2)
        handle.write("\n")
    console.print(f"[dim]audit written to {path}[/dim]")


# Must stay last: everything above registers a subcommand, and running the
# group before a command is defined silently drops it. `python -m zombiescan.cli`
# used to lose `clean` this way.
if __name__ == "__main__":
    main()
