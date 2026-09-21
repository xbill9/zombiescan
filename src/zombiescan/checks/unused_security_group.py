"""Security groups attached to nothing and referenced by nothing.

These cost nothing. They are reported because they accumulate without limit,
they make an account's security posture harder to read, and they block
deletion of the VPC they live in.

A group counts as used if any network interface has it attached, or if another
group's rules reference it. The default group of a VPC is never reported: it
cannot be deleted.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "unused-security-group"


def _attached_group_ids(client: Any) -> set[str]:
    attached: set[str] = set()
    for page in client.get_paginator("describe_network_interfaces").paginate():
        for eni in page.get("NetworkInterfaces", []):
            for group in eni.get("Groups") or []:
                if group.get("GroupId"):
                    attached.add(group["GroupId"])
    return attached


def _referenced_group_ids(groups: list[dict[str, Any]]) -> set[str]:
    """Group ids named in another group's ingress or egress rules."""
    referenced: set[str] = set()
    for group in groups:
        own_id = group.get("GroupId")
        for direction in ("IpPermissions", "IpPermissionsEgress"):
            for rule in group.get(direction) or []:
                for pair in rule.get("UserIdGroupPairs") or []:
                    target = pair.get("GroupId")
                    # A group referencing itself does not make it used.
                    if target and target != own_id:
                        referenced.add(target)
    return referenced


def build_finding(ctx: ScanContext, group: dict[str, Any]) -> Finding:
    group_id = group["GroupId"]
    return Finding(
        check=CHECK_NAME,
        resource_id=group_id,
        resource_type="security-group",
        region=ctx.region,
        reason=(
            f"Security group '{group.get('GroupName', group_id)}' is attached to no "
            f"interface and referenced by no other group"
        ),
        monthly_cost=0.0,
        remediation=f"aws ec2 delete-security-group --group-id {group_id} --region {ctx.region}",
        details={
            "group_name": group.get("GroupName"),
            "description": group.get("Description"),
            "vpc_id": group.get("VpcId"),
            "note": "no direct cost; clutters the security posture and blocks VPC deletion",
        },
    )


@check(CHECK_NAME, "Security groups attached to nothing")
def unused_security_group(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("ec2")
    groups: list[dict[str, Any]] = []
    for page in client.get_paginator("describe_security_groups").paginate():
        groups.extend(page.get("SecurityGroups", []))

    if not groups:
        return

    in_use = _attached_group_ids(client) | _referenced_group_ids(groups)
    for group in groups:
        # The default group cannot be deleted, so reporting it is just noise.
        if group.get("GroupName") == "default":
            continue
        if group.get("GroupId") in in_use:
            continue
        yield build_finding(ctx, group)
