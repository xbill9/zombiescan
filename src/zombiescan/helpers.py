"""Helpers shared by more than one check."""

from __future__ import annotations

from typing import Any

# Interfaces that exist only to serve the VPC's own plumbing. A VPC holding
# nothing but these has no workload in it: the plumbing is running for the
# benefit of nothing, which is the whole point of several checks here.
PLUMBING_INTERFACE_TYPES = frozenset({"nat_gateway", "vpc_endpoint"})


def name_tag(resource: dict[str, Any]) -> str | None:
    """The value of the ``Name`` tag, if the resource has one."""
    for tag in resource.get("Tags") or []:
        if tag.get("Key") == "Name":
            return tag.get("Value")
    return None


def interfaces_by_vpc(
    client: Any, status: str | None = "in-use"
) -> dict[str, list[dict[str, Any]]]:
    """Network interfaces grouped by VPC id.

    ``status`` filters server-side; pass None to fetch every interface
    regardless of state.
    """
    kwargs: dict[str, Any] = {}
    if status:
        kwargs["Filters"] = [{"Name": "status", "Values": [status]}]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for page in client.get_paginator("describe_network_interfaces").paginate(**kwargs):
        for eni in page.get("NetworkInterfaces", []):
            vpc_id = eni.get("VpcId")
            if vpc_id:
                grouped.setdefault(vpc_id, []).append(eni)
    return grouped


def vpcs_with_workloads(client: Any) -> set[str]:
    """VPC ids holding at least one in-use interface that is not plumbing.

    Every workload that can sit behind a NAT gateway or use a VPC endpoint --
    EC2, Lambda in a VPC, ECS, RDS -- holds an in-use interface, so this is a
    reliable proxy for "something is actually running in here".
    """
    return {
        vpc_id
        for vpc_id, interfaces in interfaces_by_vpc(client, status="in-use").items()
        if any(eni.get("InterfaceType") not in PLUMBING_INTERFACE_TYPES for eni in interfaces)
    }
