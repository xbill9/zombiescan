"""Lightsail block storage disks attached to no instance.

A Lightsail disk is billed per provisioned GB from creation to deletion,
whether or not anything is attached to it. Detaching a disk to move it
somewhere else, then never reattaching it, leaves the full charge running.

System disks are excluded: they belong to an instance and are already covered
by that instance's bundle price, so counting them here would bill the same
storage twice.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "lightsail-unattached-disk"


def build_finding(ctx: ScanContext, disk: dict[str, Any]) -> Finding:
    name = disk["name"]
    size_gb = int(disk.get("sizeInGb") or 0)
    price, approximate = ctx.pricing.rate("lightsail.disk_gb_month", region=ctx.region)
    quoted = shlex.quote(name)

    return Finding(
        check=CHECK_NAME,
        resource_id=name,
        resource_type="lightsail-disk",
        region=ctx.region,
        reason=f"{size_gb} GB Lightsail disk is attached to no instance",
        monthly_cost=size_gb * price,
        remediation=(
            f"aws lightsail create-disk-snapshot --disk-name {quoted} "
            f"--disk-snapshot-name {shlex.quote(name + '-final')} --region {ctx.region} "
            f"&& aws lightsail delete-disk --disk-name {quoted} --region {ctx.region}"
        ),
        approximate_cost=approximate,
        details={
            "size_gb": size_gb,
            "state": disk.get("state"),
            "created_at": str(disk.get("createdAt") or ""),
            "note": "snapshot first; deleting a disk destroys its contents with no undo",
        },
    )


@check(CHECK_NAME, "Lightsail disks attached to no instance")
def lightsail_unattached_disk(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("lightsail")
    for page in client.get_paginator("get_disks").paginate():
        for disk in page.get("disks", []):
            if disk.get("isAttached") or disk.get("isSystemDisk"):
                continue
            if not disk.get("name"):
                continue
            yield build_finding(ctx, disk)
