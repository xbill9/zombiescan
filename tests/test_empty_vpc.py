from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.packs.core.empty_vpc import empty_vpc


@pytest.fixture
def result(make_context):
    data = load_fixture("empty_vpc")
    ctx, client = make_context(
        {
            "describe_vpcs": [{"Vpcs": data["Vpcs"]}],
            "describe_network_interfaces": [{"NetworkInterfaces": data["NetworkInterfaces"]}],
        }
    )
    return {f.resource_id: f for f in empty_vpc(ctx)}, client


def test_flags_only_the_truly_empty_vpc(result):
    findings, _ = result
    assert set(findings) == {"vpc-empty00000000001"}


def test_vpc_with_a_live_interface_is_kept(result):
    findings, _ = result
    assert "vpc-busy000000000001" not in findings


def test_vpc_holding_an_available_interface_is_not_empty(result):
    """An unattached ENI is still debris; the VPC is not cleanly empty."""
    findings, _ = result
    assert "vpc-debris0000000001" not in findings


def test_default_vpc_is_skipped(result):
    """Every region has one and an unused default VPC is normal."""
    findings, _ = result
    assert "vpc-default000000001" not in findings


def test_every_interface_state_is_considered(result):
    """Filtering to in-use would call the debris VPC empty."""
    _, client = result
    assert client.calls["describe_network_interfaces"] == {}


def test_remediation_points_at_inspection_not_a_doomed_delete(result):
    """delete-vpc fails until dependencies are gone, so suggesting it would mislead."""
    findings, _ = result
    remediation = findings["vpc-empty00000000001"].remediation
    assert "describe-subnets" in remediation
    assert "delete-vpc " not in remediation


def test_name_tag_is_surfaced(result):
    findings, _ = result
    assert "torn-down-staging" in findings["vpc-empty00000000001"].reason
