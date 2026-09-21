"""Internet gateways attached to no VPC.

A detached internet gateway is free. It is worth reporting because it is
leftover scaffolding from a deleted VPC, it counts against the per-region
internet gateway quota, and hitting that quota produces an error that does not
mention any of this.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from zombiescan.checks._shared import name_tag
from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "detached-internet-gateway"


def build_finding(ctx: ScanContext, gateway: dict[str, Any]) -> Finding:
    gateway_id = gateway["InternetGatewayId"]
    name = name_tag(gateway)
    return Finding(
        check=CHECK_NAME,
        resource_id=gateway_id,
        resource_type="internet-gateway",
        region=ctx.region,
        reason=f"Internet gateway {name or gateway_id} is attached to no VPC",
        monthly_cost=0.0,
        remediation=(
            f"aws ec2 delete-internet-gateway --internet-gateway-id {gateway_id} "
            f"--region {ctx.region}"
        ),
        details={
            "name": name,
            "note": "no direct cost; counts against the per-region internet gateway quota",
        },
    )


@check(CHECK_NAME, "Internet gateways attached to no VPC")
def detached_internet_gateway(ctx: ScanContext) -> Iterator[Finding]:
    pages = ctx.client("ec2").get_paginator("describe_internet_gateways").paginate()
    for page in pages:
        for gateway in page.get("InternetGateways", []):
            if gateway.get("Attachments"):
                continue
            yield build_finding(ctx, gateway)
