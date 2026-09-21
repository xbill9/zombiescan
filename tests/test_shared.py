"""The VPC-census helpers three checks depend on."""

from __future__ import annotations

from tests.conftest import FakeClient
from zombiescan.helpers import interfaces_by_vpc, name_tag, vpcs_with_workloads

_PAGES = {
    "describe_network_interfaces": [
        {
            "NetworkInterfaces": [
                {
                    "NetworkInterfaceId": "eni-1",
                    "VpcId": "vpc-plumbing",
                    "InterfaceType": "nat_gateway",
                },
                {
                    "NetworkInterfaceId": "eni-2",
                    "VpcId": "vpc-plumbing",
                    "InterfaceType": "vpc_endpoint",
                },
                {"NetworkInterfaceId": "eni-3", "VpcId": "vpc-real", "InterfaceType": "lambda"},
                {"NetworkInterfaceId": "eni-4", "InterfaceType": "interface"},
            ]
        }
    ]
}


def test_a_vpc_of_pure_plumbing_has_no_workloads():
    """NAT gateways and VPC endpoints serving each other is not a workload."""
    assert vpcs_with_workloads(FakeClient(_PAGES)) == {"vpc-real"}


def test_interfaces_without_a_vpc_are_dropped():
    grouped = interfaces_by_vpc(FakeClient(_PAGES))
    assert set(grouped) == {"vpc-plumbing", "vpc-real"}
    assert len(grouped["vpc-plumbing"]) == 2


def test_status_filter_is_applied_server_side():
    client = FakeClient(_PAGES)
    interfaces_by_vpc(client, status="available")
    assert client.calls["describe_network_interfaces"] == {
        "Filters": [{"Name": "status", "Values": ["available"]}]
    }


def test_no_status_means_no_filter():
    client = FakeClient(_PAGES)
    interfaces_by_vpc(client, status=None)
    assert client.calls["describe_network_interfaces"] == {}


def test_name_tag_reads_the_name_and_ignores_the_rest():
    assert (
        name_tag({"Tags": [{"Key": "env", "Value": "dev"}, {"Key": "Name", "Value": "x"}]}) == "x"
    )
    assert name_tag({"Tags": []}) is None
    assert name_tag({}) is None
