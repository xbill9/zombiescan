from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.lightsail_unattached_static_ip import lightsail_unattached_static_ip


@pytest.fixture
def findings(make_context):
    ctx, _ = make_context({"get_static_ips": [load_fixture("lightsail_unattached_static_ip")]})
    return {f.resource_id: f for f in lightsail_unattached_static_ip(ctx)}


def test_flags_only_the_detached_address(findings):
    assert set(findings) == {"orphaned-ip"}


def test_attached_static_ip_is_free_and_not_reported(findings):
    assert "api-ip" not in findings


def test_costs_the_hourly_rate_for_a_full_month(findings):
    assert findings["orphaned-ip"].monthly_cost == pytest.approx(0.005 * 730)


def test_the_address_is_in_the_finding(findings):
    assert findings["orphaned-ip"].details["ip_address"] == "203.0.113.24"


def test_remediation_warns_the_address_is_given_up(findings):
    assert "release-static-ip" in findings["orphaned-ip"].remediation
    assert "DNS" in findings["orphaned-ip"].details["note"]
