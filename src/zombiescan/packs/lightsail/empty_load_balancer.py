"""Lightsail load balancers with no instances attached.

A Lightsail load balancer bills by the hour from creation, at a flat rate that
does not depend on traffic. One left behind after its instances were deleted
balances traffic across nothing, for about eighteen dollars a month.

Deliberately conservative, and for the same reason as the ALB/NLB check: only
balancers with *no attached instances at all* are reported. A balancer whose
instances are attached but unhealthy is an outage, not waste.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "lightsail-empty-load-balancer"


def build_finding(ctx: ScanContext, balancer: dict[str, Any]) -> Finding:
    name = balancer["name"]
    price, approximate = ctx.pricing.rate("lightsail.load_balancer_month", region=ctx.region)

    return Finding(
        check=CHECK_NAME,
        resource_id=name,
        resource_type="lightsail-load-balancer",
        region=ctx.region,
        reason="Lightsail load balancer has no instances attached to it",
        monthly_cost=price,
        remediation=(
            f"aws lightsail delete-load-balancer --load-balancer-name {shlex.quote(name)} "
            f"--region {ctx.region}"
        ),
        approximate_cost=approximate,
        details={
            "dns_name": balancer.get("dnsName"),
            "state": balancer.get("state"),
            "created_at": str(balancer.get("createdAt") or ""),
            "note": (
                "deleting also destroys any TLS certificates attached to this balancer, "
                "which have to be re-validated if it is recreated"
            ),
        },
    )


@check(CHECK_NAME, "Lightsail load balancers with no instances attached")
def lightsail_empty_load_balancer(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("lightsail")
    for page in client.get_paginator("get_load_balancers").paginate():
        for balancer in page.get("loadBalancers", []):
            if balancer.get("instanceHealthSummary"):
                continue
            if not balancer.get("name"):
                continue
            yield build_finding(ctx, balancer)
