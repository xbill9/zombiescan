"""AMIs no running or stopped instance was launched from.

An AMI is free; the EBS snapshots behind it are not. Deregistering an image
without deleting its snapshots is the usual mistake, so the snapshots outlive
everything and keep billing.

This check is the least certain in the catalog and is deliberately cautious:

- Only images older than ``MIN_AGE_DAYS`` are considered, because a fresh AMI
  is probably mid-rollout.
- It can only see *instances*. An AMI referenced by a launch template, an Auto
  Scaling group, or shared with another account looks unused here and is not.

Read the finding before acting on it. Deleting an AMI an ASG depends on breaks
the next scale-out, and the failure shows up hours later.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "unused-ami"
MIN_AGE_DAYS = 90


def _age_days(created: str | None) -> int | None:
    if not created:
        return None
    try:
        moment = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.UTC)
    return (dt.datetime.now(dt.UTC) - moment).days


def _image_ids_in_use(client: Any) -> set[str]:
    """Image ids referenced by any instance, whatever its state."""
    in_use: set[str] = set()
    for page in client.get_paginator("describe_instances").paginate():
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                image_id = instance.get("ImageId")
                if image_id:
                    in_use.add(image_id)
    return in_use


def _backing_snapshots(image: dict[str, Any]) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for mapping in image.get("BlockDeviceMappings", []):
        ebs = mapping.get("Ebs") or {}
        snapshot_id = ebs.get("SnapshotId")
        if snapshot_id:
            out.append((snapshot_id, int(ebs.get("VolumeSize", 0))))
    return out


def build_finding(ctx: ScanContext, image: dict[str, Any], age: int | None) -> Finding:
    image_id = image["ImageId"]
    snapshots = _backing_snapshots(image)
    price, approximate = ctx.pricing.snapshot_gb_month(ctx.region)
    total_gb = sum(size for _, size in snapshots)

    reason = f"AMI not used by any instance, backed by {len(snapshots)} snapshot(s) ({total_gb} GB)"
    if age is not None:
        reason += f"; {age} days old"

    deregister = f"aws ec2 deregister-image --image-id {image_id} --region {ctx.region}"
    deletes = " && ".join(
        f"aws ec2 delete-snapshot --snapshot-id {snap} --region {ctx.region}"
        for snap, _ in snapshots
    )

    return Finding(
        check=CHECK_NAME,
        resource_id=image_id,
        resource_type="ami",
        region=ctx.region,
        reason=reason,
        monthly_cost=total_gb * price,
        remediation=f"{deregister}{' && ' + deletes if deletes else ''}",
        # Snapshot billing is incremental, so this is an upper bound -- and the
        # "unused" judgement itself cannot see launch templates or ASGs.
        approximate_cost=True,
        details={
            "name": image.get("Name"),
            "age_days": age,
            "snapshot_ids": [snap for snap, _ in snapshots],
            "total_gb": total_gb,
            "note": (
                "instances only: an AMI referenced by a launch template, an Auto "
                "Scaling group, or shared with another account will appear unused here"
            ),
            "approximate_reason": "incremental-billing" if not approximate else "region-fallback",
        },
    )


@check(CHECK_NAME, "AMIs no instance uses")
def unused_ami(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("ec2")
    in_use = _image_ids_in_use(client)

    pages = client.get_paginator("describe_images").paginate(Owners=["self"])
    for page in pages:
        for image in page.get("Images", []):
            if image.get("ImageId") in in_use:
                continue
            age = _age_days(image.get("CreationDate"))
            # Too new to judge: a fresh image is probably mid-rollout.
            if age is None or age < MIN_AGE_DAYS:
                continue
            if not _backing_snapshots(image):
                continue
            yield build_finding(ctx, image, age)
