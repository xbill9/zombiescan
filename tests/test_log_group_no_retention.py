from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.log_group_no_retention import log_group_no_retention


@pytest.fixture
def findings(make_context):
    ctx, _ = make_context({"describe_log_groups": [load_fixture("log_group_no_retention")]})
    return {f.resource_id: f for f in log_group_no_retention(ctx)}


def test_flags_only_groups_that_never_expire_and_hold_data(findings):
    assert set(findings) == {"/aws/lambda/forgotten-fn", "/ecs/legacy-api"}


def test_group_with_a_retention_policy_is_kept(findings):
    assert "/aws/lambda/tidy-fn" not in findings


def test_empty_group_is_not_noise(findings):
    """No retention and no data costs nothing today; reporting it buries the real ones."""
    assert "/aws/codebuild/empty-project" not in findings


def test_cost_uses_binary_gigabytes(findings):
    fifty_gb = 53687091200 / 1024**3
    assert findings["/aws/lambda/forgotten-fn"].monthly_cost == pytest.approx(fifty_gb * 0.03)


def test_saving_is_flagged_as_exposure_not_guaranteed(findings):
    finding = findings["/ecs/legacy-api"]
    assert finding.approximate_cost is True
    assert "not guaranteed saving" in finding.details["note"]


def test_remediation_sets_retention_rather_than_deleting(findings):
    remediation = findings["/ecs/legacy-api"].remediation
    assert "put-retention-policy" in remediation
    assert "delete" not in remediation
