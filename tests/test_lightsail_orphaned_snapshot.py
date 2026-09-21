from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.packs.lightsail.orphaned_snapshot import lightsail_orphaned_snapshot


@pytest.fixture
def findings(make_context):
    data = load_fixture("lightsail_orphaned_snapshot")
    ctx, _ = make_context(
        {
            "get_instances": [{"instances": data["instances"]}],
            "get_disks": [{"disks": data["disks"]}],
            "get_instance_snapshots": [{"instanceSnapshots": data["instanceSnapshots"]}],
            "get_disk_snapshots": [{"diskSnapshots": data["diskSnapshots"]}],
        }
    )
    return {f.resource_id: f for f in lightsail_orphaned_snapshot(ctx)}


def test_flags_snapshots_whose_source_is_gone(findings):
    assert set(findings) == {
        "retired-blog-final",
        "detached-data-final",
        "whole-instance-disk-snap",
    }


def test_snapshot_of_a_live_instance_is_kept(findings):
    assert "live-api-weekly" not in findings


def test_snapshot_of_a_live_disk_is_kept(findings):
    assert "attached-data-backup" not in findings


def test_automatic_snapshots_are_left_to_their_own_schedule(findings):
    assert "auto-snapshot-2026-09-19" not in findings


def test_disk_snapshot_taken_from_an_instance_is_judged_against_instances(findings):
    """It records fromInstanceName and no disk name, so disks are the wrong list."""
    finding = findings["whole-instance-disk-snap"]
    assert finding.details["source_name"] == "retired-blog"


def test_costs_per_gb_of_snapshot_storage(findings):
    assert findings["retired-blog-final"].monthly_cost == pytest.approx(40 * 0.05)
    assert findings["detached-data-final"].monthly_cost == pytest.approx(80 * 0.05)


def test_the_snapshot_may_be_the_last_copy(findings):
    """The whole point of a final snapshot is that the source is gone."""
    assert "only remaining copy" in findings["retired-blog-final"].details["note"]


def test_remediation_matches_the_snapshot_kind(findings):
    assert "delete-instance-snapshot" in findings["retired-blog-final"].remediation
    assert "delete-disk-snapshot" in findings["detached-data-final"].remediation
