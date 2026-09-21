from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.unused_vpc_endpoint import unused_vpc_endpoint


@pytest.fixture
def findings(make_context):
    data = load_fixture("unused_vpc_endpoint")
    ctx, _ = make_context(
        {
            "describe_vpc_endpoints": [{"VpcEndpoints": data["VpcEndpoints"]}],
            "describe_network_interfaces": [{"NetworkInterfaces": data["NetworkInterfaces"]}],
        }
    )
    return {f.resource_id: f for f in unused_vpc_endpoint(ctx)}


def test_flags_the_interface_endpoint_in_the_idle_vpc(findings):
    assert set(findings) == {"vpce-00000000000000001"}


def test_gateway_endpoints_are_never_reported(findings):
    """S3 and DynamoDB gateway endpoints are free; calling them waste is wrong."""
    assert "vpce-00000000000000002" not in findings


def test_endpoint_in_a_vpc_with_workloads_is_kept(findings):
    assert "vpce-00000000000000003" not in findings


def test_endpoint_not_yet_available_is_skipped(findings):
    assert "vpce-00000000000000004" not in findings


def test_cost_scales_with_availability_zones(findings):
    """An interface endpoint bills per ENI, i.e. once per AZ it spans."""
    finding = findings["vpce-00000000000000001"]
    assert finding.details["az_count"] == 3
    assert finding.monthly_cost == pytest.approx(3 * 0.01 * 730)


def test_the_vpcs_own_plumbing_does_not_count_as_a_workload(findings):
    """The idle VPC holds only a NAT ENI and the endpoint's own ENI."""
    assert "vpce-00000000000000001" in findings


def test_service_name_is_surfaced_in_the_reason(findings):
    assert "secretsmanager" in findings["vpce-00000000000000001"].reason
