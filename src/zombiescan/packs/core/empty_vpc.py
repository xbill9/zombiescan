"""VPCs with no network interfaces in them at all.

An empty VPC is free. It is worth reporting because it is the clearest signal
of a torn-down environment, and because the things it still contains -- route
tables, subnets, internet gateways, peering connections -- are invisible until
someone tries to clean up and hits dependency errors.

Default VPCs are skipped: every region has one and an unused one is normal.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from zombiescan.helpers import interfaces_by_vpc, name_tag
from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "empty-vpc"


def build_finding(ctx: ScanContext, vpc: dict[str, Any]) -> Finding:
    vpc_id = vpc["VpcId"]
    name = name_tag(vpc)
    label = f"'{name}' " if name else ""
    return Finding(
        check=CHECK_NAME,
        resource_id=vpc_id,
        resource_type="vpc",
        region=ctx.region,
        reason=f"VPC {label}holds no network interfaces of any kind",
        monthly_cost=0.0,
        # Deleting a VPC fails until its subnets, gateways and route tables are
        # gone, so a one-line delete would mislead. Point at the inspection
        # instead and let the operator decide.
        remediation=(
            f"aws ec2 describe-subnets --filters Name=vpc-id,Values={vpc_id} "
            f"--region {ctx.region}  # inspect, then delete dependencies before the VPC"
        ),
        details={
            "name": name,
            "cidr_block": vpc.get("CidrBlock"),
            "note": (
                "no direct cost; the VPC itself is free. Subnets, route tables and "
                "gateways inside it must be removed before it can be deleted"
            ),
        },
    )


@check(
    CHECK_NAME,
    "VPCs with nothing in them",
    uncleanable=(
        "a VPC cannot be deleted until its subnets, route tables and gateways "
        "are removed first, in an order this tool does not attempt to work out"
    ),
)
def empty_vpc(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("ec2")
    vpcs: list[dict[str, Any]] = []
    for page in client.get_paginator("describe_vpcs").paginate():
        vpcs.extend(page.get("Vpcs", []))

    if not vpcs:
        return

    # Every interface, not just in-use: an available ENI still means something
    # was here and the VPC is not cleanly empty.
    populated = interfaces_by_vpc(client, status=None)
    for vpc in vpcs:
        if vpc.get("IsDefault"):
            continue
        if populated.get(vpc["VpcId"]):
            continue
        yield build_finding(ctx, vpc)
