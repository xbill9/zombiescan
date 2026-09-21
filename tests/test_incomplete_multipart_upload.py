from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.incomplete_multipart_upload import incomplete_multipart_upload

GB = 1024**3


@pytest.fixture
def result(make_context):
    data = load_fixture("incomplete_multipart_upload")
    uploads, parts = data["UploadsByBucket"], data["PartsByUpload"]
    ctx, client = make_context(
        {
            "list_buckets": [{"Buckets": data["Buckets"]}],
            "list_multipart_uploads": lambda Bucket: [{"Uploads": uploads.get(Bucket, [])}],
            "list_parts": lambda Bucket, Key, UploadId: [{"Parts": parts.get(UploadId, [])}],
        }
    )
    return {f.resource_id: f for f in incomplete_multipart_upload(ctx)}, client


def test_flags_only_the_bucket_with_stranded_parts(result):
    findings, _ = result
    assert set(findings) == {"backups-prod"}


def test_buckets_are_filtered_to_this_region_server_side(result):
    """ListBuckets is global; without the filter every region reports every bucket."""
    _, client = result
    assert client.calls["list_buckets"] == {"BucketRegion": "us-east-1"}


def test_cost_sums_every_part_of_every_upload(result):
    findings, _ = result
    expected_gb = (5368709120 + 5368709120 + 1073741824) / GB
    assert findings["backups-prod"].monthly_cost == pytest.approx(expected_gb * 0.023)


def test_oldest_upload_age_is_reported(result):
    findings, _ = result
    assert findings["backups-prod"].details["oldest_days"] > 600
    assert findings["backups-prod"].details["upload_count"] == 2


def test_invisibility_is_the_headline(result):
    """These do not appear in any object listing, which is why they survive."""
    findings, _ = result
    assert "no object listing shows" in findings["backups-prod"].reason
    assert "invisible to object listings" in findings["backups-prod"].details["note"]


def test_remediation_points_at_a_lifecycle_rule_not_just_an_abort(result):
    """Aborting once without a lifecycle rule means they come straight back."""
    findings, _ = result
    assert "AbortIncompleteMultipartUpload" in findings["backups-prod"].remediation
