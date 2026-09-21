"""Stopped RDS instances still paying for storage.

Stopping an RDS instance stops the instance-hour charge and nothing else: the
allocated storage bills in full. Worse, RDS will not let an instance stay
stopped -- AWS restarts it automatically after seven days, at which point the
compute charge resumes too, usually without anyone noticing.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "stopped-rds-instance"


def build_finding(ctx: ScanContext, instance: dict[str, Any]) -> Finding:
    identifier = instance["DBInstanceIdentifier"]
    allocated_gb = int(instance.get("AllocatedStorage", 0))
    storage_type = instance.get("StorageType", "gp2")
    multi_az = bool(instance.get("MultiAZ"))
    price, approximate = ctx.pricing.rds_storage_gb_month(ctx.region, storage_type, multi_az)

    return Finding(
        check=CHECK_NAME,
        resource_id=identifier,
        resource_type="rds-instance",
        region=ctx.region,
        reason=(
            f"Stopped {instance.get('Engine', 'database')} instance still paying for "
            f"{allocated_gb} GB of {storage_type} storage"
            f"{' (Multi-AZ)' if multi_az else ''}"
        ),
        monthly_cost=allocated_gb * price,
        remediation=(
            f"aws rds delete-db-instance --db-instance-identifier {identifier} "
            f"--final-db-snapshot-identifier {identifier}-final --region {ctx.region}"
        ),
        approximate_cost=approximate,
        details={
            "engine": instance.get("Engine"),
            "engine_version": instance.get("EngineVersion"),
            "instance_class": instance.get("DBInstanceClass"),
            "allocated_gb": allocated_gb,
            "storage_type": storage_type,
            "multi_az": multi_az,
            "note": (
                "AWS restarts a stopped RDS instance automatically after 7 days, "
                "resuming compute charges. Backup storage is not included in this figure"
            ),
        },
    )


@check(CHECK_NAME, "Stopped RDS instances still billing for storage")
def stopped_rds_instance(ctx: ScanContext) -> Iterator[Finding]:
    pages = ctx.client("rds").get_paginator("describe_db_instances").paginate()
    for page in pages:
        for instance in page.get("DBInstances", []):
            if instance.get("DBInstanceStatus") != "stopped":
                continue
            if not instance.get("AllocatedStorage"):
                continue
            yield build_finding(ctx, instance)
