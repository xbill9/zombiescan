"""Network interfaces left in the 'available' state.

These cost nothing directly, which is exactly why they pile up. They matter
because they hold references: an available ENI will block deletion of its
subnet and its security groups, and they are usually the debris of deleted
Lambda functions, load balancers, and instances.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "available-eni"


def build_finding(ctx: ScanContext, eni: dict[str, Any]) -> Finding:
    eni_id = eni["NetworkInterfaceId"]
    description = (eni.get("Description") or "").strip()
    reason = "Network interface is unattached ('available')"
    if description:
        reason += f"; left by: {description}"
    return Finding(
        check=CHECK_NAME,
        resource_id=eni_id,
        resource_type="network-interface",
        region=ctx.region,
        reason=reason,
        # Genuinely free. Reported for the subnet and security-group deletions
        # it silently blocks, not for the money.
        monthly_cost=0.0,
        remediation=(
            f"aws ec2 delete-network-interface --network-interface-id {eni_id} "
            f"--region {ctx.region}"
        ),
        details={
            "description": description or None,
            "vpc_id": eni.get("VpcId"),
            "subnet_id": eni.get("SubnetId"),
            "interface_type": eni.get("InterfaceType"),
            "private_ip": eni.get("PrivateIpAddress"),
            "note": "no direct cost; blocks subnet and security group deletion",
        },
    )


@check(CHECK_NAME, "Unattached network interfaces")
def available_eni(ctx: ScanContext) -> Iterator[Finding]:
    paginator = ctx.client("ec2").get_paginator("describe_network_interfaces")
    pages = paginator.paginate(Filters=[{"Name": "status", "Values": ["available"]}])
    for page in pages:
        for eni in page.get("NetworkInterfaces", []):
            yield build_finding(ctx, eni)
