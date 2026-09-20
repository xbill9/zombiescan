"""EBS snapshots whose volume is gone and which back no AMI.

A snapshot outlives the volume it was taken from, so deleting a volume quietly
leaves its snapshots behind, billing forever. The two things that make a
snapshot worth keeping are an existing source volume or an AMI that references
it; a snapshot with neither is usually forgotten rather than archived.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "orphaned-snapshot"


def _age_days(created: dt.datetime | None) -> int | None:
    if created is None:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=dt.UTC)
    return (dt.datetime.now(dt.UTC) - created).days


def _existing_volume_ids(client: Any) -> set[str]:
    found: set[str] = set()
    for page in client.get_paginator("describe_volumes").paginate():
        for volume in page.get("Volumes", []):
            found.add(volume["VolumeId"])
    return found


def _snapshots_backing_images(client: Any) -> set[str]:
    """Snapshot ids referenced by any AMI this account owns."""
    referenced: set[str] = set()
    for page in client.get_paginator("describe_images").paginate(Owners=["self"]):
        for image in page.get("Images", []):
            for mapping in image.get("BlockDeviceMappings", []):
                snapshot_id = (mapping.get("Ebs") or {}).get("SnapshotId")
                if snapshot_id:
                    referenced.add(snapshot_id)
    return referenced


def build_finding(ctx: ScanContext, snapshot: dict[str, Any]) -> Finding:
    snapshot_id = snapshot["SnapshotId"]
    size_gb = int(snapshot.get("VolumeSize", 0))
    price, approximate = ctx.pricing.snapshot_gb_month(ctx.region)
    age = _age_days(snapshot.get("StartTime"))
    source_volume = snapshot.get("VolumeId", "unknown")

    reason = f"Snapshot of deleted volume {source_volume}, backing no AMI"
    if age is not None:
        reason += f"; {age} days old"

    return Finding(
        check=CHECK_NAME,
        resource_id=snapshot_id,
        resource_type="ebs-snapshot",
        region=ctx.region,
        reason=reason,
        monthly_cost=size_gb * price,
        remediation=(f"aws ec2 delete-snapshot --snapshot-id {snapshot_id} --region {ctx.region}"),
        # Snapshots bill for changed blocks, not the full volume size, so this
        # figure is an upper bound. Saying so beats quietly overstating it.
        approximate_cost=True,
        details={
            "source_volume_id": source_volume,
            "volume_size_gb": size_gb,
            "age_days": age,
            "description": (snapshot.get("Description") or "").strip() or None,
            "note": (
                "upper bound: snapshots are incremental and bill for changed "
                "blocks, not the full volume size"
            ),
            "approximate_reason": "incremental-billing" if not approximate else "region-fallback",
        },
    )


@check(CHECK_NAME, "Orphaned EBS snapshots")
def orphaned_snapshots(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("ec2")
    live_volumes = _existing_volume_ids(client)
    image_snapshots = _snapshots_backing_images(client)

    pages = client.get_paginator("describe_snapshots").paginate(OwnerIds=["self"])
    for page in pages:
        for snapshot in page.get("Snapshots", []):
            if snapshot.get("SnapshotId") in image_snapshots:
                continue
            source = snapshot.get("VolumeId")
            # A snapshot with no recorded source volume cannot be judged
            # orphaned; skip rather than guess.
            if not source or source in live_volumes:
                continue
            yield build_finding(ctx, snapshot)
