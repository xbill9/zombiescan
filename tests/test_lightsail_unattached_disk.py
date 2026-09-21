from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.packs.lightsail.unattached_disk import lightsail_unattached_disk


@pytest.fixture
def findings(make_context):
    ctx, _ = make_context({"get_disks": [load_fixture("lightsail_unattached_disk")]})
    return {f.resource_id: f for f in lightsail_unattached_disk(ctx)}


def test_flags_only_the_detached_data_disk(findings):
    assert set(findings) == {"detached-data"}


def test_attached_disk_is_not_waste(findings):
    assert "attached-data" not in findings


def test_system_disk_is_skipped_even_when_detached(findings):
    """Its storage is already inside the instance bundle price."""
    assert "live-api-system-disk" not in findings


def test_costs_per_provisioned_gb(findings):
    assert findings["detached-data"].monthly_cost == pytest.approx(80 * 0.10)


def test_remediation_snapshots_before_deleting(findings):
    remediation = findings["detached-data"].remediation
    assert remediation.index("create-disk-snapshot") < remediation.index("delete-disk")
