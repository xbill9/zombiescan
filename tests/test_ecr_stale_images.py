from __future__ import annotations

import datetime as dt

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.ecr_stale_images import ecr_stale_images

GB = 1024**3
ECR_GB_MONTH = 0.10

# The fixture's recorded repositories are stale and only get staler, so their
# dates cannot rot. A repository that must *not* be flagged has no such luxury:
# any date written into the fixture would cross the 90-day line eventually and
# start failing. So the fresh one is built relative to now.
FRESH_REPO = "currency-mesh"


def _fresh(days: int) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(days=days)


@pytest.fixture
def result(make_context):
    data = load_fixture("ecr_stale_images")
    repositories = [*data["Repositories"], {"repositoryName": FRESH_REPO}]
    images = dict(data["ImagesByRepository"])
    images[FRESH_REPO] = [
        {"imageDigest": "sha256:aa", "imageSizeInBytes": 1_000_000, "imagePushedAt": _fresh(200)},
        {"imageDigest": "sha256:bb", "imageSizeInBytes": 2_000_000, "imagePushedAt": _fresh(5)},
    ]
    ctx, client = make_context(
        {
            "describe_repositories": [{"repositories": repositories}],
            "describe_images": lambda repositoryName: [
                {"imageDetails": images.get(repositoryName, [])}
            ],
        }
    )
    return {f.resource_id: f for f in ecr_stale_images(ctx)}, client


def test_flags_only_repositories_nothing_has_pushed_to(result):
    findings, _ = result
    assert set(findings) == {"ai-course-creator-app", "mcp-lambda-rust-aws"}


def test_one_recent_push_keeps_a_repository_of_old_images(result):
    """The newest push decides, not the average: a live repo keeps old tags too."""
    findings, _ = result
    assert FRESH_REPO not in findings


def test_empty_repository_is_not_reported(result):
    """No images means no stored bytes, so there is nothing to bill or delete."""
    findings, _ = result
    assert "cdk-hnb659fds-container-assets-111122223333-us-east-1" not in findings


def test_cost_sums_every_image_in_the_repository(result):
    findings, _ = result
    expected_gb = (245184050 + 1569 + 244468299 + 244464758) / GB
    assert findings["ai-course-creator-app"].monthly_cost == pytest.approx(
        expected_gb * ECR_GB_MONTH
    )


def test_manifest_list_without_a_size_does_not_break_the_sum(result):
    """A multi-arch index carries no imageSizeInBytes of its own."""
    findings, _ = result
    finding = findings["ai-course-creator-app"]
    assert finding.details["image_count"] == 5
    # Five images counted, but only the four that report bytes contribute.
    assert finding.details["total_gb"] == pytest.approx(
        (245184050 + 1569 + 244468299 + 244464758) / GB, abs=1e-3
    )


def test_untagged_images_are_counted_and_called_out(result):
    findings, _ = result
    assert findings["mcp-lambda-rust-aws"].details["untagged_count"] == 2
    assert "2 untagged" in findings["mcp-lambda-rust-aws"].reason


def test_age_is_measured_from_the_newest_push(result):
    findings, _ = result
    finding = findings["ai-course-creator-app"]
    assert finding.details["newest_push_days"] < finding.details["oldest_push_days"]
    assert f"nothing pushed in {finding.details['newest_push_days']} days" in finding.reason


def test_cost_is_always_marked_approximate(result):
    """Shared layers are stored once and counted once per image, so this is a ceiling."""
    findings, _ = result
    finding = findings["ai-course-creator-app"]
    assert finding.approximate_cost is True
    assert finding.details["approximate_reason"] == "shared-layers"
    assert "upper bound" in finding.details["note"]


def test_remediation_points_at_a_lifecycle_policy_not_just_a_delete(result):
    """Deleting tags once without a policy means the next builds refill the repo."""
    findings, _ = result
    remediation = findings["ai-course-creator-app"].remediation
    assert "lifecycle policy" in remediation
    assert "describe-images" in remediation


def test_check_makes_no_mutating_call(result):
    _, client = result
    assert {op for op, _ in client.call_log} == {"describe_repositories", "describe_images"}
