from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.packs.core.unused_ami import unused_ami


@pytest.fixture
def findings(make_context):
    data = load_fixture("unused_ami")
    ctx, _ = make_context(
        {
            "describe_images": [{"Images": data["Images"]}],
            "describe_instances": [{"Reservations": data["Reservations"]}],
        }
    )
    return {f.resource_id: f for f in unused_ami(ctx)}


def test_flags_the_old_unreferenced_image(findings):
    assert set(findings) == {"ami-00000000000000001"}


def test_image_backing_a_running_instance_is_kept(findings):
    assert "ami-00000000000000002" not in findings


def test_recent_image_is_left_alone(findings):
    """A fresh AMI is probably mid-rollout, so it is too early to call it waste."""
    assert "ami-00000000000000003" not in findings


def test_image_with_no_snapshots_costs_nothing_and_is_skipped(findings):
    assert "ami-00000000000000004" not in findings


def test_cost_sums_every_backing_snapshot(findings):
    assert findings["ami-00000000000000001"].monthly_cost == pytest.approx((30 + 100) * 0.05)


def test_remediation_deletes_snapshots_after_deregistering(findings):
    remediation = findings["ami-00000000000000001"].remediation
    assert remediation.index("deregister-image") < remediation.index("delete-snapshot")
    assert "snap-0000000000000aaa1" in remediation
    assert "snap-0000000000000aaa2" in remediation


def test_launch_template_blind_spot_is_disclosed(findings):
    """The check cannot see ASGs or launch templates. That must be stated, loudly."""
    note = findings["ami-00000000000000001"].details["note"]
    assert "Auto Scaling" in note and "launch template" in note
