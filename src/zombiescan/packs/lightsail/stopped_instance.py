"""Lightsail instances that are stopped but still billing.

This is the trap that catches people moving from EC2. Stopping an EC2
instance stops the compute charge and leaves only its disks. Stopping a
Lightsail instance stops nothing: the bundle is a flat monthly price covering
compute, storage and transfer together, and it is billed in full for every
hour the instance exists, running or not.

So a Lightsail instance stopped "to save money" saves nothing at all. The only
way to stop paying is to delete it -- which is why this check suggests taking
a snapshot first.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "lightsail-stopped-instance"


def build_finding(ctx: ScanContext, instance: dict[str, Any]) -> Finding:
    name = instance["name"]
    bundle_id = instance.get("bundleId") or ""
    price, approximate = ctx.pricing.rate(
        "lightsail.bundle_month", region=ctx.region, variant=bundle_id
    )
    quoted = shlex.quote(name)

    return Finding(
        check=CHECK_NAME,
        resource_id=name,
        resource_type="lightsail-instance",
        region=ctx.region,
        reason=(
            f"Lightsail instance is stopped but still billing its full "
            f"{bundle_id or 'unknown'} bundle -- stopping does not pause the charge"
        ),
        monthly_cost=price,
        # Snapshot first: deleting a Lightsail instance takes its disk with it,
        # and a snapshot is the only way back.
        remediation=(
            f"aws lightsail create-instance-snapshot --instance-name {quoted} "
            f"--instance-snapshot-name {shlex.quote(name + '-final')} --region {ctx.region} "
            f"&& aws lightsail delete-instance --instance-name {quoted} --region {ctx.region}"
        ),
        approximate_cost=approximate,
        details={
            "bundle_id": bundle_id,
            "blueprint": instance.get("blueprintName") or instance.get("blueprintId"),
            "state": (instance.get("state") or {}).get("name"),
            "created_at": str(instance.get("createdAt") or ""),
            "note": (
                "unlike EC2, a stopped Lightsail instance bills the whole bundle; "
                "only deleting it stops the charge"
            ),
            "approximate_reason": "unknown-bundle" if not price else None,
        },
    )


@check(CHECK_NAME, "Lightsail instances stopped but still billing")
def lightsail_stopped_instance(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("lightsail")
    for page in client.get_paginator("get_instances").paginate():
        for instance in page.get("instances", []):
            # "pending", "stopping" and "starting" are mid-transition, not a
            # resting state, and reporting them would flap between scans.
            if (instance.get("state") or {}).get("name") != "stopped":
                continue
            if not instance.get("name"):
                continue
            yield build_finding(ctx, instance)
