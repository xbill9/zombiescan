"""Report-layer behaviour that is easy to get quietly wrong."""

from __future__ import annotations

from rich.console import Console

from zombiescan.engine import ScanResult
from zombiescan.models import Finding
from zombiescan.report import _representative, render, to_script


def _finding(check: str, resource_id: str, cost: float = 0.0) -> Finding:
    return Finding(
        check=check,
        resource_id=resource_id,
        resource_type="thing",
        region="us-east-1",
        reason="because",
        monthly_cost=cost,
        remediation=f"aws thing delete --id {resource_id}",
    )


def _sorted_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (-f.monthly_cost, f.region, f.resource_id))


def test_every_check_survives_the_limit_when_costs_tie():
    """The real failure: 67 free log groups pushing 18 security groups out of sight."""
    findings = _sorted_findings(
        [_finding("log-group", f"lg-{i}") for i in range(67)]
        + [_finding("security-group", f"sg-{i}") for i in range(18)]
        + [_finding("empty-vpc", "vpc-1")]
    )
    picked = _representative(findings, limit=8)
    assert len(picked) == 8
    assert {f.check for f in picked} == {"log-group", "security-group", "empty-vpc"}


def test_cost_still_wins_when_costs_differ():
    findings = _sorted_findings(
        [_finding("pricey", "a", 100.0), _finding("cheap", "b", 1.0), _finding("cheap", "c", 0.5)]
    )
    picked = _representative(findings, limit=2)
    assert [f.resource_id for f in picked] == ["a", "b"]


def test_no_limit_returns_everything():
    findings = [_finding("x", str(i)) for i in range(5)]
    assert _representative(findings, limit=0) is findings
    assert _representative(findings, limit=99) is findings


def test_more_checks_than_slots_does_not_overflow():
    findings = _sorted_findings([_finding(f"check-{i}", f"r-{i}") for i in range(10)])
    assert len(_representative(findings, limit=3)) == 3


def test_negligible_total_is_not_reported_as_a_dollar_figure(capsys):
    """'$0.00/month ($0.01/year)' reads as a broken tool, not as cleanup debt."""
    result = ScanResult(findings=[_finding("x", "a")], regions=["us-east-1"])
    render(result, Console(width=100, force_terminal=False))
    out = capsys.readouterr().out
    assert "under $0.01/month" in out
    assert "$0.01/year" not in out


def test_script_warns_before_the_first_command():
    result = ScanResult(findings=[_finding("x", "a", 5.0)], regions=["us-east-1"])
    script = to_script(result)
    assert script.index("READ EVERY LINE") < script.index("aws thing delete")
    assert "did not run it" in script
