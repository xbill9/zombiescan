from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.packs.core.unassociated_eip import unassociated_eip


@pytest.fixture
def findings(make_context):
    ctx, _ = make_context(direct={"describe_addresses": load_fixture("unassociated_eip")})
    return {f.resource_id: f for f in unassociated_eip(ctx)}


def test_flags_only_addresses_associated_with_nothing(findings):
    assert set(findings) == {"eipalloc-00000000000000001", "203.0.113.13"}


def test_address_with_an_association_is_skipped(findings):
    assert "eipalloc-00000000000000002" not in findings


def test_address_on_an_interface_is_skipped(findings):
    """AssociationId can be absent mid-association; NetworkInterfaceId still means in use."""
    assert "eipalloc-00000000000000003" not in findings


def test_cost_is_the_flat_public_ipv4_rate(findings):
    assert findings["eipalloc-00000000000000001"].monthly_cost == pytest.approx(0.005 * 730)


def test_release_by_allocation_id_when_present(findings):
    remediation = findings["eipalloc-00000000000000001"].remediation
    assert "release-address --allocation-id eipalloc-00000000000000001" in remediation


def test_classic_address_is_released_by_ip(findings):
    """EC2-Classic addresses have no allocation id and cannot use --allocation-id."""
    assert findings["203.0.113.13"].remediation == (
        "aws ec2 release-address --public-ip 203.0.113.13 --region us-east-1"
    )


def test_name_tag_is_surfaced(findings):
    assert findings["eipalloc-00000000000000001"].details["name"] == "old-bastion-ip"
