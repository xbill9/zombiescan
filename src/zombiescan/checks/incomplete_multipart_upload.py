"""S3 multipart uploads that were started and never finished.

This is the quietest cost on AWS. A failed or abandoned multipart upload
leaves its parts in the bucket, billed at full storage rates, and they do not
appear in the object listing -- not in the console, not in `aws s3 ls`, not in
the bucket size shown on the overview page. The only way to see them is to ask
for them by name.

They accumulate from interrupted `aws s3 cp` runs, crashed backup jobs and
SDK retries, and without a lifecycle rule they stay forever.
"""

from __future__ import annotations

import datetime as dt
import shlex
from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "incomplete-multipart-upload"
BYTES_PER_GB = 1024**3


def _age_days(moment: dt.datetime | None) -> int | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.UTC)
    return (dt.datetime.now(dt.UTC) - moment).days


def _upload_bytes(client: Any, bucket: str, key: str, upload_id: str) -> int:
    total = 0
    pages = client.get_paginator("list_parts").paginate(Bucket=bucket, Key=key, UploadId=upload_id)
    for page in pages:
        for part in page.get("Parts", []):
            total += int(part.get("Size", 0))
    return total


def build_finding(
    ctx: ScanContext, bucket: str, uploads: list[dict[str, Any]], total_bytes: int
) -> Finding:
    stored_gb = total_bytes / BYTES_PER_GB
    price, approximate = ctx.pricing.s3_gb_month(ctx.region)
    ages = [d for d in (_age_days(u.get("Initiated")) for u in uploads) if d is not None]
    oldest = max(ages) if ages else None

    reason = (
        f"{len(uploads)} incomplete multipart upload(s) holding {stored_gb:,.2f} GB "
        f"that no object listing shows"
    )
    if oldest is not None:
        reason += f"; oldest started {oldest} days ago"

    return Finding(
        check=CHECK_NAME,
        resource_id=bucket,
        resource_type="s3-bucket",
        region=ctx.region,
        reason=reason,
        monthly_cost=stored_gb * price,
        # The durable fix is a lifecycle rule, not a one-off abort: without one
        # the parts come straight back with the next interrupted upload.
        remediation=(
            f"aws s3api list-multipart-uploads --bucket {shlex.quote(bucket)} "
            f"--region {ctx.region}  # review, then abort each, and add an "
            f"AbortIncompleteMultipartUpload lifecycle rule"
        ),
        approximate_cost=approximate,
        details={
            "upload_count": len(uploads),
            "total_gb": round(stored_gb, 3),
            "oldest_days": oldest,
            "keys": sorted({u.get("Key", "") for u in uploads})[:10],
            "note": (
                "invisible to object listings and to the bucket size in the console; "
                "priced at S3 Standard rates, which overstates it for buckets whose "
                "parts would land in a cheaper class"
            ),
        },
    )


@check(CHECK_NAME, "S3 buckets holding incomplete multipart uploads")
def incomplete_multipart_upload(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("s3")
    # ListBuckets is global; the BucketRegion filter keeps each region's scan
    # to its own buckets instead of every region reporting all of them.
    pages = client.get_paginator("list_buckets").paginate(BucketRegion=ctx.region)
    buckets = [b["Name"] for page in pages for b in page.get("Buckets", [])]

    for bucket in buckets:
        uploads: list[dict[str, Any]] = []
        for page in client.get_paginator("list_multipart_uploads").paginate(Bucket=bucket):
            uploads.extend(page.get("Uploads", []))
        if not uploads:
            continue
        total = sum(
            _upload_bytes(client, bucket, u["Key"], u["UploadId"])
            for u in uploads
            if u.get("Key") and u.get("UploadId")
        )
        yield build_finding(ctx, bucket, uploads, total)
