"""NAT gateways in VPCs with nothing running behind them.

A NAT gateway bills hourly whether or not a single packet crosses it, and it
is one of the most expensive things you can forget: over $30/month in
us-east-1 and more than double that in some regions.

Idle is judged by what is actually plugged into the VPC. Every workload that
can use a NAT gateway -- EC2, Lambda in a VPC, ECS, RDS -- holds an in-use
network interface. A VPC whose only in-use interfaces belong to NAT gateways
themselves has nothing behind the gateway.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "idle-nat-gateway"


def _name_of(resource: dict[str, Any]) -> str | None:
    for tag in resource.get("Tags") or []:
        if tag.get("Key") == "Name":
            return tag.get("Value")
    return None


def _vpcs_with_workloads(client: Any) -> set[str]:
    """VPC ids holding at least one in-use interface that is not a NAT gateway."""
    busy: set[str] = set()
    pages = client.get_paginator("describe_network_interfaces").paginate(
        Filters=[{"Name": "status", "Values": ["in-use"]}]
    )
    for page in pages:
        for eni in page.get("NetworkInterfaces", []):
            if eni.get("InterfaceType") == "nat_gateway":
                continue
            vpc_id = eni.get("VpcId")
            if vpc_id:
                busy.add(vpc_id)
    return busy


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
            "name": _name_of(gateway),
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

    busy = _vpcs_with_workloads(client)
    for gateway in gateways:
        if gateway.get("VpcId") in busy:
            continue
        yield build_finding(ctx, gateway)
