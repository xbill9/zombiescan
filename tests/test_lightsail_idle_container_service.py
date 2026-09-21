from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.lightsail_idle_container_service import lightsail_idle_container_service


@pytest.fixture
def result(make_context):
    data = load_fixture("lightsail_idle_container_service")
    ctx, client = make_context(direct={"get_container_services": data})
    return {f.resource_id: f for f in lightsail_idle_container_service(ctx)}, client


def test_flags_services_with_no_deployment(result):
    findings, _ = result
    assert set(findings) == {"never-deployed", "deployment-deleted"}


def test_service_with_a_live_deployment_is_in_use(result):
    findings, _ = result
    assert "dog-or-not-lite" not in findings


def test_disabled_service_is_already_the_fixed_state(result):
    """Disabling is what stops the billing, so it is not a finding."""
    findings, _ = result
    assert "switched-off" not in findings


def test_cost_multiplies_power_by_scale(result):
    findings, _ = result
    assert findings["never-deployed"].monthly_cost == pytest.approx(7.0)
    # small at 15.0, scale 3
    assert findings["deployment-deleted"].monthly_cost == pytest.approx(45.0)


def test_scale_is_spelled_out_in_the_reason(result):
    findings, _ = result
    assert "3 x small node(s)" in findings["deployment-deleted"].reason


def test_remediation_prefers_disabling_over_deleting(result):
    """Disabling is reversible and keeps the URL; deleting is not."""
    findings, _ = result
    assert "--is-disabled" in findings["never-deployed"].remediation


def test_uses_the_unpaginated_call_that_the_api_actually_offers(result):
    _, client = result
    assert [op for op, _ in client.call_log] == ["get_container_services"]
