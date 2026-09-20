from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.unattached_ebs import unattached_ebs


def _run(make_context, region="us-east-1"):
    ctx, client = make_context(
        {"describe_volumes": [load_fixture("unattached_ebs")]}, region=region
    )
    return {f.resource_id: f for f in unattached_ebs(ctx)}, client


@pytest.fixture
def findings(make_context):
    return _run(make_context)[0]


def test_flags_every_available_volume(findings):
    assert set(findings) == {
        "vol-00000000000000001",
        "vol-00000000000000002",
        "vol-00000000000000003",
        "vol-00000000000000004",
    }


def test_queries_only_available_volumes(make_context):
    """The check must filter server-side, not pull every volume in the account."""
    _, client = _run(make_context)
    assert client.calls["describe_volumes"] == {
        "Filters": [{"Name": "status", "Values": ["available"]}]
    }


@pytest.mark.parametrize(
    ("volume_id", "expected"),
    [
        ("vol-00000000000000001", 100 * 0.08),
        ("vol-00000000000000002", 500 * 0.10),
        ("vol-00000000000000003", 200 * 0.125),
    ],
)
def test_cost_is_size_times_region_price(findings, volume_id, expected):
    assert findings[volume_id].monthly_cost == pytest.approx(expected)


def test_region_price_is_used_not_the_fallback(make_context):
    findings, _ = _run(make_context, region="eu-west-1")
    gp3 = findings["vol-00000000000000001"]
    assert gp3.monthly_cost == pytest.approx(100 * 0.088)
    assert gp3.approximate_cost is False


def test_unpriced_region_falls_back_and_is_marked(make_context):
    findings, _ = _run(make_context, region="ap-northeast-3")
    gp3 = findings["vol-00000000000000001"]
    assert gp3.monthly_cost == pytest.approx(100 * 0.08)  # us-east-1 fallback
    assert gp3.approximate_cost is True


def test_unknown_volume_type_is_priced_as_gp3_and_marked(findings):
    unknown = findings["vol-00000000000000004"]
    assert unknown.monthly_cost == pytest.approx(8 * 0.08)
    assert unknown.approximate_cost is True


def test_provisioned_iops_understatement_is_disclosed(findings):
    assert "provisioned IOPS" in findings["vol-00000000000000003"].details["note"]
    assert "note" not in findings["vol-00000000000000001"].details


def test_name_tag_is_surfaced(findings):
    assert findings["vol-00000000000000001"].details["name"] == "orphaned-build-cache"
    assert findings["vol-00000000000000002"].details["name"] is None


def test_remediation_is_text_and_snapshots_first(findings):
    remediation = findings["vol-00000000000000001"].remediation
    assert remediation.startswith("aws ec2 create-snapshot")
    assert "delete-volume --volume-id vol-00000000000000001" in remediation
    assert "--region us-east-1" in remediation


def test_no_mutating_calls_are_possible(findings):
    """Remediation is a string. Nothing in the finding can execute it."""
    for finding in findings.values():
        assert isinstance(finding.remediation, str)
