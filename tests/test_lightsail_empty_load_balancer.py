from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.lightsail_empty_load_balancer import lightsail_empty_load_balancer


@pytest.fixture
def findings(make_context):
    ctx, _ = make_context({"get_load_balancers": [load_fixture("lightsail_empty_load_balancer")]})
    return {f.resource_id: f for f in lightsail_empty_load_balancer(ctx)}


def test_flags_the_balancer_with_nothing_attached(findings):
    assert set(findings) == {"orphaned-lb"}


def test_unhealthy_instances_are_an_outage_not_waste(findings):
    """Calling a broken deployment 'waste' would point at the wrong problem."""
    assert "outage-lb" not in findings


def test_costs_the_hourly_rate_for_a_full_month(findings):
    assert findings["orphaned-lb"].monthly_cost == pytest.approx(0.024193548 * 730)


def test_warns_that_certificates_go_with_it(findings):
    assert "TLS certificates" in findings["orphaned-lb"].details["note"]
