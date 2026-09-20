"""Unattached EBS volumes.

A volume in the ``available`` state is attached to nothing. You are billed for
every provisioned GB of it anyway. This is the single most common form of AWS
waste and usually the largest by dollar value.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "unattached-ebs"


def _age_days(created: dt.datetime | None) -> int | None:
    if created is None:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=dt.UTC)
    return (dt.datetime.now(dt.UTC) - created).days


def _name_of(volume: dict[str, Any]) -> str | None:
    for tag in volume.get("Tags") or []:
        if tag.get("Key") == "Name":
            return tag.get("Value")
    return None


def build_finding(ctx: ScanContext, volume: dict[str, Any]) -> Finding:
    volume_id = volume["VolumeId"]
    size_gb = int(volume.get("Size", 0))
    volume_type = volume.get("VolumeType", "gp3")
    price, approximate = ctx.pricing.ebs_gb_month(ctx.region, volume_type)
    age = _age_days(volume.get("CreateTime"))

    reason = f"{size_gb} GB {volume_type} volume in the 'available' state, attached to nothing"
    if age is not None:
        reason += f"; created {age} days ago"

    details: dict[str, Any] = {
        "size_gb": size_gb,
        "volume_type": volume_type,
        "age_days": age,
        "name": _name_of(volume),
        "encrypted": volume.get("Encrypted"),
        "usd_per_gb_month": price,
    }
    # Provisioned-IOPS volumes bill for IOPS and throughput on top of storage,
    # so the real saving is higher than what we report. Say so rather than
    # quietly understating it.
    if volume_type in ("io1", "io2"):
        details["note"] = "storage cost only; provisioned IOPS charges not included"

    return Finding(
        check=CHECK_NAME,
        resource_id=volume_id,
        resource_type="ebs-volume",
        region=ctx.region,
        reason=reason,
        monthly_cost=size_gb * price,
        remediation=(
            f"aws ec2 create-snapshot --volume-id {volume_id} --region {ctx.region} "
            f"--description 'pre-delete backup' && "
            f"aws ec2 delete-volume --volume-id {volume_id} --region {ctx.region}"
        ),
        approximate_cost=approximate,
        details=details,
    )


@check(CHECK_NAME, "Unattached EBS volumes")
def unattached_ebs(ctx: ScanContext) -> Iterator[Finding]:
    paginator = ctx.client("ec2").get_paginator("describe_volumes")
    pages = paginator.paginate(Filters=[{"Name": "status", "Values": ["available"]}])
    for page in pages:
        for volume in page.get("Volumes", []):
            yield build_finding(ctx, volume)
