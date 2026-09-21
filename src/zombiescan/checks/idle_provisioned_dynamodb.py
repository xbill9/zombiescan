"""Empty DynamoDB tables still paying for provisioned capacity.

A table on PROVISIONED billing pays for its read and write capacity every
hour, whether or not it holds a single item. Tables created for a prototype,
or left behind by a deleted stack, keep billing at whatever capacity someone
set during testing.

On-demand tables are never reported: they cost nothing when nothing reads or
writes them, so an empty one is not waste.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "idle-provisioned-dynamodb"


def build_finding(ctx: ScanContext, table: dict[str, Any]) -> Finding:
    name = table["TableName"]
    throughput = table.get("ProvisionedThroughput") or {}
    read_units = int(throughput.get("ReadCapacityUnits", 0))
    write_units = int(throughput.get("WriteCapacityUnits", 0))
    price, approximate = ctx.pricing.dynamodb_capacity_month(ctx.region, read_units, write_units)

    return Finding(
        check=CHECK_NAME,
        resource_id=name,
        resource_type="dynamodb-table",
        region=ctx.region,
        reason=(
            f"Empty table on provisioned billing, paying for {read_units} read and "
            f"{write_units} write capacity units"
        ),
        monthly_cost=price,
        remediation=(
            f"aws dynamodb delete-table --table-name {shlex.quote(name)} "
            f"--region {ctx.region}  # or switch to on-demand: update-table "
            f"--billing-mode PAY_PER_REQUEST"
        ),
        approximate_cost=approximate,
        details={
            "read_capacity_units": read_units,
            "write_capacity_units": write_units,
            "item_count": table.get("ItemCount", 0),
            "size_bytes": table.get("TableSizeBytes", 0),
            "note": (
                "ItemCount is refreshed roughly every six hours, so a table filled "
                "in the last few hours can still read as empty. Free-tier "
                "allowances are not deducted from this figure"
            ),
        },
    )


@check(CHECK_NAME, "Empty DynamoDB tables on provisioned billing")
def idle_provisioned_dynamodb(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("dynamodb")
    for page in client.get_paginator("list_tables").paginate():
        for name in page.get("TableNames", []):
            table = client.describe_table(TableName=name).get("Table", {})
            if table.get("TableStatus") != "ACTIVE":
                continue
            # BillingModeSummary is absent on tables that have never been
            # switched, and those are provisioned by default.
            billing = (table.get("BillingModeSummary") or {}).get("BillingMode", "PROVISIONED")
            if billing != "PROVISIONED":
                continue
            if table.get("ItemCount", 0):
                continue
            throughput = table.get("ProvisionedThroughput") or {}
            if not (
                throughput.get("ReadCapacityUnits", 0) or throughput.get("WriteCapacityUnits", 0)
            ):
                continue
            yield build_finding(ctx, table)
