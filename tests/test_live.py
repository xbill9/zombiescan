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


def test_scan_completes_against_the_default_region(session):
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
        assert finding.region in result.regions
        assert finding.remediation
