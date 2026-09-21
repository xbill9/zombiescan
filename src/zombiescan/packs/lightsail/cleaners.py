"""Cleaners for the Lightsail pack's checks.

Cleaners plan; they never execute. See ``zombiescan.cleaners`` for the split
between planning and running, and ``zombiescan.clean`` for the runner.
"""

from __future__ import annotations

from collections.abc import Iterator

from zombiescan.cleaners import Step, _stamp, cleaner
from zombiescan.models import Finding, ScanContext

# --- lightsail -----------------------------------------------------------


@cleaner("lightsail-stopped-instance")
def clean_lightsail_stopped_instance(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    instance = finding.resource_id
    snapshot = f"{instance}-zombiescan-{_stamp()}"
    # Deleting a Lightsail instance takes its system disk with it, so the
    # snapshot is the only way back and has to land first.
    yield Step(
        f"snapshot {instance} before deleting it",
        "lightsail",
        "create_instance_snapshot",
        {"instanceName": instance, "instanceSnapshotName": snapshot},
    )
    yield Step(
        f"delete instance {instance}",
        "lightsail",
        "delete_instance",
        {"instanceName": instance},
    )


@cleaner("lightsail-unattached-static-ip")
def clean_lightsail_static_ip(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    yield Step(
        f"release static IP {finding.resource_id}",
        "lightsail",
        "release_static_ip",
        {"staticIpName": finding.resource_id},
        # The address goes back to the pool and cannot be reclaimed.
        irreversible=True,
    )


@cleaner("lightsail-unattached-disk")
def clean_lightsail_unattached_disk(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    disk = finding.resource_id
    snapshot = f"{disk}-zombiescan-{_stamp()}"
    yield Step(
        f"snapshot {disk} before deleting it",
        "lightsail",
        "create_disk_snapshot",
        {"diskName": disk, "diskSnapshotName": snapshot},
    )
    yield Step(f"delete disk {disk}", "lightsail", "delete_disk", {"diskName": disk})


@cleaner("lightsail-idle-container-service")
def clean_lightsail_container_service(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    # Disabling stops the billing and keeps the service, its name and its URL.
    # Deleting would free the name for anyone else and is not needed to stop
    # the charge, so the cleaner does the reversible thing.
    yield Step(
        f"disable container service {finding.resource_id} (stops billing, keeps the URL)",
        "lightsail",
        "update_container_service",
        {"serviceName": finding.resource_id, "isDisabled": True},
    )


@cleaner("lightsail-empty-load-balancer")
def clean_lightsail_load_balancer(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    yield Step(
        f"delete load balancer {finding.resource_id} and its certificates",
        "lightsail",
        "delete_load_balancer",
        {"loadBalancerName": finding.resource_id},
        irreversible=True,
    )


@cleaner("lightsail-orphaned-snapshot")
def clean_lightsail_orphaned_snapshot(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    name = finding.resource_id
    if finding.details.get("source_kind") == "instance":
        operation, key = "delete_instance_snapshot", "instanceSnapshotName"
    else:
        operation, key = "delete_disk_snapshot", "diskSnapshotName"
    yield Step(
        f"delete snapshot {name}",
        "lightsail",
        operation,
        {key: name},
        # Its source is already gone, so this may be the last copy.
        irreversible=True,
    )
