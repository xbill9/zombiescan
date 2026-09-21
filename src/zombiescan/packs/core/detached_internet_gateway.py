"""Internet gateways attached to no VPC.

A detached internet gateway is free. It is worth reporting because it is
leftover scaffolding from a deleted VPC, it counts against the per-region
internet gateway quota, and hitting that quota produces an error that does not
mention any of this.

One describe call and one predicate, so it is built declaratively. See
``zombiescan.building`` for when that is and is not the right shape.
"""

from __future__ import annotations

from typing import Any

from zombiescan.building import simple_check
from zombiescan.helpers import name_tag
from zombiescan.models import ScanContext

CHECK_NAME = "detached-internet-gateway"

NOTE = "no direct cost; counts against the per-region internet gateway quota"


def _reason(gateway: dict[str, Any], ctx: ScanContext) -> str:
    label = name_tag(gateway) or gateway["InternetGatewayId"]
    return f"Internet gateway {label} is attached to no VPC"


detached_internet_gateway = simple_check(
    CHECK_NAME,
    "Internet gateways attached to no VPC",
    service="ec2",
    operation="describe_internet_gateways",
    result_key="InternetGateways",
    id_key="InternetGatewayId",
    resource_type="internet-gateway",
    where=lambda gateway: not gateway.get("Attachments"),
    reason=_reason,
    remediation=("aws ec2 delete-internet-gateway --internet-gateway-id {id} --region {region}"),
    details=lambda gateway: {"name": name_tag(gateway), "note": NOTE},
)

build_finding = detached_internet_gateway.build_finding
