"""Classic load balancers with no instances registered.

ELBv1 predates target groups: instances are registered directly on the
balancer. A Classic balancer costs more per hour than an ALB and there is
rarely a reason to still have one, let alone an empty one.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "empty-classic-lb"


def build_finding(ctx: ScanContext, balancer: dict[str, Any]) -> Finding:
    name = balancer["LoadBalancerName"]
    price, approximate = ctx.pricing.classic_lb_month(ctx.region)
    return Finding(
        check=CHECK_NAME,
        resource_id=name,
        resource_type="classic-load-balancer",
        region=ctx.region,
        reason="Classic load balancer with no instances registered",
        monthly_cost=price,
        remediation=(
            f"aws elb delete-load-balancer --load-balancer-name {name} --region {ctx.region}"
        ),
        approximate_cost=approximate,
        details={
            "vpc_id": balancer.get("VPCId"),
            "scheme": balancer.get("Scheme"),
            "dns_name": balancer.get("DNSName"),
            "note": (
                "Classic load balancers cost more per hour than an ALB; migrating "
                "is worth considering even for the ones still in use"
            ),
        },
    )


@check(CHECK_NAME, "Classic load balancers with no instances")
def classic_load_balancer(ctx: ScanContext) -> Iterator[Finding]:
    pages = ctx.client("elb").get_paginator("describe_load_balancers").paginate()
    for page in pages:
        for balancer in page.get("LoadBalancerDescriptions", []):
            if balancer.get("Instances"):
                continue
            yield build_finding(ctx, balancer)
