"""End-to-end smoke test against a real AWS account.

Excluded by default (``addopts = -m 'not live'``). Run it deliberately:

    ZOMBIESCAN_LIVE=1 uv run pytest -m live

Read-only, like everything else here, but it does make real API calls.
"""

from __future__ import annotations

import os

import boto3
import pytest

from zombiescan.engine import resolve_regions, scan, select_checks, verify_credentials
from zombiescan.pricing import PriceTable

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("ZOMBIESCAN_LIVE") != "1",
        reason="set ZOMBIESCAN_LIVE=1 to run against a real account",
    ),
]


@pytest.fixture(scope="module")
def session() -> boto3.Session:
    return boto3.Session()


def test_credentials_resolve(session):
    arn = verify_credentials(session)
    assert arn.startswith("arn:aws")


def test_scan_completes_against_the_default_regions(session):
    result = scan(
        session,
        resolve_regions(session, all_regions=False),
        select_checks(()),
        PriceTable.load(),
    )
    # An empty account is a valid outcome. What must hold is that the scan
    # completed and every finding is internally consistent.
    assert result.errors == []
    for finding in result.findings:
        assert finding.monthly_cost >= 0
        # A global check's findings are labelled "global": Route 53 and friends
        # have no region of their own to be in.
        assert finding.region in result.regions or finding.region == "global"
        assert finding.remediation


def test_mcp_scan_writes_a_report_the_other_tools_can_read(tmp_path):
    """The plugin's path through the same engine, end to end.

    scan_account writes the report; estimate_savings reads it back. If the two
    disagree about the total, the agent and the terminal are quoting different
    numbers for the same account.
    """
    from zombiescan import mcp_server

    path = tmp_path / "live-report.json"
    summary = mcp_server.scan_account({"report_path": str(path), "limit": 3})
    assert summary["caller_arn"].startswith("arn:aws")
    assert summary["report_path"] == str(path)
    assert len(summary["top_findings"]) <= 3

    savings = mcp_server.estimate_savings({"report_path": str(path)})
    assert savings["matched"]["count"] == summary["totals"]["finding_count"]
    assert savings["matched"]["monthly_cost"] == summary["totals"]["monthly_cost"]
