"""Lightsail snapshots whose source instance or disk is gone.

Deleting a Lightsail instance or disk does not delete its snapshots. They
outlive the thing they were taken from, billed per GB for as long as they
exist, and nothing in the console connects them back to a resource that no
longer appears in any list.

Read this one before acting. An orphaned snapshot is sometimes the *point*:
after deleting an instance, its final snapshot may be the only copy of that
machine left. This check finds snapshots with no live source; whether that
makes them garbage or the last backup is a judgement it cannot make.

Automatic snapshots are skipped -- they are managed by the add-on that created
them and expire on their own schedule.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "lightsail-orphaned-snapshot"


def _names(client: Any, operation: str, key: str) -> set[str]:
    found: set[str] = set()
    for page in client.get_paginator(operation).paginate():
        for item in page.get(key, []):
            if item.get("name"):
                found.add(item["name"])
    return found


def build_finding(
    ctx: ScanContext, snapshot: dict[str, Any], kind: str, source: str | None
) -> Finding:
    name = snapshot["name"]
    size_gb = int(snapshot.get("sizeInGb") or 0)
    price, approximate = ctx.pricing.rate("lightsail.snapshot_gb_month", region=ctx.region)
    delete = "delete-instance-snapshot" if kind == "instance" else "delete-disk-snapshot"
    flag = "--instance-snapshot-name" if kind == "instance" else "--disk-snapshot-name"

    return Finding(
        check=CHECK_NAME,
        resource_id=name,
        resource_type=f"lightsail-{kind}-snapshot",
        region=ctx.region,
        reason=(
            f"{size_gb} GB {kind} snapshot; its source {kind} "
            f"{source or '(unnamed)'} no longer exists"
        ),
        monthly_cost=size_gb * price,
        remediation=(f"aws lightsail {delete} {flag} {shlex.quote(name)} --region {ctx.region}"),
        approximate_cost=approximate,
        details={
            "size_gb": size_gb,
            "source_kind": kind,
            "source_name": source,
            "created_at": str(snapshot.get("createdAt") or ""),
            "note": (
                "the source is gone, so this snapshot may be the only remaining copy "
                "of it -- confirm it is not the backup before deleting"
            ),
        },
    )


@check(CHECK_NAME, "Lightsail snapshots whose source is gone")
def lightsail_orphaned_snapshot(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("lightsail")
    live_instances = _names(client, "get_instances", "instances")
    live_disks = _names(client, "get_disks", "disks")

    for page in client.get_paginator("get_instance_snapshots").paginate():
        for snapshot in page.get("instanceSnapshots", []):
            if snapshot.get("isFromAutoSnapshot") or not snapshot.get("name"):
                continue
            source = snapshot.get("fromInstanceName")
            if source and source in live_instances:
                continue
            yield build_finding(ctx, snapshot, "instance", source)

    for page in client.get_paginator("get_disk_snapshots").paginate():
        for snapshot in page.get("diskSnapshots", []):
            if snapshot.get("isFromAutoSnapshot") or not snapshot.get("name"):
                continue
            # A disk snapshot taken from a whole instance records the instance
            # it came from and no disk name; judge it against whichever it has.
            source = snapshot.get("fromDiskName")
            if source:
                if source in live_disks:
                    continue
            else:
                source = snapshot.get("fromInstanceName")
                if source and source in live_instances:
                    continue
            yield build_finding(ctx, snapshot, "disk", source)
