"""NAT gateways in VPCs with nothing running behind them.

A NAT gateway bills hourly whether or not a single packet crosses it, and it
is one of the most expensive things you can forget: over $30/month in
us-east-1 and more than double that in some regions.

Idle is judged by what is actually plugged into the VPC -- see
``_shared.vpcs_with_workloads``. A VPC whose only in-use interfaces belong to
the VPC's own plumbing (NAT gateways, VPC endpoints) has nothing behind the
gateway.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from zombiescan.helpers import name_tag, vpcs_with_workloads
from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "idle-nat-gateway"


def build_finding(ctx: ScanContext, gateway: dict[str, Any]) -> Finding:
    gateway_id = gateway["NatGatewayId"]
    price, approximate = ctx.pricing.nat_gateway_month(ctx.region)
    return Finding(
        check=CHECK_NAME,
        resource_id=gateway_id,
        resource_type="nat-gateway",
        region=ctx.region,
        reason=(
            f"NAT gateway in {gateway.get('VpcId', 'unknown VPC')} with no workload "
            f"network interfaces behind it"
        ),
        monthly_cost=price,
        remediation=(
            f"aws ec2 delete-nat-gateway --nat-gateway-id {gateway_id} --region {ctx.region}"
        ),
        approximate_cost=approximate,
        details={
            "name": name_tag(gateway),
            "vpc_id": gateway.get("VpcId"),
            "subnet_id": gateway.get("SubnetId"),
            "connectivity_type": gateway.get("ConnectivityType", "public"),
            "note": (
                "uptime charge only; data processing not included. Idle is inferred "
                "from network interfaces, so a VPC used only in bursts may appear idle"
            ),
        },
    )


@check(CHECK_NAME, "Idle NAT gateways")
def idle_nat_gateway(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("ec2")
    pages = client.get_paginator("describe_nat_gateways").paginate(
        Filters=[{"Name": "state", "Values": ["available"]}]
    )
    gateways: list[dict[str, Any]] = []
    for page in pages:
        gateways.extend(page.get("NatGateways", []))

    if not gateways:
        return

    busy = vpcs_with_workloads(client)
    for gateway in gateways:
        if gateway.get("VpcId") in busy:
            continue
        yield build_finding(ctx, gateway)
