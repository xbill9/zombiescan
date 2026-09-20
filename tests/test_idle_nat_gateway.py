from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.idle_nat_gateway import idle_nat_gateway


@pytest.fixture
def findings(make_context, request):
    region = getattr(request, "param", "us-east-1")
    data = load_fixture("idle_nat_gateway")
    ctx, _ = make_context(
        {
            "describe_nat_gateways": [{"NatGateways": data["NatGateways"]}],
            "describe_network_interfaces": [{"NetworkInterfaces": data["NetworkInterfaces"]}],
        },
        region=region,
    )
    return {f.resource_id: f for f in idle_nat_gateway(ctx)}


def test_flags_the_gateway_with_nothing_behind_it(findings):
    assert set(findings) == {"nat-00000000000000001"}


def test_gateway_with_a_workload_interface_is_kept(findings):
    assert "nat-00000000000000002" not in findings


def test_a_gateways_own_interface_does_not_make_it_look_busy(findings):
    """Every NAT gateway holds an in-use ENI. Counting it would flag nothing, ever."""
    assert "nat-00000000000000001" in findings


def test_cost_is_hourly_rate_times_month(findings):
    assert findings["nat-00000000000000001"].monthly_cost == pytest.approx(0.045 * 730)


@pytest.mark.parametrize("findings", ["eu-west-1"], indirect=True)
def test_regional_rate_is_used(findings):
    assert findings["nat-00000000000000001"].monthly_cost == pytest.approx(0.048 * 730)


def test_heuristic_limitation_is_disclosed(findings):
    assert "bursts" in findings["nat-00000000000000001"].details["note"]
