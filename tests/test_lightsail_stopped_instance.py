from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.packs.lightsail.stopped_instance import lightsail_stopped_instance


@pytest.fixture
def findings(make_context):
    ctx, _ = make_context({"get_instances": [load_fixture("lightsail_stopped_instance")]})
    return {f.resource_id: f for f in lightsail_stopped_instance(ctx)}


def test_flags_stopped_instances_only(findings):
    assert set(findings) == {"retired-blog", "next-gen-bundle"}


def test_running_instance_is_not_waste(findings):
    assert "live-api" not in findings


def test_transitional_state_is_skipped(findings):
    """pending/stopping/starting are mid-move and would flap between scans."""
    assert "mid-restart" not in findings


def test_stopped_instance_costs_the_whole_bundle(findings):
    """The point of the check: stopping a Lightsail instance saves nothing."""
    assert findings["retired-blog"].monthly_cost == pytest.approx(7.0)


def test_unknown_bundle_reports_no_price_rather_than_zero_confidence(findings):
    """A newer bundle generation must not silently price as free."""
    finding = findings["next-gen-bundle"]
    assert finding.monthly_cost == 0.0
    assert finding.approximate_cost is True


def test_reason_explains_the_ec2_difference(findings):
    assert "stopping does not pause the charge" in findings["retired-blog"].reason


def test_remediation_snapshots_before_deleting(findings):
    remediation = findings["retired-blog"].remediation
    assert remediation.index("create-instance-snapshot") < remediation.index("delete-instance")
