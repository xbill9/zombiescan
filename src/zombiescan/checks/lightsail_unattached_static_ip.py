"""Lightsail static IPs attached to nothing.

A Lightsail static IP is free while it is attached to an instance and billed
by the hour the moment it is not. Deleting the instance does not release its
static IP -- the address survives, detaches, and starts charging.

The same shape as an unassociated EC2 Elastic IP, and it accumulates the same
way: one address left behind per torn-down instance.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "lightsail-unattached-static-ip"


def build_finding(ctx: ScanContext, static_ip: dict[str, Any]) -> Finding:
    name = static_ip["name"]
    price, approximate = ctx.pricing.lightsail_static_ip_month(ctx.region)

    return Finding(
        check=CHECK_NAME,
        resource_id=name,
        resource_type="lightsail-static-ip",
        region=ctx.region,
        reason=(
            f"Static IP {static_ip.get('ipAddress') or name} is attached to nothing; "
            f"Lightsail bills a static IP only while it is detached"
        ),
        monthly_cost=price,
        remediation=(
            f"aws lightsail release-static-ip --static-ip-name {shlex.quote(name)} "
            f"--region {ctx.region}"
        ),
        approximate_cost=approximate,
        details={
            "ip_address": static_ip.get("ipAddress"),
            "created_at": str(static_ip.get("createdAt") or ""),
            "note": (
                "releasing gives the address up for good; anything with it hard-coded "
                "or in DNS will need updating first"
            ),
        },
    )


@check(CHECK_NAME, "Lightsail static IPs attached to nothing")
def lightsail_unattached_static_ip(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("lightsail")
    for page in client.get_paginator("get_static_ips").paginate():
        for static_ip in page.get("staticIps", []):
            if static_ip.get("isAttached"):
                continue
            if not static_ip.get("name"):
                continue
            yield build_finding(ctx, static_ip)
