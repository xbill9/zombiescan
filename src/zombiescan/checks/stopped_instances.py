"""Stopped EC2 instances that are still paying for their disks.

Stopping an instance stops the compute charge and nothing else. Every EBS
volume attached to it keeps billing at the full provisioned rate. An instance
stopped "temporarily" eighteen months ago is a standing bill for storage
nobody reads.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "stopped-instance"
_CHUNK = 100


def _name_of(resource: dict[str, Any]) -> str | None:
    for tag in resource.get("Tags") or []:
        if tag.get("Key") == "Name":
            return tag.get("Value")
    return None


def _stopped_since(instance: dict[str, Any]) -> int | None:
    """Days since the state transition, parsed from the human-readable reason.

    EC2 reports this as e.g. "User initiated (2025-03-04 11:22:33 GMT)". There
    is no structured field for it, so a missing or unparseable value yields
    None rather than a fabricated age.
    """
    reason = instance.get("StateTransitionReason") or ""
    start, _, rest = reason.partition("(")
    stamp = rest.partition(")")[0].replace(" GMT", "").strip()
    if not stamp:
        return None
    try:
        moment = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.UTC)
    except ValueError:
        return None
    return (dt.datetime.now(dt.UTC) - moment).days


def _volumes_by_instance(client: Any, instance_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for start in range(0, len(instance_ids), _CHUNK):
        chunk = instance_ids[start : start + _CHUNK]
        pages = client.get_paginator("describe_volumes").paginate(
            Filters=[{"Name": "attachment.instance-id", "Values": chunk}]
        )
        for page in pages:
            for volume in page.get("Volumes", []):
                for attachment in volume.get("Attachments", []):
                    owner = attachment.get("InstanceId")
                    if owner:
                        grouped.setdefault(owner, []).append(volume)
    return grouped


def build_finding(
    ctx: ScanContext, instance: dict[str, Any], volumes: list[dict[str, Any]]
) -> Finding:
    instance_id = instance["InstanceId"]
    total = 0.0
    approximate = False
    total_gb = 0
    for volume in volumes:
        price, approx = ctx.pricing.ebs_gb_month(ctx.region, volume.get("VolumeType", "gp3"))
        size = int(volume.get("Size", 0))
        total += size * price
        total_gb += size
        approximate = approximate or approx

    days = _stopped_since(instance)
    disk_count = len(volumes)
    reason = (
        f"Stopped instance still paying for {disk_count} attached "
        f"volume{'' if disk_count == 1 else 's'} ({total_gb} GB)"
    )
    if days is not None:
        reason += f"; stopped {days} days ago"

    return Finding(
        check=CHECK_NAME,
        resource_id=instance_id,
        resource_type="ec2-instance",
        region=ctx.region,
        reason=reason,
        monthly_cost=total,
        remediation=(
            f"aws ec2 create-image --instance-id {instance_id} "
            f"--name 'pre-terminate-{instance_id}' --region {ctx.region} && "
            f"aws ec2 terminate-instances --instance-ids {instance_id} --region {ctx.region}"
        ),
        approximate_cost=approximate,
        details={
            "name": _name_of(instance),
            "instance_type": instance.get("InstanceType"),
            "stopped_days": days,
            "volume_count": disk_count,
            "total_gb": total_gb,
            "volume_ids": [v["VolumeId"] for v in volumes],
            "note": "compute is not billed while stopped; the attached storage is",
        },
    )


@check(CHECK_NAME, "Stopped instances still billing for storage")
def stopped_instances(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("ec2")
    pages = client.get_paginator("describe_instances").paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["stopped"]}]
    )
    instances: list[dict[str, Any]] = []
    for page in pages:
        for reservation in page.get("Reservations", []):
            instances.extend(reservation.get("Instances", []))

    if not instances:
        return

    volumes = _volumes_by_instance(client, [i["InstanceId"] for i in instances])
    for instance in instances:
        attached = volumes.get(instance["InstanceId"], [])
        # An instance-store-only instance costs nothing while stopped.
        if not attached:
            continue
        yield build_finding(ctx, instance, attached)
