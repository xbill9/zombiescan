from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.packs.core.stopped_instances import stopped_instances


@pytest.fixture
def result(make_context):
    data = load_fixture("stopped_instances")
    ctx, client = make_context(
        {
            "describe_instances": [{"Reservations": data["Reservations"]}],
            "describe_volumes": [{"Volumes": data["Volumes"]}],
        }
    )
    return {f.resource_id: f for f in stopped_instances(ctx)}, client


def test_flags_the_stopped_instance_holding_disks(result):
    findings, _ = result
    assert set(findings) == {"i-00000000000000001"}


def test_instance_without_ebs_costs_nothing_and_is_skipped(result):
    """An instance-store-only box is free while stopped, so it is not waste."""
    findings, _ = result
    assert "i-00000000000000002" not in findings


def test_queries_only_stopped_instances(result):
    _, client = result
    assert client.calls["describe_instances"] == {
        "Filters": [{"Name": "instance-state-name", "Values": ["stopped"]}]
    }


def test_cost_is_the_sum_of_attached_volumes(result):
    findings, _ = result
    assert findings["i-00000000000000001"].monthly_cost == pytest.approx(100 * 0.08 + 500 * 0.10)


def test_stopped_duration_parsed_from_transition_reason(result):
    findings, _ = result
    assert findings["i-00000000000000001"].details["stopped_days"] > 0


def test_details_list_the_volumes_being_paid_for(result):
    findings, _ = result
    details = findings["i-00000000000000001"].details
    assert details["volume_count"] == 2
    assert details["total_gb"] == 600
    assert set(details["volume_ids"]) == {"vol-00000000000000011", "vol-00000000000000012"}


def test_remediation_images_the_instance_before_terminating(result):
    findings, _ = result
    remediation = findings["i-00000000000000001"].remediation
    assert remediation.index("create-image") < remediation.index("terminate-instances")
