from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.orphaned_snapshots import orphaned_snapshots


@pytest.fixture
def findings(make_context):
    data = load_fixture("orphaned_snapshots")
    ctx, _ = make_context(
        {
            "describe_snapshots": [{"Snapshots": data["Snapshots"]}],
            "describe_volumes": [{"Volumes": data["Volumes"]}],
            "describe_images": [{"Images": data["Images"]}],
        }
    )
    return {f.resource_id: f for f in orphaned_snapshots(ctx)}


def test_only_the_genuinely_orphaned_snapshot_is_flagged(findings):
    assert set(findings) == {"snap-00000000000000001"}


def test_snapshot_of_a_live_volume_is_kept(findings):
    assert "snap-00000000000000002" not in findings


def test_snapshot_backing_an_ami_is_kept(findings):
    """Deleting this would break the AMI, so it is not waste."""
    assert "snap-00000000000000003" not in findings


def test_snapshot_without_a_source_volume_is_not_judged(findings):
    """No recorded source means we cannot prove it is orphaned. Skip, don't guess."""
    assert "snap-00000000000000004" not in findings


def test_cost_is_an_upper_bound_and_says_so(findings):
    finding = findings["snap-00000000000000001"]
    assert finding.monthly_cost == pytest.approx(100 * 0.05)
    assert finding.approximate_cost is True
    assert "incremental" in finding.details["note"]
    assert finding.details["approximate_reason"] == "incremental-billing"
