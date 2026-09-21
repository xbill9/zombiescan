from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.orphaned_rds_snapshot import orphaned_rds_snapshot


@pytest.fixture
def result(make_context):
    data = load_fixture("orphaned_rds_snapshot")
    ctx, client = make_context(
        {
            "describe_db_instances": [{"DBInstances": data["DBInstances"]}],
            "describe_db_snapshots": [{"DBSnapshots": data["DBSnapshots"]}],
        }
    )
    return {f.resource_id: f for f in orphaned_rds_snapshot(ctx)}, client


def test_flags_the_snapshot_whose_database_is_gone(result):
    findings, _ = result
    assert set(findings) == {"manual-deleted-db-final"}


def test_snapshot_of_a_live_database_is_a_backup_not_debris(result):
    findings, _ = result
    assert "manual-live-db-backup" not in findings


def test_snapshot_still_being_created_is_skipped(result):
    findings, _ = result
    assert "still-copying" not in findings


def test_only_manual_snapshots_are_requested(result):
    """Automated snapshots are deleted with the instance, so asking for them
    would return nothing and waste a page of results."""
    _, client = result
    assert client.calls["describe_db_snapshots"] == {"SnapshotType": "manual"}


def test_cost_is_allocated_storage_at_the_snapshot_rate(result):
    findings, _ = result
    assert findings["manual-deleted-db-final"].monthly_cost == pytest.approx(400 * 0.095)


def test_allocated_versus_stored_gap_is_disclosed(result):
    findings, _ = result
    finding = findings["manual-deleted-db-final"]
    assert finding.approximate_cost is True
    assert "stored size, not allocated" in finding.details["note"]


def test_aurora_gap_is_disclosed(result):
    findings, _ = result
    assert "Aurora" in findings["manual-deleted-db-final"].details["note"]
