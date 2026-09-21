"""Network interfaces left in the 'available' state.

These cost nothing directly, which is exactly why they pile up. They matter
because they hold references: an available ENI will block deletion of its
subnet and its security groups, and they are usually the debris of deleted
Lambda functions, load balancers, and instances.

One describe call with a server-side filter, so it is built declaratively.
See ``zombiescan.building`` for when that is and is not the right shape.
"""

from __future__ import annotations

from typing import Any

from zombiescan.building import simple_check
from zombiescan.models import ScanContext

CHECK_NAME = "available-eni"

NOTE = "no direct cost; blocks subnet and security group deletion"


def _reason(eni: dict[str, Any], ctx: ScanContext) -> str:
    reason = "Network interface is unattached ('available')"
    description = (eni.get("Description") or "").strip()
    return f"{reason}; left by: {description}" if description else reason


def _details(eni: dict[str, Any]) -> dict[str, Any]:
    return {
        "description": (eni.get("Description") or "").strip() or None,
        "vpc_id": eni.get("VpcId"),
        "subnet_id": eni.get("SubnetId"),
        "interface_type": eni.get("InterfaceType"),
        "private_ip": eni.get("PrivateIpAddress"),
        "note": NOTE,
    }


available_eni = simple_check(
    CHECK_NAME,
    "Unattached network interfaces",
    service="ec2",
    operation="describe_network_interfaces",
    result_key="NetworkInterfaces",
    id_key="NetworkInterfaceId",
    resource_type="network-interface",
    # Filtered server-side: the account may hold thousands of in-use
    # interfaces and none of them need to cross the wire.
    params={"Filters": [{"Name": "status", "Values": ["available"]}]},
    reason=_reason,
    # Genuinely free. Reported for the subnet and security-group deletions it
    # silently blocks, not for the money.
    remediation=("aws ec2 delete-network-interface --network-interface-id {id} --region {region}"),
    details=_details,
)

build_finding = available_eni.build_finding
