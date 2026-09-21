"""Load balancers with nothing registered behind them.

A load balancer bills by the hour from the moment it exists. One left behind
by a torn-down service keeps charging to balance traffic across zero targets.

Deliberately conservative: only load balancers with *no registered targets at
all* are reported. A balancer whose targets are registered but unhealthy is an
outage, not waste, and calling it waste would be actively misleading.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "idle-load-balancer"


def _registered_target_count(client: Any, load_balancer_arn: str) -> tuple[int, int]:
    """Return (target_group_count, registered_target_count) for one balancer."""
    groups = 0
    targets = 0
    pages = client.get_paginator("describe_target_groups").paginate(
        LoadBalancerArn=load_balancer_arn
    )
    for page in pages:
        for group in page.get("TargetGroups", []):
            groups += 1
            health = client.describe_target_health(TargetGroupArn=group["TargetGroupArn"])
            targets += len(health.get("TargetHealthDescriptions", []))
    return groups, targets


def build_finding(ctx: ScanContext, balancer: dict[str, Any], group_count: int) -> Finding:
    arn = balancer["LoadBalancerArn"]
    name = balancer.get("LoadBalancerName", arn.rsplit("/", 2)[-2] if "/" in arn else arn)
    kind = balancer.get("Type", "application")
    price, approximate = ctx.pricing.load_balancer_month(
        ctx.region, "nlb" if kind == "network" else "alb"
    )

    if group_count == 0:
        detail = "no target groups attached"
    else:
        detail = f"{group_count} target group(s), none with a registered target"

    return Finding(
        check=CHECK_NAME,
        resource_id=name,
        resource_type=f"{kind}-load-balancer",
        region=ctx.region,
        reason=f"{kind.title()} load balancer with {detail}",
        monthly_cost=price,
        remediation=(
            f"aws elbv2 delete-load-balancer --load-balancer-arn {arn} --region {ctx.region}"
        ),
        approximate_cost=approximate,
        details={
            "arn": arn,
            "type": kind,
            "scheme": balancer.get("Scheme"),
            "vpc_id": balancer.get("VpcId"),
            "target_group_count": group_count,
            "dns_name": balancer.get("DNSName"),
            "note": (
                "uptime charge only, LCU charges excluded. Classic (ELBv1) load "
                "balancers are not covered by this check"
            ),
        },
    )


@check(CHECK_NAME, "Load balancers with no registered targets")
def idle_load_balancer(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("elbv2")
    pages = client.get_paginator("describe_load_balancers").paginate()
    for page in pages:
        for balancer in page.get("LoadBalancers", []):
            if (balancer.get("State") or {}).get("Code") != "active":
                continue
            groups, targets = _registered_target_count(client, balancer["LoadBalancerArn"])
            if targets:
                continue
            yield build_finding(ctx, balancer, groups)
