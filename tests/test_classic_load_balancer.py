from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.classic_load_balancer import classic_load_balancer


@pytest.fixture
def findings(make_context):
    ctx, _ = make_context({"describe_load_balancers": [load_fixture("classic_load_balancer")]})
    return {f.resource_id: f for f in classic_load_balancer(ctx)}


def test_flags_the_balancer_with_no_instances(findings):
    assert set(findings) == {"legacy-empty-elb"}


def test_balancer_with_a_registered_instance_is_kept(findings):
    assert "legacy-live-elb" not in findings


def test_priced_at_the_classic_rate(findings):
    """Classic balancers cost more per hour than an ALB, and are priced separately."""
    assert findings["legacy-empty-elb"].monthly_cost == pytest.approx(0.025 * 730)


def test_remediation_uses_the_v1_api(findings):
    remediation = findings["legacy-empty-elb"].remediation
    assert remediation.startswith("aws elb delete-load-balancer")
    assert "elbv2" not in remediation
