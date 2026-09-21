"""Manual RDS snapshots whose database no longer exists.

Deleting an RDS instance deletes its automated backups and keeps every manual
snapshot -- deliberately, because that is what manual snapshots are for. The
consequence is that a database deleted two years ago is often still billing
for storage, and nothing in the RDS console draws attention to it.
"""

from __future__ import annotations

import datetime as dt
import shlex
from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "orphaned-rds-snapshot"


def _age_days(moment: dt.datetime | None) -> int | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.UTC)
    return (dt.datetime.now(dt.UTC) - moment).days


def build_finding(ctx: ScanContext, snapshot: dict[str, Any]) -> Finding:
    identifier = snapshot["DBSnapshotIdentifier"]
    allocated_gb = int(snapshot.get("AllocatedStorage", 0))
    price, approximate = ctx.pricing.rds_snapshot_gb_month(ctx.region)
    source = snapshot.get("DBInstanceIdentifier", "unknown")
    age = _age_days(snapshot.get("SnapshotCreateTime"))

    reason = f"Manual snapshot of deleted database {source}, holding {allocated_gb} GB"
    if age is not None:
        reason += f"; {age} days old"

    return Finding(
        check=CHECK_NAME,
        resource_id=identifier,
        resource_type="rds-snapshot",
        region=ctx.region,
        reason=reason,
        monthly_cost=allocated_gb * price,
        remediation=(
            f"aws rds delete-db-snapshot --db-snapshot-identifier {shlex.quote(identifier)} "
            f"--region {ctx.region}"
        ),
        # Snapshot storage bills for what is actually stored, not the volume's
        # allocated size, so this is an upper bound.
        approximate_cost=True,
        details={
            "source_db": source,
            "allocated_gb": allocated_gb,
            "age_days": age,
            "engine": snapshot.get("Engine"),
            "note": (
                "upper bound: billed on stored size, not allocated. Aurora cluster "
                "snapshots are a separate API and are not covered by this check"
            ),
            "approximate_reason": "allocated-not-stored" if not approximate else "region-fallback",
        },
    )


@check(CHECK_NAME, "Manual RDS snapshots of deleted databases")
def orphaned_rds_snapshot(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("rds")

    live: set[str] = set()
    for page in client.get_paginator("describe_db_instances").paginate():
        for instance in page.get("DBInstances", []):
            live.add(instance["DBInstanceIdentifier"])

    pages = client.get_paginator("describe_db_snapshots").paginate(SnapshotType="manual")
    for page in pages:
        for snapshot in page.get("DBSnapshots", []):
            if snapshot.get("Status") != "available":
                continue
            source = snapshot.get("DBInstanceIdentifier")
            # A snapshot whose source still exists is a backup, not debris.
            if not source or source in live:
                continue
            yield build_finding(ctx, snapshot)
