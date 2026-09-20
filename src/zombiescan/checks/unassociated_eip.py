"""Unassociated Elastic IPs.

Since February 2024 AWS bills every public IPv4 address by the hour, attached
or not. An Elastic IP associated with nothing is the purest form of this waste:
it does nothing and costs the same as one doing work.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "unassociated-eip"


def _name_of(address: dict[str, Any]) -> str | None:
    for tag in address.get("Tags") or []:
        if tag.get("Key") == "Name":
            return tag.get("Value")
    return None


def _release_command(address: dict[str, Any], region: str) -> str:
    allocation_id = address.get("AllocationId")
    if allocation_id:
        return f"aws ec2 release-address --allocation-id {allocation_id} --region {region}"
    # EC2-Classic addresses have no allocation id and are released by IP.
    return f"aws ec2 release-address --public-ip {address['PublicIp']} --region {region}"


def build_finding(ctx: ScanContext, address: dict[str, Any]) -> Finding:
    price, approximate = ctx.pricing.public_ipv4_month()
    public_ip = address.get("PublicIp", "unknown")
    return Finding(
        check=CHECK_NAME,
        resource_id=address.get("AllocationId") or public_ip,
        resource_type="elastic-ip",
        region=ctx.region,
        reason=f"Elastic IP {public_ip} is allocated but associated with nothing",
        monthly_cost=price,
        remediation=_release_command(address, ctx.region),
        approximate_cost=approximate,
        details={
            "public_ip": public_ip,
            "name": _name_of(address),
            "domain": address.get("Domain"),
            "network_border_group": address.get("NetworkBorderGroup"),
        },
    )


@check(CHECK_NAME, "Unassociated Elastic IPs")
def unassociated_eip(ctx: ScanContext) -> Iterator[Finding]:
    # describe_addresses has no paginator: it returns every address at once.
    addresses = ctx.client("ec2").describe_addresses().get("Addresses", [])
    for address in addresses:
        if address.get("AssociationId"):
            continue
        # Belt and braces: an address mid-association may lack AssociationId
        # while already pointing at something.
        if address.get("InstanceId") or address.get("NetworkInterfaceId"):
            continue
        yield build_finding(ctx, address)
