"""EFS file systems with no mount targets.

Without a mount target a file system cannot be reached from any VPC. It is
storage nothing can read, billed per GB every month.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "unmounted-efs"
BYTES_PER_GB = 1024**3


def build_finding(ctx: ScanContext, filesystem: dict[str, Any]) -> Finding:
    fs_id = filesystem["FileSystemId"]
    size = filesystem.get("SizeInBytes") or {}
    # ValueInStandard excludes the Infrequent Access tier, which is priced
    # differently; fall back to the total when it is absent.
    standard_bytes = size.get("ValueInStandard", size.get("Value", 0))
    stored_gb = standard_bytes / BYTES_PER_GB
    price, approximate = ctx.pricing.efs_gb_month(ctx.region)
    name = filesystem.get("Name")

    return Finding(
        check=CHECK_NAME,
        resource_id=fs_id,
        resource_type="efs-filesystem",
        region=ctx.region,
        reason=(
            f"File system {name or fs_id} has no mount targets, so nothing can reach "
            f"the {stored_gb:,.2f} GB it holds"
        ),
        monthly_cost=stored_gb * price,
        remediation=f"aws efs delete-file-system --file-system-id {fs_id} --region {ctx.region}",
        approximate_cost=approximate,
        details={
            "name": name,
            "standard_gb": round(stored_gb, 3),
            "total_bytes": size.get("Value"),
            "performance_mode": filesystem.get("PerformanceMode"),
            "encrypted": filesystem.get("Encrypted"),
            "note": (
                "Standard-tier storage only; Infrequent Access and Archive tiers "
                "are billed separately and not included"
            ),
        },
    )


@check(CHECK_NAME, "EFS file systems with no mount targets")
def unmounted_efs(ctx: ScanContext) -> Iterator[Finding]:
    pages = ctx.client("efs").get_paginator("describe_file_systems").paginate()
    for page in pages:
        for filesystem in page.get("FileSystems", []):
            if filesystem.get("LifeCycleState") != "available":
                continue
            if filesystem.get("NumberOfMountTargets"):
                continue
            yield build_finding(ctx, filesystem)
