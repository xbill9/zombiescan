"""CloudWatch log groups that never expire.

A log group created without a retention policy keeps every byte forever. It is
the quietest line on an AWS bill: nothing breaks, nothing alerts, the number
just grows. Groups created by the console and by most IaC default to "never
expire" unless you say otherwise.

Empty groups are skipped. A group with no retention and no data costs nothing
today, and reporting it would bury the ones that do.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "log-group-no-retention"
BYTES_PER_GB = 1024**3
SUGGESTED_RETENTION_DAYS = 30


def build_finding(ctx: ScanContext, group: dict[str, Any]) -> Finding:
    name = group["logGroupName"]
    stored_gb = group.get("storedBytes", 0) / BYTES_PER_GB
    price, approximate = ctx.pricing.log_storage_gb_month(ctx.region)

    return Finding(
        check=CHECK_NAME,
        resource_id=name,
        resource_type="log-group",
        region=ctx.region,
        reason=(
            f"Log group has no retention policy and holds {stored_gb:,.2f} GB "
            f"that will never expire"
        ),
        monthly_cost=stored_gb * price,
        remediation=(
            f"aws logs put-retention-policy --log-group-name '{name}' "
            f"--retention-in-days {SUGGESTED_RETENTION_DAYS} --region {ctx.region}"
        ),
        # The reported figure is what the stored data costs now. Setting a
        # retention policy reclaims only the portion older than the window, so
        # treat this as the exposure, not the guaranteed saving.
        approximate_cost=True,
        details={
            "stored_gb": round(stored_gb, 3),
            "usd_per_gb_month": price,
            "suggested_retention_days": SUGGESTED_RETENTION_DAYS,
            "note": (
                "cost shown is current storage, not guaranteed saving: a retention "
                "policy only deletes data older than the window it sets"
            ),
            "approximate_reason": "retention-window" if not approximate else "region-fallback",
        },
    )


@check(CHECK_NAME, "Log groups with no retention policy")
def log_group_no_retention(ctx: ScanContext) -> Iterator[Finding]:
    pages = ctx.client("logs").get_paginator("describe_log_groups").paginate()
    for page in pages:
        for group in page.get("logGroups", []):
            if group.get("retentionInDays"):
                continue
            if not group.get("storedBytes"):
                continue
            yield build_finding(ctx, group)
