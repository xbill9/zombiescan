from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.stopped_rds_instance import stopped_rds_instance


@pytest.fixture
def findings(make_context):
    ctx, _ = make_context({"describe_db_instances": [load_fixture("stopped_rds_instance")]})
    return {f.resource_id: f for f in stopped_rds_instance(ctx)}


def test_flags_only_stopped_instances(findings):
    assert set(findings) == {"legacy-postgres", "stopped-ha-mysql"}


def test_running_instance_is_not_waste(findings):
    assert "production-db" not in findings


def test_single_az_storage_is_priced_at_the_single_az_rate(findings):
    assert findings["legacy-postgres"].monthly_cost == pytest.approx(200 * 0.115)


def test_multi_az_storage_costs_double(findings):
    """Multi-AZ mirrors the storage, and RDS bills for both copies."""
    assert findings["stopped-ha-mysql"].monthly_cost == pytest.approx(100 * 0.23)


def test_the_seven_day_auto_restart_is_disclosed(findings):
    """This is the part people do not know: stopped is not a stable state."""
    assert "7 days" in findings["legacy-postgres"].details["note"]


def test_remediation_takes_a_final_snapshot(findings):
    assert "--final-db-snapshot-identifier" in findings["legacy-postgres"].remediation
